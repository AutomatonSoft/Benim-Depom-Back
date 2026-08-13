import logging

from celery import shared_task
from firebase_admin import messaging

from .firebase import get_firebase_app
from .models import DeviceToken, Notification
from django.core.exceptions import ImproperlyConfigured


logger = logging.getLogger(__name__)


@shared_task
def send_notification_push(notification_id: int) -> dict:
    notification = (
        Notification.objects.select_related("user", "product")
        .filter(id=notification_id)
        .first()
    )

    if notification is None:
        return {"status": "skipped", "reason": "notification_not_found"}

    firebase_app = get_firebase_app()

    if firebase_app is None:
        return {"status": "skipped", "reason": "firebase_disabled"}

    device_tokens = list(
        DeviceToken.objects.filter(
            user=notification.user,
            is_active=True,
        )
    )

    if not device_tokens:
        return {"status": "skipped", "reason": "no_active_device_tokens"}

    title = notification.title or "Marketplace"
    body = notification.body or "You have a new notification."

    data = {
        "notification_id": str(notification.id),
        "notification_type": notification.notification_type,
        "product_id": str(notification.product_id or ""),
    }

    sent_count = 0
    deactivated_count = 0
    failed_count = 0

    for device_token in device_tokens:
        message = messaging.Message(
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            data=data,
            token=device_token.token,
        )

        try:
            messaging.send(message, app=firebase_app)
            sent_count += 1
        except messaging.UnregisteredError:
            device_token.is_active = False
            device_token.save(update_fields=("is_active",))
            deactivated_count += 1
        except Exception:
            logger.exception(
                "Unable to send push notification.",
                extra={
                    "notification_id": notification.id,
                    "device_token_id": device_token.id,
                },
            )
            failed_count += 1

    return {
        "status": "completed",
        "sent": sent_count,
        "deactivated": deactivated_count,
        "failed": failed_count,
    }



