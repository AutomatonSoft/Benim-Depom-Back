import hashlib
import json
import re
from urllib.parse import quote

import requests
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from urllib3.util.retry import Retry

from apps.common.marketplace_http import build_marketplace_session
from apps.common.permissions import IsManager
from apps.common.throttles import ManagerMutationThrottleMixin
from apps.products.models import Product

from .catalog import get_category_aspects
from .client import EbayAPIError, api_base_url, get_seller_resource
from .drafts import (
    LISTING_SETTINGS_FIELDS,
    _validate_aspects,
    _validate_listing_settings,
)
from .models import EbayDraft, EbayOfferPreparation
from .preview import build_draft_preview
from .regulatory import EbayRegulatorySerializer
from .services import get_access_token


class PreparationConflict(APIException):
    status_code = 409
    default_detail = "Review the existing eBay preparation before retrying."


class PreparationRequestSerializer(serializers.Serializer):
    """Требует подтверждения фактического наличия товара на выбранном складе."""

    confirm_stock_location = serializers.BooleanField()

    def validate_confirm_stock_location(self, value):
        if not value:
            raise serializers.ValidationError("Confirm stock at merchant_location_key.")
        return value


class PreparationSerializer(serializers.ModelSerializer):
    """Показывает сохранённый результат без токенов и персональных данных GPSR."""

    publication_performed = serializers.BooleanField(read_only=True, default=False)

    class Meta:
        model = EbayOfferPreparation
        fields = (
            "sku",
            "environment",
            "account",
            "status",
            "inventory_confirmed",
            "offer_id",
            "last_error",
            "updated_at",
            "publication_performed",
        )
        read_only_fields = fields


def _error_messages(errors, token, body):
    """Оставляет диагностику eBay, скрывая токен и отражённые GPSR-контакты."""
    private_values = [token] if token else []
    pending = [(body or {}).get("regulatory", {})]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, str) and len(value) > 2:
            private_values.append(value)
    messages = []
    for error in errors[:3]:
        if not isinstance(error, dict):
            continue
        for field in ("message", "longMessage"):
            message = error.get(field)
            if not isinstance(message, str):
                continue
            for value in sorted(private_values, key=len, reverse=True):
                message = message.replace(value, "[REDACTED]")
            message = re.sub(r"https?://\S+|[\w.+-]+@[\w.-]+", "[REDACTED]", message)
            message = " ".join(message.split())[:1000]
            if message and message not in messages:
                messages.append(message)
    return " ".join(messages)


def _request(session, token, method, path, *, body=None, params=None):
    """Выполняет один запрос без редиректов и повторов; не сохраняет сырой ответ eBay."""
    try:
        response = session.request(
            method,
            f"{api_base_url()}{path}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Content-Language": "de-DE",
            },
            json=body,
            params=params,
            timeout=(5, 15),
            allow_redirects=False,
        )
    except requests.RequestException as error:
        raise EbayAPIError(
            "eBay connection failed; check preparation status before retrying."
        ) from error
    if method == "GET" and response.status_code == 404:
        return None
    if response.status_code not in {200, 201, 204}:
        codes = []
        messages = ""
        try:
            payload = response.json()
            errors = payload.get("errors", []) if isinstance(payload, dict) else []
            if isinstance(errors, list):
                messages = _error_messages(errors, token, body)
                codes = [
                    str(item["errorId"])
                    for item in errors[:3]
                    if isinstance(item, dict) and type(item.get("errorId")) is int
                ]
        except ValueError:
            pass
        error = EbayAPIError(
            f"eBay HTTP {response.status_code}; errorIds={','.join(codes) or 'unknown'}. "
            f"{method} {path}. {messages}".rstrip()
        )
        # Только явный отказ позволяет безопасно повторить операцию записи.
        error.definite_rejection = response.status_code in {400, 401, 403, 404, 422}
        raise error
    if response.status_code == 204:
        return {}
    try:
        payload = response.json()
    except ValueError as error:
        raise EbayAPIError(
            "Invalid eBay response; reconcile before retrying."
        ) from error
    if not isinstance(payload, dict):
        raise EbayAPIError("Invalid eBay response; reconcile before retrying.")
    return payload


