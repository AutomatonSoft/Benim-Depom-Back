import hashlib
from decimal import Decimal, InvalidOperation
from time import monotonic
from urllib.parse import quote, urlsplit

import requests
from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from urllib3.util.retry import Retry

from apps.common.marketplace_http import build_marketplace_session
from apps.common.permissions import IsManager
from apps.common.safe_image_download import (
    GeneratedImageDownloadError,
    _validate_public_host,
)
from apps.common.throttles import ManagerMutationThrottleMixin

from .client import EbayAPIError, get_seller_resource
from .drafts import LISTING_SETTINGS_FIELDS, _validate_listing_settings
from .models import EbayOfferPreparation
from .preparation import (
    PreparationConflict,
    PreparationRequestSerializer,
    PreparationSerializer,
    _check_local,
    _check_new_condition,
    _claim,
    _payload_hash,
    _request,
    _save_state,
)
from .preview import build_draft_preview
from .services import get_access_token
from .updating import _read_unpublished


class PublishRequestSerializer(PreparationRequestSerializer):
    """Связывает явное разрешение публикации с просмотренным отпечатком конкретного черновика."""

    confirm_publication = serializers.BooleanField()
    preview_hash = serializers.RegexField(r"^[0-9a-f]{64}$")

    def validate_confirm_publication(self, value):
        if not value:
            raise serializers.ValidationError(
                "Explicit publication confirmation is required."
            )
        return value


class PublishChecksSerializer(serializers.Serializer):
    """Описывает результат проверок приложения без утверждения о полной сертификации товара."""

    ready = serializers.BooleanField()
    product = serializers.IntegerField()
    account = serializers.CharField()
    sku = serializers.CharField()
    offer_id = serializers.CharField()
    environment = serializers.CharField()
    preview_hash = serializers.CharField()
    blocking_reasons = serializers.ListField(child=serializers.CharField())
    manual_checks = serializers.ListField(child=serializers.CharField())


def _get_preparation(product_id, *, account="jv"):
    """Выбирает подготовку заданного сервером аккаунта текущего окружения."""
    return get_object_or_404(
        EbayOfferPreparation.objects.select_related("draft__product"),
        draft__product_id=product_id,
        draft__account=account,
        account=account,
        environment=settings.EBAY_ENVIRONMENT,
        draft__environment=settings.EBAY_ENVIRONMENT,
    )


def _review_hash(preparation, fingerprint):
    """Привязывает подтверждение нового выставления к последнему listingId, исключая старый POST."""
    if not preparation.listing_id:
        return fingerprint
    return hashlib.sha256(
        f"{fingerprint}:{preparation.listing_id}".encode()
    ).hexdigest()


def _differences(actual, expected, path=""):
    """Сверяет управляемые поля; цену сравнивает численно, характеристики — целиком."""
    if path.endswith("pricingSummary.price.value"):
        try:
            return [] if Decimal(str(actual)) == Decimal(str(expected)) else [path]
        except InvalidOperation:
            return [path]
    if isinstance(expected, dict) and not path.endswith(".aspects"):
        if not isinstance(actual, dict):
            return [path]
        return [
            mismatch
            for key, value in expected.items()
            for mismatch in _differences(actual.get(key), value, f"{path}.{key}")
        ]
    return [] if actual == expected else [path]


def _check_images(urls):
    """Проверяет доступность фото только на настроенном публичном FTP media origin, без редиректов."""
    origin = urlsplit(settings.FTP_MEDIA_PUBLIC_BASE_URL)
    deadline = monotonic() + 45
    with build_marketplace_session() as session:
        for adapter in session.adapters.values():
            adapter.max_retries = Retry(total=0)
        for index, url in enumerate(urls, 1):
            try:
                parsed = urlsplit(url)
                if (
                    parsed.scheme != "https"
                    or origin.scheme != "https"
                    or not parsed.hostname
                    or parsed.hostname != origin.hostname
                    or parsed.username
                    or parsed.password
                    or parsed.port not in (None, 443)
                ):
                    raise ValidationError(
                        f"Image {index}: use the configured HTTPS media host."
                    )
                _validate_public_host(parsed.hostname)
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise ValidationError(
                        "Image availability check exceeded 45 seconds."
                    )
                with session.head(
                    url,
                    allow_redirects=False,
                    timeout=(min(5, remaining), min(10, remaining)),
                ) as response:
                    if response.status_code != 200 or not response.headers.get(
                        "Content-Type", ""
                    ).lower().startswith("image/"):
                        raise ValidationError(
                            f"Image {index} is unavailable or is not served as an image."
                        )
            except (
                requests.RequestException,
                GeneratedImageDownloadError,
                ValueError,
            ) as error:
                raise ValidationError(
                    f"Image {index} availability could not be verified."
                ) from error