@shared_task
def send_product_availability_reminders() -> dict:
    from datetime import timedelta

    from django.conf import settings
    from django.db import transaction
    from django.utils import timezone

    from apps.products.models import Product

    from .services import create_notification

    now = timezone.now()
    cutoff = now - timedelta(
        days=settings.PRODUCT_AVAILABILITY_REMINDER_DAYS
    )

    from django.db.models import Q

    eligible_products = Product.objects.filter(
        status=Product.Status.APPROVED,
    ).filter(
        Q(
            approved_at__lte=cutoff,
            availability_reminder_sent_at__isnull=True,
        )
        | Q(availability_reminder_sent_at__lte=cutoff)
    )

    product_ids = list(
        eligible_products
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
                    skip_locked=True,
                    of=("self",),
                )
                .select_related("owner")
                .filter(
                    id=product_id,
                    status=Product.Status.APPROVED,
                )
                .filter(
                    Q(
                        approved_at__lte=cutoff,
                        availability_reminder_sent_at__isnull=True,
                    )
                    | Q(availability_reminder_sent_at__lte=cutoff)
                )
                .first()
            )

            if product is None:
                continue

            product.availability_reminder_sent_at = now
            product.save(
                update_fields=(
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
                title="Product availability",
                body=(
                    "Do you still have this product available?"
                ),
                data={
                    "product_id": product.id,
                    "title": product.title,
                },
            )
            sent_count += 1

    return {
        "checked": len(product_ids),
        "sent": sent_count,
    }



@shared_task
def process_product_image(image_id: int) -> dict:
    from apps.common.white_image_service import (
        WhiteImageServiceError,
        generate_white_background,
    )
    from apps.products.models import ProductImage

    image = (
        ProductImage.objects.select_related(
            "product",
            "product__product_type",
        )
        .filter(id=image_id)
        .first()
    )

    if image is None:
        return {"status": "skipped", "reason": "image_not_found"}

    if image.processing_status != ProductImage.ProcessingStatus.PENDING:
        return {"status": "skipped", "reason": "not_pending"}

    image.processing_status = ProductImage.ProcessingStatus.PROCESSING
    image.processing_error = ""
    image.save(
        update_fields=("processing_status", "processing_error")
    )

    try:
        result = generate_white_background(
            image_file=image.image,
            title=image.product.title,
            product_type=image.product.product_type.name,
        )
    except (WhiteImageServiceError, ImproperlyConfigured) as error:
        image.processing_status = ProductImage.ProcessingStatus.FAILED
        image.processing_error = str(error)[:500]
        image.save(
            update_fields=("processing_status", "processing_error")
        )
        return {"status": "failed"}

    payload = result.get("payload", {})
    external_product_id = payload.get("product_id")

    if payload.get("status") != "queued" or not external_product_id:
        image.processing_status = ProductImage.ProcessingStatus.FAILED
        image.processing_error = "Unexpected AI service response."
        image.processing_result = result
        image.save(
            update_fields=(
                "processing_status",
                "processing_error",
                "processing_result",
            )
        )
        return {"status": "failed"}

    image.processing_result = result
    image.save(update_fields=("processing_result",))

    check_product_image_generation.apply_async(
        args=(image.id, external_product_id, 1),
        countdown=10,
    )

    return {
        "status": "queued",
        "image_id": image.id,
        "external_product_id": external_product_id,
    }


@shared_task
def check_product_image_generation(
    image_id: int,
    external_product_id: int,
    attempt: int,
) -> dict:
    from django.conf import settings
    from django.core.files.base import ContentFile

    import requests

    from apps.common.white_image_service import (
        WhiteImageServiceError,
        get_generation_results,
    )
    from apps.products.models import (
        ProductGeneratedImage,
        ProductImage,
    )

    image = ProductImage.objects.filter(id=image_id).first()

    if image is None:
        return {"status": "skipped", "reason": "image_not_found"}

    if attempt > settings.BULK_WHITE_IMAGE_SERVICE_MAX_POLL_ATTEMPTS:
        image.processing_status = ProductImage.ProcessingStatus.FAILED
        image.processing_error = "AI generation timed out."
        image.save(
            update_fields=("processing_status", "processing_error")
        )
        return {"status": "failed", "reason": "timeout"}

    try:
        payload = image.processing_result.get("payload", {})
        result = get_generation_results(
            product_id=external_product_id,
            result_url=payload.get("status_url"),
        )
    except (WhiteImageServiceError, ImproperlyConfigured) as error:
        image.processing_status = ProductImage.ProcessingStatus.FAILED
        image.processing_error = str(error)[:500]
        image.save(
            update_fields=("processing_status", "processing_error")
        )
        return {"status": "failed"}

    if result.get("status") != "completed":
        check_product_image_generation.apply_async(
            args=(image.id, external_product_id, attempt + 1),
            countdown=(
                settings.BULK_WHITE_IMAGE_SERVICE_POLL_INTERVAL_SECONDS
            ),
        )
        return {
            "status": result.get("status", "waiting"),
            "attempt": attempt,
        }

    images = result.get("images", {})
    required_modes = ("white", "interior", "human")

    if not all(images.get(mode) for mode in required_modes):
        image.processing_status = ProductImage.ProcessingStatus.FAILED
        image.processing_error = "AI response does not contain all images."
        image.processing_result = result
        image.save(
            update_fields=(
                "processing_status",
                "processing_error",
                "processing_result",
            )
        )
        return {"status": "failed"}

    try:
        for mode in required_modes:
            response = requests.get(
                images[mode],
                timeout=settings.BULK_WHITE_IMAGE_SERVICE_TIMEOUT_SECONDS,
            )
            response.raise_for_status()

            generated_image, _ = (
                ProductGeneratedImage.objects.get_or_create(
                    source_image=image,
                    mode=mode,
                )
            )

            filename = f"{mode}-{image.id}.jpg"
            generated_image.image.save(
                filename,
                ContentFile(response.content),
                save=True,
            )

            if mode == ProductGeneratedImage.Mode.WHITE:
                image.processed_image = generated_image.image

    except requests.RequestException:
        image.processing_status = ProductImage.ProcessingStatus.FAILED
        image.processing_error = "Unable to download generated image."
        image.save(
            update_fields=("processing_status", "processing_error")
        )
        return {"status": "failed"}

    image.processing_status = ProductImage.ProcessingStatus.SUCCEEDED
    image.processing_error = ""
    image.processing_result = result
    image.save(
        update_fields=(
            "processed_image",
            "processing_status",
            "processing_error",
            "processing_result",
        )
    )

    return {
        "status": "completed",
        "image_id": image.id,
    }