def _save_state(preparation, **values):
    """Сохраняет этап отдельно от сети: таймаут не откатывает сведения об отправке."""
    for name, value in values.items():
        setattr(preparation, name, value)
    preparation.save(update_fields=[*values, "updated_at"])


def _check_local(draft, preview):
    """Проверяет товар, остаток, EAN и введённые контакты перед созданием черновика Offer."""
    if not preview["draft_complete"]:
        raise ValidationError({"missing_requirements": preview["missing_requirements"]})
    regulatory = EbayRegulatorySerializer(data=draft.regulatory)
    regulatory.is_valid(raise_exception=True)
    if (
        draft.product.status != Product.Status.APPROVED
        or not draft.product.is_available
    ):
        raise ValidationError("Product must be approved and available.")
    if len(draft.description) > 4000:
        raise ValidationError({"description": "eBay allows at most 4000 characters."})
    stock = draft.product.variants.aggregate(total=Sum("quantity"))["total"] or 0
    if not draft.quantity or draft.quantity > stock:
        raise ValidationError(
            {"quantity": "Choose a positive quantity within product stock."}
        )
    schema = get_category_aspects(category_id=draft.category_id)
    _validate_aspects(draft.aspects, schema)
    missing = [
        name for name in schema["required_aspects"] if not draft.aspects.get(name)
    ]
    if missing:
        raise ValidationError({"aspects": missing})


