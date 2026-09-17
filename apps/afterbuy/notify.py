from zoneinfo import ZoneInfo

from django.utils import timezone

from apps.notifications.models import Notification
from apps.notifications.services import create_notification

from .models import AfterbuyOrderItem, AfterbuySaleNotification
from .stock import read_quantities_for_sale, sync_stock_after_sale

BERLIN = ZoneInfo("Europe/Berlin")


def _format_sold_at(order_item: AfterbuyOrderItem) -> str:
    paid_at = order_item.order.paid_at
    if paid_at is None:
        return ""
    return timezone.localtime(paid_at, BERLIN).strftime("%d.%m.%Y %H:%M")


def notify_seller_product_sold(order_item: AfterbuyOrderItem) -> None:
    """
    Пишет продавцу in-app + ставит push в очередь.

    Остаток берём с площадки продажи, затем тот же остаток ставим
    на два других канала. Канал покупки не перезаписываем.
    """
    product = order_item.matched_product
    if product is None:
        return

    snapshot = read_quantities_for_sale(order_item)
    create_notification(
        user=product.owner,
        product=product,
        notification_type=Notification.Type.PRODUCT_SOLD,
        card_price=str(product.unit_price),
        currency=product.currency,
        sold_at=_format_sold_at(order_item),
        qty_sold=str(order_item.quantity),
        qty_before=str(snapshot.qty_before),
        qty_after=str(snapshot.qty_after),
    )
    sync_stock_after_sale(order_item, snapshot)


def queue_sale_notification(
    *, order_item: AfterbuyOrderItem
) -> AfterbuySaleNotification:
    """
    Идемпотентная отправка: одна Afterbuy-строка — одно сообщение продавцу.
    """
    sale_notification, _created = AfterbuySaleNotification.objects.get_or_create(
        order_item=order_item,
        defaults={"status": AfterbuySaleNotification.Status.PENDING},
    )
    if sale_notification.status == AfterbuySaleNotification.Status.SENT:
        return sale_notification
    if order_item.matched_product_id is None:
        return sale_notification

    notify_seller_product_sold(order_item)
    sale_notification.status = AfterbuySaleNotification.Status.SENT
    sale_notification.sent_at = timezone.now()
    sale_notification.save(update_fields=["status", "sent_at", "updated_at"])
    return sale_notification


def send_test_product_sold_notification(*, product) -> Notification:
    """
    Тестовый пуш с карточки менеджера. Склад и площадки не трогает.
    Шаблон тот же, что у реальной продажи.
    """
    stock = sum(variant.quantity for variant in product.variants.all())
    qty_sold = 1 if stock == 0 else min(2, stock)
    qty_after = max(0, stock - qty_sold)
    sold_at = timezone.localtime(timezone.now(), BERLIN).strftime("%d.%m.%Y %H:%M")
    return create_notification(
        user=product.owner,
        product=product,
        notification_type=Notification.Type.PRODUCT_SOLD,
        card_price=str(product.unit_price),
        currency=product.currency,
        sold_at=sold_at,
        qty_sold=str(qty_sold),
        qty_before=str(stock),
        qty_after=str(qty_after),
    )
