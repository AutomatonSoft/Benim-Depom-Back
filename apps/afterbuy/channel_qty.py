from __future__ import annotations

from typing import Any

from django.conf import settings

from apps.marketplace.hood.client import HoodClient
from apps.marketplace.hood.client import path_with_ean as hood_path_with_ean
from apps.orchestrator.client import MarketplaceClient
from apps.orchestrator.tasks import path_with_ean

from .models import AfterbuyOrder
from .parser import TRACKED_MARKETPLACES

TRACKED_CHANNEL_ORDER = (
    AfterbuyOrder.Marketplace.OTTO,
    AfterbuyOrder.Marketplace.HOOD,
    AfterbuyOrder.Marketplace.KAUFLAND,
)


def _coerce_qty(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float) and value.is_integer():
        return max(0, int(value))
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def parse_otto_quantity(payload: Any) -> int | None:
    if not isinstance(payload, dict):
        return None
    for key in ("quantity", "qty", "stock"):
        qty = _coerce_qty(payload.get(key))
        if qty is not None:
            return qty
    nested = payload.get("response")
    if isinstance(nested, dict):
        return parse_otto_quantity(nested)
    return None


def parse_kaufland_quantity(payload: Any) -> int | None:
    data = payload
    if isinstance(payload, dict) and isinstance(payload.get("response_data"), dict):
        data = payload["response_data"]
    if not isinstance(data, dict):
        return None
    units = data.get("units")
    if isinstance(units, list) and units:
        total = 0
        found = False
        for unit in units:
            if not isinstance(unit, dict):
                continue
            qty = _coerce_qty(unit.get("amount"))
            if qty is None:
                continue
            total += qty
            found = True
        if found:
            return total
    return _coerce_qty(data.get("quantity"))


def parse_hood_quantity(payload: Any) -> int | None:
    if isinstance(payload, list) and payload:
        payload = payload[0]
    if not isinstance(payload, dict):
        return None
    for key in ("quantity", "qty", "stock", "itemQuantity", "Menge"):
        qty = _coerce_qty(payload.get(key))
        if qty is not None:
            return qty
    for nested_key in ("item", "data", "result", "response"):
        nested = payload.get(nested_key)
        if nested is None or nested is payload:
            continue
        qty = parse_hood_quantity(nested)
        if qty is not None:
            return qty
    return None


def product_listing_ean(*, product, account: str) -> str:
    if account == AfterbuyOrder.Account.JV:
        return (product.ean_jv or "").strip()
    return (product.ean_xl or "").strip()


def fetch_selling_channel_quantity(
    *,
    marketplace: str,
    ean: str,
    account: str,
    request_id: str,
) -> int | None:
    if marketplace not in TRACKED_MARKETPLACES:
        return None
    try:
        if marketplace == AfterbuyOrder.Marketplace.OTTO:
            client = MarketplaceClient(request_id=request_id)
            result = client.request(
                settings.OTTO_API_BASE_URL,
                "GET",
                path_with_ean(settings.OTTO_API_QUANTITY_ENDPOINT, ean),
                params={"controller": account},
            )
            if not result.get("ok"):
                return None
            return parse_otto_quantity(result.get("details"))

        if marketplace == AfterbuyOrder.Marketplace.KAUFLAND:
            client = MarketplaceClient(request_id=request_id)
            result = client.request(
                settings.KAUFLAND_API_BASE_URL,
                "GET",
                path_with_ean(settings.KAUFLAND_API_GET_BY_EAN_ENDPOINT, ean),
                params={"ean": ean, "controller": account},
            )
            if not result.get("ok"):
                return None
            return parse_kaufland_quantity(result.get("details"))

        if marketplace == AfterbuyOrder.Marketplace.HOOD:
            client = HoodClient(request_id=request_id)
            result = client.request(
                "GET",
                hood_path_with_ean(settings.HOOD_API_GET_ENDPOINT, ean),
                account=account,
            )
            if not result.get("ok"):
                return None
            return parse_hood_quantity(result.get("details"))
    except Exception:
        return None
    return None