@transaction.atomic
def _claim(product_id):
    """Блокирует конкурентную отправку и закрепляет существующий EAN за одним черновиком."""
    draft = get_object_or_404(
        EbayDraft.objects.select_for_update().select_related("product"),
        product_id=product_id,
        environment=settings.EBAY_ENVIRONMENT,
        account="jv",
    )
    preview = build_draft_preview(draft)
    fingerprint = hashlib.sha256(
        json.dumps(
            [preview["inventory_item"]["body"], preview["offer"]["body"]],
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    preparation = EbayOfferPreparation.objects.filter(draft=draft).first()
    if preparation:
        if preparation.status in {"processing", "uncertain"}:
            raise PreparationConflict(
                "Previous request requires reconciliation; no repeat was sent."
            )
        if preparation.sku != preview["sku"]:
            raise PreparationConflict("Product EAN changed after preparation.")
        if preparation.offer_id:
            if preparation.payload_hash != fingerprint:
                raise PreparationConflict(
                    "Draft changed; updating an existing Offer requires a separate step."
                )
            return draft, preview, preparation, False
    _check_local(draft, preview)
    if preparation:
        _save_state(
            preparation, status="processing", payload_hash=fingerprint, last_error=""
        )
    else:
        try:
            with transaction.atomic():
                preparation = EbayOfferPreparation.objects.create(
                    draft=draft,
                    environment=draft.environment,
                    account=draft.account,
                    sku=preview["sku"],
                    payload_hash=fingerprint,
                    status="processing",
                )
        except IntegrityError as error:
            raise PreparationConflict(
                "This SKU is already assigned to another preparation."
            ) from error
    return draft, preview, preparation, True


def prepare_offer(product_id):
    """Создаёт только Inventory Item и неопубликованный Offer; publish/delete отсутствуют."""
    api_base_url()
    draft, preview, preparation, send = _claim(product_id)
    if not send:
        return preparation
    writing = False
    try:
        _validate_listing_settings(
            account="jv",
            values={field: getattr(draft, field) for field in LISTING_SETTINGS_FIELDS},
        )
        token = get_access_token(account="jv")
        with build_marketplace_session() as session:
            for adapter in session.adapters.values():
                adapter.max_retries = Retry(total=0)
            conditions = get_seller_resource(
                session=session,
                access_token=token,
                path="/sell/metadata/v1/marketplace/EBAY_DE/get_item_condition_policies",
                params={"filter": f"categoryIds:{{{draft.category_id}}}"},
            )
            policies = conditions.get("itemConditionPolicies")
            if not isinstance(policies, list) or any(
                not isinstance(policy, dict)
                or not isinstance(policy.get("itemConditions"), list)
                or any(not isinstance(item, dict) for item in policy["itemConditions"])
                for policy in policies
            ):
                raise EbayAPIError("eBay returned invalid condition metadata.")
            if not any(
                policy.get("categoryId") == draft.category_id
                and any(
                    str(item.get("conditionId")) == "1000"
                    for item in policy.get("itemConditions", [])
                )
                for policy in policies
            ):
                raise ValidationError("Category does not confirm condition NEW.")
            inventory_path = (
                f"/sell/inventory/v1/inventory_item/{quote(preparation.sku, safe='')}"
            )
            offers = _request(
                session,
                token,
                "GET",
                "/sell/inventory/v1/offer",
                params={"sku": preparation.sku},
            )
            # Любой Offer, включая другой marketplace и вариант формата, запрещает замену Inventory Item.
            if offers is not None and (
                not isinstance(offers.get("offers"), list)
                or offers["offers"]
                or offers.get("next")
                or offers.get("total", 0)
            ):
                raise PreparationConflict(
                    "SKU already has eBay offers; no resources were changed."
                )
            existing = _request(session, token, "GET", inventory_path)
            if existing is not None and not preparation.inventory_confirmed:
                raise PreparationConflict(
                    "SKU already exists in eBay and is not owned by this preparation."
                )
            _save_state(preparation, status="uncertain")
            writing = True
            _request(
                session,
                token,
                "PUT",
                inventory_path,
                body=preview["inventory_item"]["body"],
            )
            writing = False
            _save_state(preparation, status="processing", inventory_confirmed=True)
            _save_state(preparation, status="uncertain")
            writing = True
            offer = _request(
                session,
                token,
                "POST",
                "/sell/inventory/v1/offer",
                body=preview["offer"]["body"],
            )
            offer_id = offer.get("offerId")
            if not isinstance(offer_id, str) or not offer_id or len(offer_id) > 64:
                raise EbayAPIError(
                    "eBay did not return a valid offerId; reconcile before retrying."
                )
            _save_state(
                preparation, status="prepared", offer_id=offer_id, last_error=""
            )
        return preparation
    except (APIException, requests.RequestException) as error:
        uncertain = writing and not getattr(error, "definite_rejection", False)
        # Не сохраняем тело ответа, URL, токены или персональные GPSR-контакты.
        detail = (
            str(error.detail)
            if isinstance(error, EbayAPIError)
            else "Preparation checks failed; inspect the API response."
        )
        _save_state(
            preparation,
            status="uncertain" if uncertain else "failed",
            last_error=detail,
        )
        raise


class EbayDraftPreparationView(ManagerMutationThrottleMixin, APIView):
    """Позволяет менеджеру отправить черновик без публикации и прочитать результат."""

    permission_classes = (IsManager,)

    @extend_schema(responses={200: PreparationSerializer}, tags=["eBay"])
    def get(self, request, product_id):
        preparation = get_object_or_404(
            EbayOfferPreparation,
            draft__product_id=product_id,
            environment=settings.EBAY_ENVIRONMENT,
            account="jv",
        )
        response = Response(PreparationSerializer(preparation).data)
        response["Cache-Control"] = "no-store"
        return response

    @extend_schema(
        request=PreparationRequestSerializer,
        responses={200: PreparationSerializer},
        tags=["eBay"],
        description="Creates an Inventory Item and an unpublished Offer. Does not publish. Confirm actual stock at the selected location.",
    )
    def post(self, request, product_id):
        serializer = PreparationRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        preparation = prepare_offer(product_id)
        response = Response(PreparationSerializer(preparation).data)
        response["Cache-Control"] = "no-store"
        return response
