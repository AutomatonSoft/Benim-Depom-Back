from apps.accounts.models import User
from apps.products.models import Product

from django.db import transaction

from .tasks import send_notification_push
from .models import Notification


def create_notification(
    *,
    user: User,
    notification_type: str,
    product: Product | None = None,
    sender: User | None = None,
    title: str = "",
    body: str = "",
    data: dict | None = None,
) -> Notification:
    
    notification = Notification.objects.create(
        user=user,
        sender=sender,
        product=product,
        notification_type=notification_type,
        title=title,
        body=body,
        data=data or {},
    )

    transaction.on_commit(
        lambda: send_notification_push.delay(notification.id)
    )

    return notification

