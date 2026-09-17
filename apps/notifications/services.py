from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User
from apps.products.models import Product

from .copy import copy_key_for_type, render_notification_copy
from .models import Notification
from .tasks import send_notification_push

_NAME_FALLBACK = {
    "en": "this product",
    "ru": "этот товар",
    "de": "dieses Produkt",
    "tr": "bu ürün",
}


def notification_language(user: User) -> str:
    from .copy import normalize_language

    return normalize_language(user.preferred_language)


def product_display_name(product: Product | None, *, language: str) -> str:
    if product is None:
        return _NAME_FALLBACK.get(language, _NAME_FALLBACK["ru"])
    name = (product.title or "").strip()
    if name:
        return name
    return _NAME_FALLBACK.get(language, _NAME_FALLBACK["ru"])


def product_availability_reminder_copy(
    product: Product, *, language: str | None = None
) -> tuple[str, str]:
    lang = language or notification_language(product.owner)
    return render_notification_copy(
        key="product_availability_reminder",
        language=lang,
        name=product_display_name(product, language=lang),
    )


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
    copy_key = (
        "product_confirmation_available"
        if is_available
        else "product_confirmation_unavailable"
    )
    for manager in manager_inbox_users(exclude_user=seller):
        create_notification(
            user=manager,
            sender=seller,
            product=product,
            notification_type=Notification.Type.PRODUCT_CONFIRMATION,
            copy_key=copy_key,
        )


SELLER_COMMENT_MARKER = "\n\n__SELLER_COMMENT__\n"


def attach_seller_comment(body: str, comment: str) -> str:
    text = (comment or "").strip()
    if not text:
        return body
    base = body.split(SELLER_COMMENT_MARKER, 1)[0].rstrip()
    return f"{base}{SELLER_COMMENT_MARKER}{text}"


def extract_seller_comment(body: str) -> str:
    if SELLER_COMMENT_MARKER not in (body or ""):
        return ""
    return body.split(SELLER_COMMENT_MARKER, 1)[1].strip()


def create_notification(
    *,
    user: User,
    notification_type: str,
    product: Product | None = None,
    sender: User | None = None,
    title: str = "",
    body: str = "",
    copy_key: str = "",
    comment: str = "",
    message: str = "",
    price: str = "",
    currency: str = "",
    sold_at: str = "",
    card_price: str = "",
    qty_sold: str = "",
    qty_before: str = "",
    qty_after: str = "",
    price_negotiation=None,
) -> Notification:
    key = copy_key_for_type(notification_type, copy_key=copy_key)
    if key and (not title or not body):
        language = notification_language(user)
        actor = sender or (product.owner if product is not None else None)
        seller_name = ""
        if actor is not None:
            seller_name = (actor.username or actor.email or "").strip()
        generated_title, generated_body = render_notification_copy(
            key=key,
            language=language,
            name=product_display_name(product, language=language),
            seller_name=seller_name,
            message=message,
            price=price,
            currency=currency,
            sold_at=sold_at,
            card_price=card_price,
            qty_sold=qty_sold,
            qty_before=qty_before,
            qty_after=qty_after,
        )
        title = title or generated_title
        body = body or generated_body

    body = attach_seller_comment(body, comment)

    notification = Notification.objects.create(
        user=user,
        sender=sender,
        product=product,
        notification_type=notification_type,
        title=title,
        body=body,
        price_negotiation=price_negotiation,
    )

    transaction.on_commit(lambda: send_notification_push.delay(notification.id))

    return notification
