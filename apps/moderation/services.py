from django.db import transaction
from rest_framework.exceptions import ValidationError
from apps.notifications.models import Notification
from apps.notifications.services import create_notification
from apps.products.models import Product
from apps.ean.services import assign_ean_codes_to_product
from django.utils import timezone

from .models import ModerationDecision


@transaction.atomic
def submit_product_for_moderation(*, product: Product) -> Product:
    product = Product.objects.select_for_update().get(pk=product.pk)

    if product.status not in {
        Product.Status.DRAFT,
        Product.Status.REJECTED,
    }:
        raise ValidationError(
            {"detail": "This product cannot be submitted for moderation."}
        )

    if not product.variants.exists():
        raise ValidationError(
            {"variants": "The product must contain at least one variant."}
        )

    if not product.images.exists():
        raise ValidationError(
            {"images": "The product must contain at least one image."}
        )

    product.status = Product.Status.SUBMITTED
    product.save(update_fields=("status", "updated_at"))

    return product


@transaction.atomic
def approve_product(
    *,
    product: Product,
    manager,
    comment: str = "",
) -> Product:
    product = Product.objects.select_for_update().get(pk=product.pk)

    if product.status not in {
        Product.Status.SUBMITTED,
        Product.Status.UNDER_REVIEW,
    }:
        raise ValidationError(
            {"detail": "Only submitted products can be approved."}
        )

    # EANs are consumed only for a product the manager actually approves.
    # The same transaction prevents a partial approval if a pool is empty.
    assign_ean_codes_to_product(product=product)

    product.status = Product.Status.APPROVED
    product.approved_at = timezone.now()
    product.is_available = True
    product.availability_reminder_sent_at = None
    product.save(
        update_fields=(
            "status",
            "approved_at",
            "is_available",
            "availability_reminder_sent_at",
            "updated_at",
        )
    )

    ModerationDecision.objects.create(
        product=product,
        manager=manager,
        decision=ModerationDecision.Decision.APPROVED,
        comment=comment,
    )

    transaction.on_commit(
        lambda: create_notification(
            user=product.owner,
            product=product,
            notification_type=Notification.Type.PRODUCT_APPROVED,
            data={"product_id": product.id},
        )
    )

    return product


@transaction.atomic
def reject_product(
    *,
    product: Product,
    manager,
    comment: str,
) -> Product:
    product = Product.objects.select_for_update().get(pk=product.pk)

    if product.status not in {
        Product.Status.SUBMITTED,
        Product.Status.UNDER_REVIEW,
    }:
        raise ValidationError(
            {"detail": "Only submitted products can be rejected."}
        )

    product.status = Product.Status.REJECTED
    product.save(update_fields=("status", "updated_at"))

    ModerationDecision.objects.create(
        product=product,
        manager=manager,
        decision=ModerationDecision.Decision.REJECTED,
        comment=comment,
    )

    transaction.on_commit(
        lambda: create_notification(
            user=product.owner,
            product=product,
            notification_type=Notification.Type.PRODUCT_REJECTED,
            data={
                "product_id": product.id,
                "reason": comment,
            },
        )
    )

    return product


