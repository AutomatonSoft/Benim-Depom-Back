from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User
from apps.products.models import Product

from .models import Notification
from .tasks import send_notification_push


def product_availability_reminder_copy(product: Product) -> tuple[str, str]:
    name = (product.title or "").strip()
    if name:
        body = f'Do you still have "{name}" available?'
    else:
        body = "Do you still have this product available?"
    return "Product availability", body


def mark_product_availability_reminders_responded(*, product: Product) -> int:
    return Notification.objects.filter(
        product=product,
        notification_type=Notification.Type.PRODUCT_AVAILABILITY_REMINDER,
        responded_at__isnull=True,
    ).update(responded_at=timezone.now())


def manager_inbox_users(*, exclude_user: User | None = None):
    queryset = User.objects.filter(
        Q(role__in=(User.Role.MANAGER, User.Role.ADMIN)) | Q(is_superuser=True)
    )
    if exclude_user is not None:
        queryset = queryset.exclude(pk=exclude_user.pk)
    return queryset.distinct()


def notify_managers_of_availability_confirmation(
    *,
    product: Product,
    seller: User,
    is_available: bool,
) -> None:
    name = (product.title or "").strip() or f"#{product.pk}"
    seller_name = (seller.username or seller.email or "Seller").strip()
    if is_available:
        title = "Product is available"
        body = f"{seller_name} confirmed '{name}' is still available."
    else:
        title = "Product is not available"
        body = f"{seller_name} confirmed '{name}' is not available."

    for manager in manager_inbox_users(exclude_user=seller):
        create_notification(
            user=manager,
            sender=seller,
            product=product,
            notification_type=Notification.Type.PRODUCT_CONFIRMATION,
            title=title,
            body=body,
        )


def create_notification(
    *,
    user: User,
    notification_type: str,
    product: Product | None = None,
    sender: User | None = None,
    title: str = "",
    body: str = "",
) -> Notification:

    notification = Notification.objects.create(
        user=user,
        sender=sender,
        product=product,
        notification_type=notification_type,
        title=title,
        body=body,
    )

    transaction.on_commit(lambda: send_notification_push.delay(notification.id))

    return notification
