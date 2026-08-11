from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.products.models import Product

from .models import Notification
from .services import create_notification


@shared_task
def send_product_availability_reminders() -> dict:
    now = timezone.now()
    cutoff = now - timedelta(
        days = settings.PRODUCT_AVAILABLITY_REMINDER_DAYS
    )

    product_ids = list(
        Product.objects.filter(
            status=Product.Status.APPROVED,
            approved_at__lte=cutoff,
            availability_reminder_sent_at__isnull=True,
        )
        .order_by("id")
        .values_list("id", flat=True)[
            :settings.PRODUCT_AVAILABILITY_REMINDER_BATCH_SIZE
        ]
    )

    sent_count = 0

    for product_id in product_ids:
        with transaction.atomic():
            product = (
                Product.objects.select_for_update(
                    ship_locked=True,
                    of=("self",),
                ).select_related("owner")
                .filter(
                    id=product_id,
                    status=Product.Status.APPROVED,
                    approved_at__lte=cutoff,
                    availability_reminder_sent_at__isnull=True,
                )
                .first()
            )

            if product is None:
                continue

            product.availability_reminder_sent_at = now
            product.save(
                update_fields = (
                    "availability_reminder_sent_at",
                    "updated_at",
                )
            )

            create_notification(
                user=product.owner,
                product=product,
                notification_type=(
                    Notification.Type.PRODUCT_AVAILABILITY_REMINDER
                ),
                data={
                    "product_id": product.id,
                    "title": product.title,
                    "message": (
                        "Do you still have this product available?"
                    ),
                },
            )
            sent_count += 1

    return {
        "checked": len(product_ids),
        "sent": sent_count
    }