from urllib.parse import quote

from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from urllib3.util.retry import Retry

from apps.common.marketplace_http import build_marketplace_session
from apps.common.permissions import IsManager
from apps.common.throttles import ManagerMutationThrottleMixin

from .client import EbayAPIError
from .drafts import LISTING_SETTINGS_FIELDS, _validate_listing_settings
from .models import EbayDraft
from .preparation import (
    PreparationConflict,
    PreparationRequestSerializer,
    PreparationSerializer,
    _check_local,
    _check_new_condition,
    _payload_hash,
    _request,
    _save_state,
)
from .preview import build_draft_preview
from .publishing import _check_images, _get_preparation
from .regulatory import missing_regulatory_fields
from .services import get_access_token
from .updating import _update_bodies


class ListingUpdateSerializer(PreparationRequestSerializer):
    """Требует явного подтверждения изменения активного объявления и склада."""

    confirm_update = serializers.BooleanField()

    def validate_confirm_update(self, value):
        if not value:
            raise serializers.ValidationError("Confirm updating the live listing.")
        return value


class ListingWithdrawSerializer(serializers.Serializer):
    """Подтверждает снятие с продажи без удаления Offer, товара и фотографий."""

    confirm_withdrawal = serializers.BooleanField()

    def validate_confirm_withdrawal(self, value):
        if not value:
            raise serializers.ValidationError("Confirm ending the live listing.")
        return value


class ListingSnapshotSerializer(serializers.Serializer):
    """Возвращает полные Inventory/Offer, текущие статусы eBay и локальное состояние."""

    product = serializers.IntegerField()
    account = serializers.CharField()
    environment = serializers.CharField()
    checked_at = serializers.DateTimeField()
    remote_status = serializers.CharField(allow_null=True)
    listing_status = serializers.CharField(allow_null=True)
    remote_listing_id = serializers.CharField(allow_blank=True)
    preparation = PreparationSerializer()
    inventory_item = serializers.JSONField(allow_null=True)
    offer = serializers.JSONField(allow_null=True)


def _read_resources(session, token, preparation):
    """Читает два ресурса и проверяет принадлежность сохранённым SKU/Offer и площадке."""
    if not preparation.offer_id:
        raise PreparationConflict("Prepare the Offer first.")
    inventory = _request(
        session,
        token,
        "GET",
        f"/sell/inventory/v1/inventory_item/{quote(preparation.sku, safe='')}",
    )
    offer = _request(
        session,
        token,
        "GET",
        f"/sell/inventory/v1/offer/{quote(preparation.offer_id, safe='')}",
    )
    if inventory is not None and inventory.get("sku") != preparation.sku:
        raise EbayAPIError("Inventory SKU does not match the saved preparation.")
    if offer is not None and (
        offer.get("offerId") != preparation.offer_id
        or offer.get("sku") != preparation.sku
        or offer.get("marketplaceId") != "EBAY_DE"
        or offer.get("format") != "FIXED_PRICE"
    ):
        raise EbayAPIError("Offer does not match the saved preparation.")
    if offer is not None and not isinstance(offer.get("listing", {}), dict):
        raise EbayAPIError("eBay returned invalid listing details.")
    return inventory, offer


def _check_active(offer, preparation):
    """Запрещает изменение другого, завершённого или запланированного объявления."""
    listing = offer.get("listing", {}) if offer else {}
    if (
        not offer
        or offer.get("status") != "PUBLISHED"
        or not isinstance(listing, dict)
        or listing.get("listingId") != preparation.listing_id
        or listing.get("listingStatus") not in (None, "ACTIVE")
        or offer.get("listingStartDate")
    ):
        raise PreparationConflict(
            "Saved listing is not the current active eBay listing."
        )


@transaction.atomic
def _claim_listing(product_id, *, withdraw):
    """Блокирует черновик на время изменения; неопределённые результаты не повторяет."""
    preparation = _get_preparation(product_id, account="depotum")
    draft = (
        EbayDraft.objects.select_for_update()
        .select_related("product")
        .get(pk=preparation.draft_id)
    )
    preparation.refresh_from_db()
    preparation.draft = draft
    if preparation.status in {"processing", "uncertain"}:
        raise PreparationConflict(
            "Previous request requires reconciliation; no repeat was sent."
        )
    if withdraw and preparation.status == "withdrawn":
        return preparation, False
    if not preparation.listing_id or preparation.status != "published":
        raise PreparationConflict("This operation requires a published listing.")
    _save_state(preparation, status="processing", last_error="")
    return preparation, True


