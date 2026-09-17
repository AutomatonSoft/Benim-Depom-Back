from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import AfterbuyChannelStock, AfterbuyOrderItem
from .parser import TRACKED_MARKETPLACES
from .stock import (
    TRACKED_CHANNEL_ORDER,
    align_sibling_channel_quantities,
    apply_canonical_qty_to_product,
    fetch_selling_channel_quantity,
    product_listing_ean,
    record_channel_quantities,
)


def decide_stock_action(
    previous: dict[str, int | None],
    live: dict[str, int | None],
    last_sold_qty: dict[str, int],
) -> tuple[str, str | None, int | None]:
    """
    Сравнивает прошлый снимок и живые остатки.

    cancel_restore — один канал вырос на qty последней продажи, остальные не падали.
    skip_dirty — картина грязная, ничего не пишем.
    noop — менять чужие каналы не нужно.
    """
    increases: list[tuple[str, int, int]] = []
    decreases: list[tuple[str, int, int]] = []
    for channel in TRACKED_CHANNEL_ORDER:
        old = previous.get(channel)
        new = live.get(channel)
        if old is None or new is None:
            continue
        if new > old:
            increases.append((channel, old, new))
        elif new < old:
            decreases.append((channel, old, new))

    if len(increases) == 1 and not decreases:
        channel, old, new = increases[0]
        if last_sold_qty.get(channel) == new - old:
            return "cancel_restore", channel, new
        return "skip_dirty", channel, new
    if increases or (len(decreases) > 1):
        return "skip_dirty", None, None
    return "noop", None, None


def load_previous_quantities(*, product, account: str) -> dict[str, int | None]:
    rows = AfterbuyChannelStock.objects.filter(product=product, account=account)
    found = {row.marketplace: row.quantity for row in rows}
    return {channel: found.get(channel) for channel in TRACKED_CHANNEL_ORDER}


def last_sold_qty_by_channel(*, product, account: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for channel in TRACKED_CHANNEL_ORDER:
        item = (
            AfterbuyOrderItem.objects.filter(
                matched_product=product,
                order__account=account,
                marketplace=channel,
            )
            .order_by("-order__paid_at", "-id")
            .first()
        )
        if item is not None:
            result[channel] = item.quantity
    return result


def fetch_live_quantities(*, ean: str, account: str, request_id: str) -> dict[str, int | None]:
    live: dict[str, int | None] = {}
    for channel in TRACKED_CHANNEL_ORDER:
        live[channel] = fetch_selling_channel_quantity(
            marketplace=channel,
            ean=ean,
            account=account,
            request_id=f"{request_id}-{channel}",
        )
    return live


def reconcile_product_account(*, product, account: str, request_id: str) -> str:
    """
    Смотрит три канала. При отмене (рост на канале продажи) возвращает остаток
    на остальные. Грязные дельты не трогает.
    """
    ean = product_listing_ean(product=product, account=account)
    if not ean:
        return "skipped"
    live = fetch_live_quantities(ean=ean, account=account, request_id=request_id)
    previous = load_previous_quantities(product=product, account=account)
    if all(value is None for value in previous.values()):
        record_channel_quantities(product=product, account=account, quantities=live)
        return "snapshot"
    action, channel, quantity = decide_stock_action(
        previous,
        live,
        last_sold_qty_by_channel(product=product, account=account),
    )
    if action == "cancel_restore" and channel is not None and quantity is not None:
        align_sibling_channel_quantities(
            sold_on=channel,
            ean=ean,
            account=account,
            quantity=quantity,
            request_id=f"{request_id}-restore",
        )
        apply_canonical_qty_to_product(product, quantity)
        restored = {
            name: quantity if live.get(name) is None else live[name]
            for name in TRACKED_CHANNEL_ORDER
        }
        restored[channel] = quantity
        for sibling in TRACKED_CHANNEL_ORDER:
            if sibling != channel:
                restored[sibling] = quantity
        record_channel_quantities(product=product, account=account, quantities=restored)
        return action
    record_channel_quantities(product=product, account=account, quantities=live)
    return action


def _hot_product_accounts():
    since = timezone.now() - timedelta(days=settings.AFTERBUY_STOCK_HOT_DAYS)
    return (
        AfterbuyOrderItem.objects.filter(
            matched_product_id__isnull=False,
            order__paid_at__gte=since,
            marketplace__in=TRACKED_MARKETPLACES,
        )
        .values_list("matched_product_id", "order__account")
        .distinct()
    )


def reconcile_hot_stock() -> dict:
    """Каждые 15 минут только товары с недавней продажей (окно отзыва)."""
    from apps.products.models import Product

    seen = 0
    for product_id, account in _hot_product_accounts()[: settings.AFTERBUY_STOCK_RECONCILE_BATCH]:
        product = Product.objects.filter(pk=product_id).first()
        if product is None:
            continue
        reconcile_product_account(
            product=product,
            account=account,
            request_id=f"afterbuy-hot-{product_id}-{account}",
        )
        seen += 1
    return {"hot": seen}


def reconcile_sale_followups() -> dict:
    """Разовая сверка ~на 15-й день после оплаты."""
    now = timezone.now()
    followup_after = now - timedelta(days=settings.AFTERBUY_STOCK_FOLLOWUP_DAYS)
    followup_since = now - timedelta(days=settings.AFTERBUY_STOCK_HOT_DAYS)
    items = (
        AfterbuyOrderItem.objects.filter(
            matched_product_id__isnull=False,
            followup_reconciled_at__isnull=True,
            order__paid_at__lte=followup_after,
            order__paid_at__gte=followup_since,
            marketplace__in=TRACKED_MARKETPLACES,
        )
        .select_related("matched_product", "order")
        .order_by("id")[: settings.AFTERBUY_STOCK_RECONCILE_BATCH]
    )
    done = 0
    for item in items:
        reconcile_product_account(
            product=item.matched_product,
            account=item.order.account,
            request_id=f"afterbuy-followup-{item.pk}",
        )
        item.followup_reconciled_at = now
        item.save(update_fields=["followup_reconciled_at", "updated_at"])
        done += 1
    return {"followups": done}


def reconcile_afterbuy_stock() -> dict:
    if not settings.AFTERBUY_SYNC_ENABLED:
        return {"skipped": True, "reason": "disabled"}
    return {**reconcile_hot_stock(), **reconcile_sale_followups()}
