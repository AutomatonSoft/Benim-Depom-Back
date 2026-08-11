from apps.accounts.models import User
from apps.products.models import Product

from .models import Notification

def create_notification(
    *,
    user: User,
    notification_type: str,
    product: Product | None = None,
    data: dict | None = None
) -> Notification:
    return Notification.objects.create(
        user=user,
        product=product,
        notification_type=notification_type,
        data=data or {}
    )