def change_listing(product_id, *, withdraw=False):
    """Обновляет активный листинг или снимает его; частичные и неизвестные результаты блокирует."""
    preparation, send = _claim_listing(product_id, withdraw=withdraw)
    if not send:
        return preparation
    writing = False
    inventory_updated = False
    try:
        draft = preparation.draft
        preview = None
        if not withdraw:
            preview = build_draft_preview(draft)
            if preview["sku"] != preparation.sku:
                raise PreparationConflict("Product EAN changed after publication.")
            _check_local(draft, preview)
            missing = missing_regulatory_fields(draft.regulatory)
            if missing:
                raise ValidationError({"missing_regulatory_fields": missing})
            _validate_listing_settings(
                account="depotum",
                values={
                    field: getattr(draft, field) for field in LISTING_SETTINGS_FIELDS
                },
            )
            _check_images(draft.image_urls)
        token = get_access_token(account="depotum")
        with build_marketplace_session() as session:
            for adapter in session.adapters.values():
                adapter.max_retries = Retry(total=0)
            # Inventory Item общий для SKU: нельзя менять его при наличии других Offer.
            offers = _request(
                session,
                token,
                "GET",
                "/sell/inventory/v1/offer",
                params={"sku": preparation.sku},
            )
            if (
                not offers
                or not isinstance(offers.get("offers"), list)
                or len(offers["offers"]) != 1
                or offers.get("next")
                or str(offers.get("total", 1)) != "1"
                or not isinstance(offers["offers"][0], dict)
                or offers["offers"][0].get("offerId") != preparation.offer_id
            ):
                raise PreparationConflict("SKU must have exactly one saved Offer.")
            inventory, offer = _read_resources(session, token, preparation)
            _check_active(offer, preparation)
            if inventory and inventory.get("inventoryItemGroupKeys"):
                raise PreparationConflict("Variation listings are not supported here.")
            if withdraw:
                _save_state(preparation, status="uncertain")
                writing = True
                result = _request(
                    session,
                    token,
                    "POST",
                    f"/sell/inventory/v1/offer/{quote(preparation.offer_id, safe='')}/withdraw",
                )
                if result.get("listingId") != preparation.listing_id:
                    raise EbayAPIError(
                        "Withdrawal returned no matching listingId; reconcile before retrying."
                    )
                _save_state(preparation, status="withdrawn", last_error="")
            else:
                _check_new_condition(session, token, draft.category_id)
                inventory_body, offer_body = _update_bodies(inventory, offer, preview)
                _save_state(preparation, status="uncertain")
                writing = True
                _request(
                    session,
                    token,
                    "PUT",
                    f"/sell/inventory/v1/inventory_item/{quote(preparation.sku, safe='')}",
                    body=inventory_body,
                )
                writing = False
                inventory_updated = True
                _save_state(preparation, status="processing")
                _, offer = _read_resources(session, token, preparation)
                _check_active(offer, preparation)
                _, offer_body = _update_bodies(inventory, offer, preview)
                _save_state(preparation, status="uncertain")
                writing = True
                _request(
                    session,
                    token,
                    "PUT",
                    f"/sell/inventory/v1/offer/{quote(preparation.offer_id, safe='')}",
                    body=offer_body,
                )
                _save_state(
                    preparation,
                    status="published",
                    payload_hash=_payload_hash(preview),
                    last_error="",
                )
        return preparation
    except APIException as error:
        uncertain = writing and not getattr(error, "definite_rejection", False)
        detail = (
            str(error.detail)
            if isinstance(error, EbayAPIError)
            else "Listing operation rejected; inspect the API response."
        )
        if inventory_updated:
            detail = "Inventory Item updated; Offer update did not complete. " + detail
        _save_state(
            preparation,
            status="uncertain" if uncertain or inventory_updated else "published",
            last_error=detail,
        )
        raise


class EbayListingView(ManagerMutationThrottleMixin, APIView):
    """Обслуживает один листинг Depotum: чтение eBay, обновление и снятие с продажи."""

    permission_classes = (IsManager,)

    @extend_schema(responses={200: ListingSnapshotSerializer}, tags=["eBay"])
    def get(self, request, product_id):
        """Возвращает текущие данные eBay без изменения локальных статусов."""
        preparation = _get_preparation(product_id, account="depotum")
        token = get_access_token(account="depotum")
        with build_marketplace_session() as session:
            for adapter in session.adapters.values():
                adapter.max_retries = Retry(total=0)
            inventory, offer = _read_resources(session, token, preparation)
        listing = (offer or {}).get("listing") or {}
        result = {
            "product": product_id,
            "account": "depotum",
            "environment": preparation.environment,
            "checked_at": timezone.now(),
            "remote_status": (offer or {}).get("status"),
            "listing_status": listing.get("listingStatus"),
            "remote_listing_id": listing.get("listingId", ""),
            "preparation": preparation,
            "inventory_item": inventory,
            "offer": offer,
        }
        response = Response(ListingSnapshotSerializer(result).data)
        response["Cache-Control"] = "no-store"
        return response

    @extend_schema(
        request=ListingUpdateSerializer,
        responses={200: PreparationSerializer},
        tags=["eBay"],
        description="Updates the live listing from the saved Depotum draft, including HTML and 19% VAT. Does not create or republish listings.",
    )
    def put(self, request, product_id):
        """Применяет сохранённый черновик к активному объявлению после подтверждения."""
        serializer = ListingUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        response = Response(PreparationSerializer(change_listing(product_id)).data)
        response["Cache-Control"] = "no-store"
        return response

    @extend_schema(
        request=ListingWithdrawSerializer,
        responses={200: PreparationSerializer},
        tags=["eBay"],
        description="Ends the live listing using withdrawOffer. Keeps Inventory Item, Offer, local Product, EAN and photos. Does not permanently delete resources.",
    )
    def delete(self, request, product_id):
        """Снимает объявление с продажи, сохраняя товар и идентификаторы для сверки."""
        serializer = ListingWithdrawSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        response = Response(
            PreparationSerializer(change_listing(product_id, withdraw=True)).data
        )
        response["Cache-Control"] = "no-store"
        return response