def _publication_checks(preparation, *, claimed=False):
    """Читает eBay и проверяет текущие данные; операций записи здесь нет."""
    draft = preparation.draft
    preview = build_draft_preview(draft)
    fingerprint = _payload_hash(preview)
    reasons = list(preview["missing_regulatory_fields"])
    if preparation.account != "depotum":
        reasons.append("Publication is enabled only for Depotum.")
    if not preparation.offer_id or not preparation.inventory_confirmed:
        reasons.append("Prepare the Offer first.")
    if preparation.status == "published":
        reasons.append("Offer is already published.")
    elif preparation.status == "uncertain" or (
        preparation.status == "processing" and not claimed
    ):
        reasons.append("Previous request needs reconciliation or is still processing.")
    if preparation.sku != preview["sku"] or not preview["sku"]:
        reasons.append("Product EAN does not match the saved SKU.")
    if preparation.payload_hash != fingerprint:
        reasons.append("Apply the current draft with POST prepare/ first.")
    report = {
        "ready": False,
        "product": draft.product_id,
        "account": preparation.account,
        "sku": preparation.sku,
        "offer_id": preparation.offer_id,
        "environment": preparation.environment,
        "preview_hash": _review_hash(preparation, fingerprint),
        "blocking_reasons": reasons,
        "manual_checks": [
            "actual_stock_location",
            "photo_quality_and_dimensions",
            "product_safety_documents_and_warnings",
        ],
    }
    if reasons:
        return report
    try:
        _check_local(draft, preview)
        _validate_listing_settings(
            account=draft.account,
            values={field: getattr(draft, field) for field in LISTING_SETTINGS_FIELDS},
        )
        token = get_access_token(account=draft.account)
        with build_marketplace_session() as session:
            for adapter in session.adapters.values():
                adapter.max_retries = Retry(total=0)
            privileges = get_seller_resource(
                session=session, access_token=token, path="/sell/account/v1/privilege"
            )
            if privileges.get("sellerRegistrationCompleted") is not True:
                raise ValidationError("Seller registration is not confirmed by eBay.")
            _check_new_condition(session, token, draft.category_id)
            offer = _read_unpublished(session, token, preparation)
            inventory = _request(
                session,
                token,
                "GET",
                f"/sell/inventory/v1/inventory_item/{quote(preparation.sku, safe='')}",
            )
            if (
                not isinstance(inventory, dict)
                or inventory.get("sku") != preparation.sku
                or inventory.get("inventoryItemGroupKeys")
            ):
                raise PreparationConflict(
                    "Inventory Item is missing, mismatched, or grouped."
                )
            mismatches = _differences(
                inventory, preview["inventory_item"]["body"], "inventory_item"
            )
            mismatches += _differences(offer, preview["offer"]["body"], "offer")
            if mismatches:
                raise ValidationError({"remote_mismatches": mismatches})
        _check_images(draft.image_urls)
    except (ValidationError, PreparationConflict) as error:
        report["blocking_reasons"].append(str(error.detail))
        return report
    report["ready"] = True
    return report


@transaction.atomic
def _claim_publication(product_id, fingerprint, *, account="jv"):
    """Повтор после успеха возвращает listingId; остальные операции сериализует блокировка черновика."""
    if account != "depotum":
        raise PreparationConflict("Publication is enabled only for Depotum.")
    preparation = _get_preparation(product_id, account=account)
    type(preparation.draft).objects.select_for_update().get(pk=preparation.draft_id)
    preparation.refresh_from_db()
    if preparation.status == "published" and preparation.listing_id:
        return preparation, False
    draft, _, preparation, _ = _claim(product_id, update=True, account=account)
    if (
        preparation.payload_hash != _payload_hash(build_draft_preview(draft))
        or _review_hash(preparation, preparation.payload_hash) != fingerprint
    ):
        raise PreparationConflict(
            "Draft changed since review. Run POST prepare/, then GET publish/ again."
        )
    return preparation, True


def publish_offer(product_id, fingerprint, *, account="jv"):
    """Публикует один проверенный Offer без автоматического повтора при неопределённом результате."""
    preparation, send = _claim_publication(product_id, fingerprint, account=account)
    if not send:
        return preparation
    writing = False
    try:
        checks = _publication_checks(preparation, claimed=True)
        if not checks["ready"]:
            raise ValidationError({"blocking_reasons": checks["blocking_reasons"]})
        token = get_access_token(account=preparation.account)
        with build_marketplace_session() as session:
            for adapter in session.adapters.values():
                adapter.max_retries = Retry(total=0)
            offer = _read_unpublished(session, token, preparation)
            mismatches = _differences(
                offer, build_draft_preview(preparation.draft)["offer"]["body"], "offer"
            )
            if mismatches:
                raise ValidationError({"remote_mismatches": mismatches})
            _save_state(preparation, status="uncertain")
            writing = True
            result = _request(
                session,
                token,
                "POST",
                f"/sell/inventory/v1/offer/{quote(preparation.offer_id, safe='')}/publish",
            )
            listing_id = result.get("listingId")
            if (
                not isinstance(listing_id, str)
                or not listing_id
                or len(listing_id) > 64
                or listing_id == preparation.listing_id
            ):
                raise EbayAPIError(
                    "Publish returned no valid listingId; reconcile before retrying."
                )
            _save_state(
                preparation, status="published", listing_id=listing_id, last_error=""
            )
        return preparation
    except APIException as error:
        _save_state(
            preparation,
            status="uncertain"
            if writing and not getattr(error, "definite_rejection", False)
            else "failed",
            last_error=str(error.detail)
            if isinstance(error, EbayAPIError)
            else "Publication checks failed; inspect the API response.",
        )
        raise


class EbayDraftPublishView(ManagerMutationThrottleMixin, APIView):
    """Разделяет чтение готовности и явно подтверждённую публикацию реального объявления."""

    permission_classes = (IsManager,)
    account = "jv"

    @extend_schema(responses={200: PublishChecksSerializer}, tags=["eBay"])
    def get(self, request, product_id):
        result = _publication_checks(_get_preparation(product_id, account=self.account))
        response = Response(PublishChecksSerializer(result).data)
        response["Cache-Control"] = "no-store"
        return response

    @extend_schema(
        request=PublishRequestSerializer,
        responses={200: PreparationSerializer},
        tags=["eBay"],
        description="Publishes a live eBay listing. Requires explicit confirmation and the reviewed preview_hash. May incur eBay fees.",
    )
    def post(self, request, product_id):
        serializer = PublishRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        preparation = publish_offer(
            product_id, serializer.validated_data["preview_hash"], account=self.account
        )
        response = Response(PreparationSerializer(preparation).data)
        response["Cache-Control"] = "no-store"
        return response
