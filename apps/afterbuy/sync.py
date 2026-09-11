from datetime import timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .client import afterbuy_credentials, fetch_sold_items_xml
from .matching import match_catalog_product
from .models import AfterbuyOrder, AfterbuyOrderItem
from .notify import queue_sale_notification
from .parser import is_tracked_marketplace, parse_sold_items_xml
from .types import NormalizedSoldItem, NormalizedSoldOrder

BERLIN = ZoneInfo("Europe/Berlin")


def _item_key(item: NormalizedSoldItem) -> str:
    return (
        item.item_id
        or item.anr
        or item.sku
        or item.product_id
        or item.ean
        or item.title[:80]
        or "item"
    )


def _marketplace(value: str) -> str:
    if is_tracked_marketplace(value):
        return value
    return AfterbuyOrder.Marketplace.OTHER


@transaction.atomic
def upsert_sold_order(*, account: str, order: NormalizedSoldOrder) -> AfterbuyOrder:
    stored, _created = AfterbuyOrder.objects.update_or_create(
        account=account,
        afterbuy_order_id=order.afterbuy_order_id,
        defaults={
            "marketplace": _marketplace(order.marketplace),
            "paid_at": order.paid_at,
            "total_amount": order.total_amount,
            "currency": (order.currency or "EUR")[:8],
            "payment_method": order.payment_method[:80],
            "platform_order_number": order.platform_order_number[:80],
            "buyer_name": order.buyer_name[:160],
            "buyer_email": order.buyer_email[:254],
            "buyer_platform_user_id": order.buyer_platform_user_id[:120],
        },
    )
    for item in order.items:
        if not is_tracked_marketplace(item.marketplace) and not is_tracked_marketplace(
            order.marketplace
        ):
            continue
        product = match_catalog_product(account=account, item=item)
        stored_item, _item_created = AfterbuyOrderItem.objects.update_or_create(
            order=stored,
            item_key=_item_key(item)[:80],
            defaults={
                "afterbuy_item_id": item.item_id[:32],
                "anr": item.anr[:64],
                "product_id": item.product_id[:64],
                "sku": item.sku[:64],
                "ean": item.ean[:14],
                "title": item.title[:255],
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "platform_name": item.platform_name[:80],
                "marketplace": _marketplace(item.marketplace or order.marketplace),
                "platform_order_number": item.platform_order_number[:80],
                "extra_tags": item.extra_tags,
                "matched_product": product,
            },
        )
        if product is not None:
            queue_sale_notification(order_item=stored_item)
    return stored


def sync_account(*, account: str) -> dict:
    credentials = afterbuy_credentials(account)
    if not credentials.is_configured():
        return {"account": account, "skipped": True, "reason": "missing_credentials"}

    now = timezone.now().astimezone(BERLIN)
    lookback = timedelta(hours=settings.AFTERBUY_LOOKBACK_HOURS)
    date_from = now - lookback
    date_to = now
    cursor: str | None = None
    seen: set[str] = set()
    fetched = 0
    stored = 0

    while True:
        xml = fetch_sold_items_xml(
            credentials=credentials,
            date_from=date_from,
            date_to=date_to,
            range_id_from=cursor,
        )
        orders, has_more, last_order_id = parse_sold_items_xml(xml)
        fetched += len(orders)
        for order in orders:
            if not order.items:
                continue
            if not any(
                is_tracked_marketplace(item.marketplace) for item in order.items
            ) and not is_tracked_marketplace(order.marketplace):
                continue
            upsert_sold_order(account=account, order=order)
            stored += 1
            seen.add(order.afterbuy_order_id)
        if not has_more or not last_order_id or last_order_id == cursor:
            break
        cursor = last_order_id

    return {
        "account": account,
        "skipped": False,
        "fetched": fetched,
        "stored": stored,
        "unique_orders": len(seen),
    }


def sync_afterbuy_sales() -> dict:
    if not settings.AFTERBUY_SYNC_ENABLED:
        return {"skipped": True, "reason": "disabled"}
    return {
        "jv": sync_account(account=AfterbuyOrder.Account.JV),
        "xl": sync_account(account=AfterbuyOrder.Account.XL),
    }
