import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone
from firebase_admin import messaging

from .firebase import get_firebase_app
from .models import DeviceToken, Notification, PushDelivery

logger = logging.getLogger(__name__)


def _claim_push_delivery(
    *,
    notification: Notification,
    device_token: DeviceToken,
) -> PushDelivery | None:
    """
    Atomically reserves one device delivery.

    A fresh PROCESSING delivery is owned by another worker and is skipped.
    An old PROCESSING delivery is treated as abandoned and retried.
    """
    now = timezone.now()
    stale_before = now - timedelta(
        seconds=settings.PUSH_DELIVERY_PROCESSING_LEASE_SECONDS
    )

    with transaction.atomic():
        delivery, _ = PushDelivery.objects.select_for_update().get_or_create(
            notification=notification,
            device_token=device_token,
        )

        if delivery.status in {
            PushDelivery.Status.SENT,
            PushDelivery.Status.INVALID,
            PushDelivery.Status.FAILED,
        }:
            return None

        if (
            delivery.status == PushDelivery.Status.PROCESSING
            and delivery.last_attempt_at is not None
            and delivery.last_attempt_at > stale_before
        ):
            return None

        delivery.status = PushDelivery.Status.PROCESSING
        delivery.attempt_count += 1
        delivery.last_attempt_at = now
        delivery.save(
            update_fields=(
                "status",
                "attempt_count",
                "last_attempt_at",
                "updated_at",
            )
        )

        return delivery


@shared_task(
    bind=True,
    name="apps.notifications.tasks.send_notification_push",
)
def send_notification_push(self, notification_id: int) -> dict:
    """
    Sends each push once per device.

    Temporary Firebase failures retry only unsent PushDelivery rows.
    Already sent devices are never notified again during a retry.
    """
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

    device_tokens = DeviceToken.objects.filter(
        user=notification.user,
        is_active=True,
    )

    if not device_tokens.exists():
        return {"status": "skipped", "reason": "no_active_device_tokens"}

    from .copy import render_notification_copy

    fallback_title, fallback_body = render_notification_copy(
        key="push_fallback",
        language=notification.user.preferred_language,
    )
    title = notification.title or fallback_title
    body = notification.body or fallback_body
    data = {
        "notification_id": str(notification.id),
        "notification_type": notification.notification_type,
        "product_id": str(notification.product_id or ""),
        "product_title": (
            notification.product.title if notification.product_id else ""
        ),
    }

    sent_count = 0
    invalid_count = 0
    retryable_delivery_ids = []
    final_error = ""

    for device_token in device_tokens:
        delivery = _claim_push_delivery(
            notification=notification,
            device_token=device_token,
        )

        if delivery is None:
            continue

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
        except messaging.UnregisteredError:
            device_token.is_active = False
            device_token.save(update_fields=("is_active", "last_seen_at"))

            PushDelivery.objects.filter(pk=delivery.pk).update(
                status=PushDelivery.Status.INVALID,
                last_error="Firebase token is no longer registered.",
            )
            invalid_count += 1

        except Exception as exc:
            error_message = f"{type(exc).__name__}: {exc}"[:500]

            logger.exception(
                "Unable to send push notification.",
                extra={
                    "notification_id": notification.id,
                    "device_token_id": device_token.id,
                    "push_delivery_id": delivery.id,
                },
            )

            PushDelivery.objects.filter(pk=delivery.pk).update(
                status=PushDelivery.Status.PENDING,
                last_error=error_message,
            )
            retryable_delivery_ids.append(delivery.id)
            final_error = error_message

        else:
            PushDelivery.objects.filter(pk=delivery.pk).update(
                status=PushDelivery.Status.SENT,
                sent_at=timezone.now(),
                last_error="",
            )
            sent_count += 1

    if retryable_delivery_ids:
        retries_used = self.request.retries
        max_retries = settings.PUSH_NOTIFICATION_MAX_ATTEMPTS - 1

        if retries_used < max_retries:
            retry_number = retries_used + 1
            countdown = min(
                settings.PUSH_NOTIFICATION_RETRY_BASE_SECONDS
                * (2 ** (retry_number - 1)),
                settings.PUSH_NOTIFICATION_RETRY_MAX_SECONDS,
            )

            raise self.retry(
                exc=RuntimeError(final_error or "Temporary Firebase error."),
                countdown=countdown,
                max_retries=max_retries,
            )

        PushDelivery.objects.filter(
            id__in=retryable_delivery_ids,
            status=PushDelivery.Status.PENDING,
        ).update(status=PushDelivery.Status.FAILED)

    return {
        "status": (
            "completed" if not retryable_delivery_ids else "failed_after_retries"
        ),
        "sent": sent_count,
        "invalid": invalid_count,
        "failed": len(retryable_delivery_ids),
    }


