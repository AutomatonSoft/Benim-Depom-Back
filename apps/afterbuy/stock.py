from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.utils import timezone

from apps.marketplace.hood.client import HoodClient
from apps.marketplace.hood.client import path_with_ean as hood_path_with_ean
from apps.orchestrator.client import MarketplaceClient
from apps.orchestrator.tasks import path_with_ean

from .models import AfterbuyChannelStock, AfterbuyOrder, AfterbuyOrderItem
from .parser import TRACKED_MARKETPLACES


def _coerce_qty(value: Any) -> int | None:
    """Превращает ответ API в целое количество. bool не считаем числом."""
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
    """OTTO: {\"sku\": \"...\", \"quantity\": 20}."""
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
    """Kaufland: response_data.units[].amount."""
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
    """Hood GET by EAN: ищем quantity/qty/stock в корне или во вложенном item."""
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
    """
    Читает остаток только на площадке продажи. Ничего не записывает.

    Если API недоступен или формат чужой — None, уведомление уйдёт
    с количеством из нашего приложения.
    """
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


TRACKED_CHANNEL_ORDER = (
    AfterbuyOrder.Marketplace.OTTO,
    AfterbuyOrder.Marketplace.HOOD,
    AfterbuyOrder.Marketplace.KAUFLAND,
)


@dataclass(frozen=True, slots=True)
class SaleStockSnapshot:
    qty_before: int
    qty_after: int
    from_marketplace: bool


def sibling_marketplaces(sold_on: str) -> tuple[str, ...]:
    """Площадки с общим складом, кроме той, где уже списали продажу."""
    return tuple(channel for channel in TRACKED_CHANNEL_ORDER if channel != sold_on)


def write_channel_quantity(
    *,
    marketplace: str,
    ean: str,
    account: str,
    quantity: int,
    request_id: str,
) -> bool:
    """
    Ставит абсолютный остаток на канал. Не минус, а новое число.

    Канал продажи сюда передавать нельзя — он уже сам изменился.
    """
    try:
        if marketplace == AfterbuyOrder.Marketplace.OTTO:
            client = MarketplaceClient(request_id=request_id)
            method = (settings.OTTO_API_QUANTITY_WRITE_METHOD or "POST").upper()
            result = client.request(
                settings.OTTO_API_BASE_URL,
                method,
                path_with_ean(settings.OTTO_API_QUANTITY_ENDPOINT, ean),
                params={"controller": account},
                payload={
                    "sku": ean,
                    "ean": ean,
                    "quantity": quantity,
                    "controller": account,
                },
            )
            return bool(result.get("ok"))

        if marketplace == AfterbuyOrder.Marketplace.HOOD:
            client = HoodClient(request_id=request_id)
            result = client.request(
                "PATCH",
                hood_path_with_ean(settings.HOOD_API_PATCH_ENDPOINT, ean),
                account=account,
                payload={"quantity": quantity},
            )
            return bool(result.get("ok"))

        if marketplace == AfterbuyOrder.Marketplace.KAUFLAND:
            client = MarketplaceClient(request_id=request_id)
            result = client.request(
                settings.KAUFLAND_API_BASE_URL,
                "PATCH",
                settings.KAUFLAND_API_UPDATE_ENDPOINT,
                params={"controller": account},
                payload={
                    "ean": ean,
                    "controller": account,
                    "storefront": settings.KAUFLAND_STATUS_STOREFRONT or "de",
                    "amount": quantity,
                },
            )
            return bool(result.get("ok"))
    except Exception:
        return False
    return False


def align_sibling_channel_quantities(
    *,
    sold_on: str,
    ean: str,
    account: str,
    quantity: int,
    request_id: str,
) -> dict[str, bool]:
    """После продажи дописывает тот же остаток на Hood/OTTO/Kaufland, кроме канала покупки."""
    results: dict[str, bool] = {}
    for marketplace in sibling_marketplaces(sold_on):
        results[marketplace] = write_channel_quantity(
            marketplace=marketplace,
            ean=ean,
            account=account,
            quantity=quantity,
            request_id=f"{request_id}-{marketplace}",
        )
    return results


def apply_canonical_qty_to_product(product, quantity: int) -> None:
    """
    Карточка у нас одно число на варианте. Несколько вариантов без
    цвета в заказе не трогаем. 0 — товар распродан.
    """
    if quantity < 0:
        return
    variants = list(product.variants.all())
    if len(variants) != 1:
        return
    variant = variants[0]
    if variant.quantity == quantity:
        return
    variant.quantity = quantity
    variant.save(update_fields=["quantity"])


def record_channel_quantities(
    *, product, account: str, quantities: dict[str, int | None]
) -> None:
    """Пишет снимок по каналам. None не затирает прошлый остаток."""
    now = timezone.now()
    for marketplace, quantity in quantities.items():
        if quantity is None or marketplace not in TRACKED_MARKETPLACES:
            continue
        AfterbuyChannelStock.objects.update_or_create(
            product=product,
            account=account,
            marketplace=marketplace,
            defaults={"quantity": quantity, "observed_at": now},
        )


def app_stock_qty(order_item: AfterbuyOrderItem) -> int:
    """Сумма количества вариантов в нашем приложении."""
    product = order_item.matched_product
    if product is None:
        return 0
    return sum(variant.quantity for variant in product.variants.all())


def read_quantities_for_sale(order_item: AfterbuyOrderItem) -> SaleStockSnapshot:
    """
    qty_after — живой остаток на площадке продажи.
    qty_before — after + купленное, если площадка ответила.
    """
    app_qty = app_stock_qty(order_item)
    product = order_item.matched_product
    if product is None:
        return SaleStockSnapshot(app_qty, app_qty, from_marketplace=False)

    marketplace = order_item.marketplace
    if marketplace not in TRACKED_MARKETPLACES:
        return SaleStockSnapshot(app_qty, app_qty, from_marketplace=False)

    account = order_item.order.account
    ean = product_listing_ean(product=product, account=account)
    if not ean:
        return SaleStockSnapshot(app_qty, app_qty, from_marketplace=False)

    live = fetch_selling_channel_quantity(
        marketplace=marketplace,
        ean=ean,
        account=account,
        request_id=f"afterbuy-sale-{order_item.pk}",
    )
    if live is None:
        return SaleStockSnapshot(app_qty, app_qty, from_marketplace=False)
    return SaleStockSnapshot(
        qty_before=live + order_item.quantity,
        qty_after=live,
        from_marketplace=True,
    )


def sync_stock_after_sale(
    order_item: AfterbuyOrderItem, snapshot: SaleStockSnapshot
) -> None:
    """
    Выравнивает два других канала и карточку, только если остаток снят с площадки.

    Если GET не ответил — чужие листинги не трогаем.
    """
    if not snapshot.from_marketplace:
        return
    product = order_item.matched_product
    if product is None:
        return
    account = order_item.order.account
    ean = product_listing_ean(product=product, account=account)
    if not ean:
        return
    align_sibling_channel_quantities(
        sold_on=order_item.marketplace,
        ean=ean,
        account=account,
        quantity=snapshot.qty_after,
        request_id=f"afterbuy-align-{order_item.pk}",
    )
    apply_canonical_qty_to_product(product, snapshot.qty_after)
    record_channel_quantities(
        product=product,
        account=account,
        quantities={channel: snapshot.qty_after for channel in TRACKED_CHANNEL_ORDER},
    )
