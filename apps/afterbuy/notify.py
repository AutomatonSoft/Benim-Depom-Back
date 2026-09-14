from .models import AfterbuyOrderItem, AfterbuySaleNotification


def queue_sale_notification(
    *, order_item: AfterbuyOrderItem
) -> AfterbuySaleNotification:
    notification, _created = AfterbuySaleNotification.objects.get_or_create(
        order_item=order_item,
        defaults={"status": AfterbuySaleNotification.Status.PENDING},
    )
    return notification