@shared_task(
    name="apps.notifications.tasks.recover_stale_push_deliveries",
)
def recover_stale_push_deliveries() -> dict[str, int]:
    """
    Returns abandoned push deliveries from PROCESSING back to PENDING.

    A new regular push task is scheduled per notification. SENT and INVALID
    rows are never touched, so already delivered pushes are not duplicated.
    """
    now = timezone.now()
    stale_before = now - timedelta(
        seconds=settings.PUSH_DELIVERY_PROCESSING_LEASE_SECONDS
    )

    notification_ids = list(
        PushDelivery.objects.filter(
            status=PushDelivery.Status.PROCESSING,
            last_attempt_at__lt=stale_before,
        )
        .order_by("notification_id")
        .values_list("notification_id", flat=True)
        .distinct()[: settings.PUSH_DELIVERY_RECOVERY_BATCH_SIZE]
    )

    recovered_deliveries = 0
    scheduled_notifications = 0
    skipped_notifications = 0

    for notification_id in notification_ids:
        with transaction.atomic():
            deliveries = list(
                PushDelivery.objects.select_for_update(skip_locked=True).filter(
                    notification_id=notification_id,
                    status=PushDelivery.Status.PROCESSING,
                    last_attempt_at__lt=stale_before,
                )
            )

            if not deliveries:
                skipped_notifications += 1
                continue

            for delivery in deliveries:
                delivery.status = PushDelivery.Status.PENDING
                delivery.last_error = (
                    "Push delivery lease expired; scheduled for recovery."
                )
                delivery.save(
                    update_fields=(
                        "status",
                        "last_error",
                        "updated_at",
                    )
                )
                recovered_deliveries += 1

            transaction.on_commit(
                lambda current_notification_id=notification_id: (
                    send_notification_push.delay(current_notification_id)
                )
            )
            scheduled_notifications += 1

    return {
        "candidate_notifications": len(notification_ids),
        "recovered_deliveries": recovered_deliveries,
        "scheduled_notifications": scheduled_notifications,
        "skipped_notifications": skipped_notifications,
    }


