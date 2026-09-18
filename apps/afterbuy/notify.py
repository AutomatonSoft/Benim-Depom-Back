from zoneinfo import ZoneInfo

from django.utils import timezone

from apps.notifications.models import Notification
from apps.notifications.services import create_notification, manager_inbox_users

from .models import AfterbuyOrderItem, AfterbuySaleNotification

BERLIN = ZoneInfo("Europe/Berlin")


def _format_sold_at(order_item: AfterbuyOrderItem) -> str:
    paid_at = order_item.order.paid_at
    if paid_at is None:
        return ""
    return timezone.localtime(paid_at, BERLIN).strftime("%d.%m.%Y %H:%M")


def notify_managers_product_sold(order_item: AfterbuyOrderItem) -> None:
    """Пишет менеджерам в inbox. Склад и площадки не трогает."""
    product = order_item.matched_product
    if product is None:
        return

    marketplace = order_item.get_marketplace_display()
    sold_at = _format_sold_at(order_item)
    qty_sold = str(order_item.quantity)
    for manager in manager_inbox_users():
        create_notification(
            user=manager,
            sender=product.owner,
            product=product,
            notification_type=Notification.Type.PRODUCT_SOLD,
            sold_at=sold_at,
            qty_sold=qty_sold,
            marketplace=marketplace,
            afterbuy_order_item=order_item,
        )


def queue_sale_notification(
    *, order_item: AfterbuyOrderItem
) -> AfterbuySaleNotification:
    """
    Идемпотентная отправка: одна Afterbuy-строка — одно сообщение менеджерам.
    """
    sale_notification, _created = AfterbuySaleNotification.objects.get_or_create(
        order_item=order_item,
        defaults={"status": AfterbuySaleNotification.Status.PENDING},
    )
    if sale_notification.status == AfterbuySaleNotification.Status.SENT:
        return sale_notification
    if order_item.matched_product_id is None:
        return sale_notification

    notify_managers_product_sold(order_item)
    sale_notification.status = AfterbuySaleNotification.Status.SENT
    sale_notification.sent_at = timezone.now()
    sale_notification.save(update_fields=["status", "sent_at", "updated_at"])
    return sale_notification
