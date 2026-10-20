from copy import deepcopy
from urllib.parse import quote

from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import APIView
from urllib3.util.retry import Retry

from apps.common.marketplace_http import build_marketplace_session
from apps.common.permissions import IsManager
from apps.common.throttles import ManagerMutationThrottleMixin

from .client import EbayAPIError, api_base_url
from .drafts import LISTING_SETTINGS_FIELDS, _validate_listing_settings
from .preparation import (
    PreparationConflict,
    PreparationRequestSerializer,
    PreparationSerializer,
    _check_new_condition,
    _claim,
    _payload_hash,
    _request,
    _save_state,
)
from .services import get_access_token

OFFER_WRITABLE_FIELDS = frozenset(
    {
        "availableQuantity",
        "categoryId",
        "charity",
        "extendedProducerResponsibility",
        "hideBuyerDetails",
        "includeCatalogProductDetails",
        "listingDescription",
        "listingDuration",
        "listingPolicies",
        "listingStartDate",
        "lotSize",
        "merchantLocationKey",
        "pricingSummary",
        "quantityLimitPerBuyer",
        "regulatory",
        "secondaryCategoryId",
        "storeCategoryNames",
        "tax",
    }
)
OFFER_READONLY_FIELDS = frozenset(
    {"sku", "marketplaceId", "format", "offerId", "status", "listing"}
)
INVENTORY_WRITABLE_FIELDS = frozenset(
    {
        "availability",
        "condition",
        "conditionDescription",
        "conditionDescriptors",
        "packageWeightAndSize",
        "product",
    }
)


def _read_unpublished(session, token, preparation):
    """Проверяет весь SKU и сохранённый offerId; чужие, опубликованные и неполные ответы запрещены."""
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
    ):
        raise PreparationConflict("SKU must have exactly one known unpublished Offer.")
    offer = _request(
        session,
        token,
        "GET",
        f"/sell/inventory/v1/offer/{quote(preparation.offer_id, safe='')}",
    )
    for item in (offers["offers"][0], offer):
        if (
            not isinstance(item, dict)
            or item.get("offerId") != preparation.offer_id
            or item.get("sku") != preparation.sku
            or item.get("marketplaceId") != "EBAY_DE"
            or item.get("format") != "FIXED_PRICE"
            or item.get("status") != "UNPUBLISHED"
            or item.get("listing")
            or item.get("listingStartDate")
        ):
            raise PreparationConflict(
                "Offer is missing, published, scheduled, or does not match this draft."
            )
    return offer


def _merge_fields(existing, changes):
    """Сохраняет дополнительные поля eBay; массивы и набор характеристик заменяет целиком."""
    result = deepcopy(existing)
    for key, value in changes.items():
        if (
            key != "aspects"
            and isinstance(value, dict)
            and isinstance(result.get(key), dict)
        ):
            result[key] = _merge_fields(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _update_bodies(inventory, offer, preview):
    """Собирает полные PUT без read-only полей; неизвестные поля не удаляет молча."""
    if not isinstance(inventory, dict) or inventory.get("inventoryItemGroupKeys"):
        raise PreparationConflict(
            "Inventory Item is missing or belongs to a variation group."
        )
    if (
        inventory.keys()
        - INVENTORY_WRITABLE_FIELDS
        - {"sku", "locale", "inventoryItemGroupKeys"}
    ):
        raise PreparationConflict(
            "Inventory Item contains unsupported fields; review before replacing."
        )
    if offer.keys() - OFFER_WRITABLE_FIELDS - OFFER_READONLY_FIELDS:
        raise PreparationConflict(
            "Offer contains unsupported fields; review before replacing."
        )
    inventory_body = _merge_fields(
        {
            key: value
            for key, value in inventory.items()
            if key in INVENTORY_WRITABLE_FIELDS
        },
        preview["inventory_item"]["body"],
    )
    offer_body = _merge_fields(
        {key: value for key, value in offer.items() if key in OFFER_WRITABLE_FIELDS},
        {
            key: value
            for key, value in preview["offer"]["body"].items()
            if key in OFFER_WRITABLE_FIELDS
        },
    )
    return inventory_body, offer_body


def update_unpublished_offer(product_id, *, account="jv"):
    """Обновляет только ранее созданный Offer; частичный результат не откатывает и не публикует."""
    api_base_url()
    draft, preview, preparation, _ = _claim(product_id, update=True, account=account)
    fingerprint = _payload_hash(preview)
    writing = False
    inventory_updated = False
    try:
        _validate_listing_settings(
            account=draft.account,
            values={field: getattr(draft, field) for field in LISTING_SETTINGS_FIELDS},
        )
        token = get_access_token(account=draft.account)
        with build_marketplace_session() as session:
            for adapter in session.adapters.values():
                adapter.max_retries = Retry(total=0)
            _check_new_condition(session, token, draft.category_id)
            offer = _read_unpublished(session, token, preparation)
            inventory_path = (
                f"/sell/inventory/v1/inventory_item/{quote(preparation.sku, safe='')}"
            )
            inventory = _request(session, token, "GET", inventory_path)
            if inventory is not None and inventory.get("sku") != preparation.sku:
                raise PreparationConflict(
                    "Inventory Item SKU does not match this draft."
                )
            inventory_body, offer_body = _update_bodies(inventory, offer, preview)
            _save_state(preparation, status="uncertain")
            writing = True
            _request(session, token, "PUT", inventory_path, body=inventory_body)
            writing = False
            inventory_updated = True
            _save_state(preparation, status="processing")
            # Повторно проверяем состояние после первого PUT, до изменения Offer.
            offer = _read_unpublished(session, token, preparation)
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
                preparation, status="prepared", payload_hash=fingerprint, last_error=""
            )
        return preparation
    except APIException as error:
        detail = (
            str(error.detail)
            if isinstance(error, EbayAPIError)
            else "Update checks failed; inspect the API response."
        )
        if inventory_updated:
            detail = (
                "Inventory Item was updated; Offer update did not complete. " + detail
            )
        _save_state(
            preparation,
            status="uncertain"
            if writing and not getattr(error, "definite_rejection", False)
            else "failed",
            last_error=detail,
        )
        raise


class EbayDraftUpdateView(ManagerMutationThrottleMixin, APIView):
    """Применяет сохранённый черновик к неопубликованному Offer без создания нового."""

    permission_classes = (IsManager,)
    account = "jv"

    @extend_schema(
        request=PreparationRequestSerializer,
        responses={200: PreparationSerializer},
        tags=["eBay"],
        description="Updates the existing unpublished Offer and Inventory Item. Does not publish or create offers.",
    )
    def post(self, request, product_id):
        serializer = PreparationRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        preparation = update_unpublished_offer(product_id, account=self.account)
        response = Response(PreparationSerializer(preparation).data)
        response["Cache-Control"] = "no-store"
        return response