@shared_task
def send_product_availability_reminders() -> dict:
    from datetime import timedelta

    from django.conf import settings
    from django.db import transaction
    from django.utils import timezone

    from apps.products.models import Product

    from .services import create_notification, product_availability_reminder_copy

    now = timezone.now()
    cutoff = now - timedelta(days=settings.PRODUCT_AVAILABILITY_REMINDER_DAYS)

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
        eligible_products.order_by("id").values_list("id", flat=True)[
            : settings.PRODUCT_AVAILABILITY_REMINDER_BATCH_SIZE
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

            title, body = product_availability_reminder_copy(product)
            create_notification(
                user=product.owner,
                product=product,
                notification_type=(Notification.Type.PRODUCT_AVAILABILITY_REMINDER),
                title=title,
                body=body,
            )
            sent_count += 1

    return {
        "checked": len(product_ids),
        "sent": sent_count,
    }


@shared_task
def process_product_image(image_id: int) -> dict:
    from django.db import transaction
    from django.utils import timezone

    from apps.common.white_image_service import (
        WhiteImageServiceError,
        generate_white_background,
    )
    from apps.products.models import ProductImage

    now = timezone.now()

    with transaction.atomic():
        image = (
            ProductImage.objects.select_for_update(skip_locked=True)
            .select_related("product")
            .filter(id=image_id)
            .first()
        )

        if image is None:
            if ProductImage.objects.filter(id=image_id).exists():
                return {
                    "status": "skipped",
                    "reason": "image_is_locked",
                }

            return {
                "status": "skipped",
                "reason": "image_not_found",
            }

        if image.processing_status != ProductImage.ProcessingStatus.PENDING:
            return {"status": "skipped", "reason": "not_pending"}

        image.processing_status = ProductImage.ProcessingStatus.PROCESSING
        image.processing_error = ""
        image.processing_claimed_at = now
        image.processing_finished_at = None
        image.save(
            update_fields=(
                "processing_status",
                "processing_error",
                "processing_claimed_at",
                "processing_finished_at",
            )
        )

        product_title = image.product.title
        product_type = image.product.product_type
        image_file = image.image

    try:
        result = generate_white_background(
            image_file=image_file,
            title=product_title,
            product_type=product_type,
        )
    except (WhiteImageServiceError, ImproperlyConfigured) as error:
        with transaction.atomic():
            image = ProductImage.objects.select_for_update().get(pk=image_id)

            if image.processing_status != ProductImage.ProcessingStatus.PROCESSING:
                return {"status": "skipped", "reason": "state_changed"}

            image.processing_status = ProductImage.ProcessingStatus.FAILED
            image.processing_error = str(error)[:500]
            image.processing_finished_at = timezone.now()
            image.save(
                update_fields=(
                    "processing_status",
                    "processing_error",
                    "processing_finished_at",
                )
            )

        return {"status": "failed"}

    payload = result.get("payload", {})
    external_product_id = payload.get("product_id")

    if payload.get("status") != "queued" or not external_product_id:
        with transaction.atomic():
            image = ProductImage.objects.select_for_update().get(pk=image_id)

            if image.processing_status != ProductImage.ProcessingStatus.PROCESSING:
                return {"status": "skipped", "reason": "state_changed"}

            image.processing_status = ProductImage.ProcessingStatus.FAILED
            image.processing_error = "Unexpected AI service response."
            image.processing_result = result
            image.processing_finished_at = timezone.now()
            image.save(
                update_fields=(
                    "processing_status",
                    "processing_error",
                    "processing_result",
                    "processing_finished_at",
                )
            )

        return {"status": "failed"}

    with transaction.atomic():
        image = ProductImage.objects.select_for_update().get(pk=image_id)

        if image.processing_status != ProductImage.ProcessingStatus.PROCESSING:
            return {"status": "skipped", "reason": "state_changed"}

        image.processing_result = result
        image.save(update_fields=("processing_result",))

    check_product_image_generation.apply_async(
        args=(image_id, external_product_id, 1),
        countdown=10,
    )

    return {
        "status": "queued",
        "image_id": image_id,
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
    from django.db import transaction
    from django.utils import timezone

    from apps.common.safe_image_download import (
        GeneratedImageDownloadError,
        download_generated_image,
    )
    from apps.common.white_image_service import (
        WhiteImageServiceError,
        get_generation_results,
    )
    from apps.products.models import (
        ProductGeneratedImage,
        ProductImage,
    )

    now = timezone.now()
    stale_before = now - timedelta(seconds=settings.IMAGE_PROCESSING_LEASE_SECONDS)

    def mark_failed(*, error: str, result: dict | None = None) -> None:
        with transaction.atomic():
            locked_image = (
                ProductImage.objects.select_for_update().filter(id=image_id).first()
            )

            if (
                locked_image is None
                or locked_image.processing_status
                == ProductImage.ProcessingStatus.SUCCEEDED
            ):
                return

            locked_image.processing_status = ProductImage.ProcessingStatus.FAILED
            locked_image.processing_error = error[:500]
            locked_image.processing_finished_at = timezone.now()

            if result is not None:
                locked_image.processing_result = result

            update_fields = [
                "processing_status",
                "processing_error",
                "processing_finished_at",
            ]

            if result is not None:
                update_fields.append("processing_result")

            locked_image.save(update_fields=update_fields)

    with transaction.atomic():
        image = (
            ProductImage.objects.select_for_update(skip_locked=True)
            .filter(id=image_id)
            .first()
        )

        if image is None:
            if ProductImage.objects.filter(id=image_id).exists():
                return {
                    "status": "skipped",
                    "reason": "image_is_locked",
                }

            return {
                "status": "skipped",
                "reason": "image_not_found",
            }

        if image.processing_status == ProductImage.ProcessingStatus.SUCCEEDED:
            return {
                "status": "skipped",
                "reason": "already_succeeded",
            }

        if image.processing_status == ProductImage.ProcessingStatus.FAILED:
            return {
                "status": "skipped",
                "reason": "already_failed",
            }

        if (
            image.processing_status == ProductImage.ProcessingStatus.RESULT_RECEIVED
            and image.processing_claimed_at is not None
            and image.processing_claimed_at > stale_before
        ):
            return {
                "status": "skipped",
                "reason": "finalization_in_progress",
            }

        if image.processing_status not in {
            ProductImage.ProcessingStatus.PENDING,
            ProductImage.ProcessingStatus.PROCESSING,
            ProductImage.ProcessingStatus.RESULT_RECEIVED,
        }:
            return {
                "status": "skipped",
                "reason": "unsupported_processing_status",
            }

        current_status = image.processing_status
        status_url = image.processing_result.get("payload", {}).get("status_url")
        # Polling task is alive: renew its lease.
        image.processing_claimed_at = now
        image.save(update_fields=("processing_claimed_at",))

    if (
        attempt > settings.BULK_WHITE_IMAGE_SERVICE_MAX_POLL_ATTEMPTS
        and current_status != ProductImage.ProcessingStatus.RESULT_RECEIVED
    ):
        mark_failed(error="AI generation timed out.")
        return {"status": "failed", "reason": "timeout"}

    try:
        result = get_generation_results(
            product_id=external_product_id,
            result_url=status_url,
        )
    except (WhiteImageServiceError, ImproperlyConfigured) as error:
        mark_failed(error=str(error))
        return {"status": "failed"}

    if result.get("status") != "completed":
        check_product_image_generation.apply_async(
            args=(image_id, external_product_id, attempt + 1),
            countdown=(settings.BULK_WHITE_IMAGE_SERVICE_POLL_INTERVAL_SECONDS),
        )
        return {
            "status": result.get("status", "waiting"),
            "attempt": attempt,
        }

    images = result.get("images", {})
    required_modes = ("white", "interior", "human")

    if not all(images.get(mode) for mode in required_modes):
        mark_failed(
            error="AI response does not contain all images.",
            result=result,
        )
        return {"status": "failed"}

    # Only one worker may claim the final downloading/saving stage.
    with transaction.atomic():
        image = (
            ProductImage.objects.select_for_update(skip_locked=True)
            .filter(id=image_id)
            .first()
        )

        if image is None:
            return {
                "status": "skipped",
                "reason": "image_is_locked",
            }

        if image.processing_status == ProductImage.ProcessingStatus.SUCCEEDED:
            return {
                "status": "skipped",
                "reason": "already_succeeded",
            }

        if (
            image.processing_status == ProductImage.ProcessingStatus.RESULT_RECEIVED
            and image.processing_claimed_at is not None
            and image.processing_claimed_at > stale_before
        ):
            return {
                "status": "skipped",
                "reason": "finalization_in_progress",
            }

        image.processing_status = ProductImage.ProcessingStatus.RESULT_RECEIVED
        image.processing_result = result
        image.processing_error = ""
        image.processing_claimed_at = timezone.now()
        image.save(
            update_fields=(
                "processing_status",
                "processing_result",
                "processing_error",
                "processing_claimed_at",
            )
        )

    # Safety retry: if this worker dies while downloading, after lease timeout
    # another task will reclaim the finalization.
    check_product_image_generation.apply_async(
        args=(image_id, external_product_id, attempt),
        countdown=settings.IMAGE_PROCESSING_LEASE_SECONDS,
    )

    try:
        for mode in required_modes:
            image_content = download_generated_image(images[mode])

            generated_image, _ = ProductGeneratedImage.objects.get_or_create(
                source_image_id=image_id,
                mode=mode,
            )

            generated_image.image.save(
                f"{mode}-{image_id}.jpg",
                ContentFile(image_content),
                save=True,
            )

            if mode == ProductGeneratedImage.Mode.WHITE:
                with transaction.atomic():
                    locked_image = ProductImage.objects.select_for_update().get(
                        pk=image_id
                    )
                    locked_image.processed_image = generated_image.image
                    locked_image.save(update_fields=("processed_image",))

    except GeneratedImageDownloadError:
        mark_failed(error="Unable to download generated image.")
        return {"status": "failed"}

    with transaction.atomic():
        image = ProductImage.objects.select_for_update().get(pk=image_id)

        if image.processing_status != ProductImage.ProcessingStatus.RESULT_RECEIVED:
            return {
                "status": "skipped",
                "reason": "processing_state_changed",
            }

        image.processing_status = ProductImage.ProcessingStatus.SUCCEEDED
        image.processing_error = ""
        image.processing_result = result
        image.processing_finished_at = timezone.now()
        image.save(
            update_fields=(
                "processing_status",
                "processing_error",
                "processing_result",
                "processing_finished_at",
            )
        )

    return {
        "status": "completed",
        "image_id": image_id,
    }


@shared_task(
    name="apps.notifications.tasks.recover_stale_product_image_processing",
)
def recover_stale_product_image_processing() -> dict[str, int]:
    """
    Recovers image-processing tasks abandoned by a worker crash.

    An image with no external product ID cannot be safely resumed: a new
    generation request could create duplicate paid provider work. It is marked
    failed for a deliberate manager retry instead.
    """
    from apps.products.models import ProductImage

    now = timezone.now()
    stale_before = now - timedelta(seconds=settings.IMAGE_PROCESSING_LEASE_SECONDS)

    candidate_ids = list(
        ProductImage.objects.filter(
            processing_status__in=(
                ProductImage.ProcessingStatus.PROCESSING,
                ProductImage.ProcessingStatus.RESULT_RECEIVED,
            ),
            processing_claimed_at__lt=stale_before,
        )
        .order_by("id")
        .values_list("id", flat=True)[: settings.IMAGE_PROCESSING_RECOVERY_BATCH_SIZE]
    )

    resumed_count = 0
    failed_count = 0
    skipped_count = 0

    for image_id in candidate_ids:
        with transaction.atomic():
            image = (
                ProductImage.objects.select_for_update(skip_locked=True)
                .filter(pk=image_id)
                .first()
            )

            if image is None:
                skipped_count += 1
                continue

            if image.processing_status not in {
                ProductImage.ProcessingStatus.PROCESSING,
                ProductImage.ProcessingStatus.RESULT_RECEIVED,
            }:
                skipped_count += 1
                continue

            if (
                image.processing_claimed_at is not None
                and image.processing_claimed_at >= stale_before
            ):
                skipped_count += 1
                continue

            result = image.processing_result or {}
            payload = result.get("payload", {})

            external_product_id = payload.get("product_id") or result.get("product_id")

            if not external_product_id:
                image.processing_status = ProductImage.ProcessingStatus.FAILED
                image.processing_error = (
                    "Image processing recovery failed: external product ID "
                    "was not saved before the worker stopped."
                )
                image.processing_finished_at = now
                image.save(
                    update_fields=(
                        "processing_status",
                        "processing_error",
                        "processing_finished_at",
                    )
                )
                failed_count += 1
                continue

            # Reserve the image before scheduling its recovery.
            image.processing_claimed_at = now
            image.save(update_fields=("processing_claimed_at",))

        check_product_image_generation.apply_async(
            args=(image_id, int(external_product_id), 1),
        )
        resumed_count += 1

    return {
        "candidates": len(candidate_ids),
        "resumed": resumed_count,
        "failed": failed_count,
        "skipped": skipped_count,
    }
