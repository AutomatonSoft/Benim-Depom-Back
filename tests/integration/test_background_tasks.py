from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.common.safe_image_download import (
    GeneratedImageDownloadError,
)
from apps.notifications.models import (
    DeviceToken,
    Notification,
    PushDelivery,
)
from apps.notifications.services import create_notification
from apps.notifications.tasks import (
    check_product_image_generation,
    process_product_image,
    recover_stale_product_image_processing,
    recover_stale_push_deliveries,
    send_notification_push,
    send_product_availability_reminders,
)
from apps.products.models import Product, ProductGeneratedImage, ProductImage


@pytest.mark.integration
@pytest.mark.django_db
def test_push_task_skips_without_notification_or_firebase(product_factory, seller):
    assert send_notification_push.run(999999) == {
        "status": "skipped",
        "reason": "notification_not_found",
    }
    notification = Notification.objects.create(
        user=seller, notification_type=Notification.Type.MANAGER_MESSAGE
    )
    assert send_notification_push.run(notification.id) == {
        "status": "skipped",
        "reason": "firebase_disabled",
    }


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_notification_service_persists_payload_and_enqueues_push(monkeypatch, seller):
    delay = Mock()
    monkeypatch.setattr(
        "apps.notifications.services.send_notification_push.delay", delay
    )
    notification = create_notification(
        user=seller,
        notification_type=Notification.Type.MESSAGE_RECEIVED,
        title="Message",
        body="Body",
        data={"source": "test"},
    )
    assert notification.data == {"source": "test"}
    delay.assert_called_once_with(notification.id)


@pytest.mark.integration
@pytest.mark.django_db
def test_push_task_sends_once_and_marks_unregistered_token_invalid(
    monkeypatch,
    seller,
):
    notification = Notification.objects.create(
        user=seller,
        notification_type=Notification.Type.MANAGER_MESSAGE,
        title="Title",
        body="Body",
    )
    valid_token = DeviceToken.objects.create(
        user=seller,
        token="valid",
        platform="android",
    )
    expired_token = DeviceToken.objects.create(
        user=seller,
        token="expired",
        platform="ios",
    )

    monkeypatch.setattr(
        "apps.notifications.tasks.get_firebase_app",
        lambda: object(),
    )

    def firebase_send(message, app):
        if message.token == expired_token.token:
            raise __import__("firebase_admin").messaging.UnregisteredError("gone")

    send = Mock(side_effect=firebase_send)
    monkeypatch.setattr("apps.notifications.tasks.messaging.send", send)

    result = send_notification_push.run(notification.id)

    assert result == {
        "status": "completed",
        "sent": 1,
        "invalid": 1,
        "failed": 0,
    }

    valid_delivery = PushDelivery.objects.get(
        notification=notification,
        device_token=valid_token,
    )
    expired_delivery = PushDelivery.objects.get(
        notification=notification,
        device_token=expired_token,
    )

    assert valid_delivery.status == PushDelivery.Status.SENT
    assert valid_delivery.attempt_count == 1
    assert valid_delivery.sent_at is not None

    assert expired_delivery.status == PushDelivery.Status.INVALID
    assert DeviceToken.objects.get(pk=expired_token.pk).is_active is False


@pytest.mark.integration
@pytest.mark.django_db
def test_availability_reminders_include_only_stale_approved_products(
    product_factory, seller
):
    old = product_factory(owner=seller, status=Product.Status.APPROVED, title="Old")
    recent = product_factory(
        owner=seller, status=Product.Status.APPROVED, title="Recent"
    )
    old_time = timezone.now() - timedelta(days=30)
    Product.objects.filter(pk=old.pk).update(approved_at=old_time)
    Product.objects.filter(pk=recent.pk).update(approved_at=timezone.now())

    result = send_product_availability_reminders.run()
    old.refresh_from_db()
    assert result["sent"] == 1
    assert old.availability_reminder_sent_at is not None
    assert Notification.objects.filter(
        product=old, notification_type=Notification.Type.PRODUCT_AVAILABILITY_REMINDER
    ).exists()
    assert not Notification.objects.filter(product=recent).exists()


@pytest.mark.integration
@pytest.mark.django_db
def test_image_processing_queues_external_job_and_handles_service_failure(
    monkeypatch, seller, product_factory, product_image_factory
):
    product = product_factory(owner=seller, status=Product.Status.SUBMITTED)
    image = product_image_factory(product=product)
    image.processing_status = ProductImage.ProcessingStatus.PENDING
    image.save(update_fields=["processing_status"])
    monkeypatch.setattr(
        "apps.common.white_image_service.generate_white_background",
        Mock(return_value={"payload": {"status": "queued", "product_id": 42}}),
    )
    schedule = Mock()
    monkeypatch.setattr(
        "apps.notifications.tasks.check_product_image_generation.apply_async", schedule
    )

    result = process_product_image.run(image.id)
    image.refresh_from_db()
    assert result == {
        "status": "queued",
        "image_id": image.id,
        "external_product_id": 42,
    }
    assert image.processing_status == ProductImage.ProcessingStatus.PROCESSING
    schedule.assert_called_once()

    failed = product_image_factory(
        product=product_factory(owner=seller, status=Product.Status.SUBMITTED)
    )
    failed.processing_status = ProductImage.ProcessingStatus.PENDING
    failed.save(update_fields=["processing_status"])
    monkeypatch.setattr(
        "apps.common.white_image_service.generate_white_background",
        Mock(
            side_effect=__import__(
                "apps.common.white_image_service", fromlist=["WhiteImageServiceError"]
            ).WhiteImageServiceError("down")
        ),
    )
    assert process_product_image.run(failed.id) == {"status": "failed"}
    failed.refresh_from_db()
    assert failed.processing_status == ProductImage.ProcessingStatus.FAILED


@pytest.mark.integration
@pytest.mark.django_db
def test_image_processing_skips_unknown_and_non_pending_and_rejects_bad_payload(
    monkeypatch, seller, product_factory, product_image_factory
):
    assert process_product_image.run(999999) == {
        "status": "skipped",
        "reason": "image_not_found",
    }
    image = product_image_factory(
        product=product_factory(owner=seller, status=Product.Status.SUBMITTED)
    )
    image.processing_status = ProductImage.ProcessingStatus.SUCCEEDED
    image.save(update_fields=["processing_status"])
    assert process_product_image.run(image.id) == {
        "status": "skipped",
        "reason": "not_pending",
    }

    image.processing_status = ProductImage.ProcessingStatus.PENDING
    image.save(update_fields=["processing_status"])
    monkeypatch.setattr(
        "apps.common.white_image_service.generate_white_background",
        Mock(return_value={"payload": {"status": "completed"}}),
    )
    assert process_product_image.run(image.id) == {"status": "failed"}
    image.refresh_from_db()
    assert image.processing_error == "Unexpected AI service response."


@pytest.mark.integration
@pytest.mark.django_db
@override_settings(BULK_WHITE_IMAGE_SERVICE_MAX_POLL_ATTEMPTS=3)
def test_image_poll_reschedules_then_persists_all_generated_images(
    monkeypatch, seller, product_factory, product_image_factory
):
    product = product_factory(owner=seller, status=Product.Status.SUBMITTED)
    image = product_image_factory(product=product)
    image.processing_status = ProductImage.ProcessingStatus.PROCESSING
    image.processing_result = {
        "payload": {"status_url": "https://ai.example/result/42/"}
    }
    image.save(update_fields=["processing_status", "processing_result"])
    schedule = Mock()
    monkeypatch.setattr(
        "apps.notifications.tasks.check_product_image_generation.apply_async", schedule
    )
    monkeypatch.setattr(
        "apps.common.white_image_service.get_generation_results",
        Mock(return_value={"status": "queued"}),
    )
    assert check_product_image_generation.run(image.id, 42, 1) == {
        "status": "queued",
        "attempt": 1,
    }
    schedule.assert_called_once()

    monkeypatch.setattr(
        "apps.common.white_image_service.get_generation_results",
        Mock(
            return_value={
                "status": "completed",
                "images": {
                    "white": "https://ai.example/w.jpg",
                    "interior": "https://ai.example/i.jpg",
                    "human": "https://ai.example/h.jpg",
                },
            }
        ),
    )
    monkeypatch.setattr(
        "apps.common.safe_image_download.download_generated_image",
        Mock(return_value=b"image-bytes"),
    )
    assert check_product_image_generation.run(image.id, 42, 2)["status"] == "completed"
    image.refresh_from_db()
    assert image.processing_status == ProductImage.ProcessingStatus.SUCCEEDED
    assert ProductGeneratedImage.objects.filter(source_image=image).count() == 3


@pytest.mark.integration
@pytest.mark.django_db
@override_settings(BULK_WHITE_IMAGE_SERVICE_MAX_POLL_ATTEMPTS=1)
def test_image_poll_fails_on_timeout_or_incomplete_result(
    monkeypatch, seller, product_factory, product_image_factory
):
    image = product_image_factory(
        product=product_factory(owner=seller, status=Product.Status.SUBMITTED)
    )
    assert check_product_image_generation.run(image.id, 42, 2) == {
        "status": "failed",
        "reason": "timeout",
    }
    image.refresh_from_db()
    assert image.processing_status == ProductImage.ProcessingStatus.FAILED

    image.processing_status = ProductImage.ProcessingStatus.PROCESSING
    image.save(update_fields=["processing_status"])
    monkeypatch.setattr(
        "apps.common.white_image_service.get_generation_results",
        Mock(
            return_value={
                "status": "completed",
                "images": {"white": "https://ai.example/w.jpg"},
            }
        ),
    )
    assert check_product_image_generation.run(image.id, 42, 1) == {"status": "failed"}


@pytest.mark.integration
@pytest.mark.django_db
def test_image_poll_handles_missing_image_external_error_and_download_error(
    monkeypatch, seller, product_factory, product_image_factory
):
    assert check_product_image_generation.run(999999, 42, 1) == {
        "status": "skipped",
        "reason": "image_not_found",
    }
    image = product_image_factory(
        product=product_factory(owner=seller, status=Product.Status.SUBMITTED)
    )
    monkeypatch.setattr(
        "apps.common.white_image_service.get_generation_results",
        Mock(
            side_effect=__import__(
                "apps.common.white_image_service", fromlist=["WhiteImageServiceError"]
            ).WhiteImageServiceError("remote down")
        ),
    )
    assert check_product_image_generation.run(image.id, 42, 1) == {"status": "failed"}
    image.refresh_from_db()
    assert image.processing_status == ProductImage.ProcessingStatus.FAILED

    image.processing_status = ProductImage.ProcessingStatus.PROCESSING
    image.save(update_fields=["processing_status"])
    monkeypatch.setattr(
        "apps.common.white_image_service.get_generation_results",
        Mock(
            return_value={
                "status": "completed",
                "images": {
                    "white": "https://ai.example/w.jpg",
                    "interior": "https://ai.example/i.jpg",
                    "human": "https://ai.example/h.jpg",
                },
            }
        ),
    )
    monkeypatch.setattr(
        "apps.common.safe_image_download.download_generated_image",
        Mock(side_effect=GeneratedImageDownloadError("download failed")),
    )
    assert check_product_image_generation.run(image.id, 42, 1) == {"status": "failed"}
    image.refresh_from_db()
    assert image.processing_error == "Unable to download generated image."


@pytest.mark.integration
@pytest.mark.django_db
def test_second_push_attempt_does_not_resend_successful_delivery(
    monkeypatch,
    seller,
):
    notification = Notification.objects.create(
        user=seller,
        notification_type=Notification.Type.MANAGER_MESSAGE,
    )
    DeviceToken.objects.create(
        user=seller,
        token="only-valid-token",
        platform="android",
    )

    monkeypatch.setattr(
        "apps.notifications.tasks.get_firebase_app",
        lambda: object(),
    )
    send = Mock()
    monkeypatch.setattr("apps.notifications.tasks.messaging.send", send)

    first = send_notification_push.run(notification.id)
    assert first["sent"] == 1
    send.assert_called_once()

    send.reset_mock()
    second = send_notification_push.run(notification.id)

    assert second == {
        "status": "completed",
        "sent": 0,
        "invalid": 0,
        "failed": 0,
    }
    send.assert_not_called()


@pytest.mark.integration
@pytest.mark.django_db
def test_temporary_push_error_keeps_delivery_pending_and_requests_retry(
    monkeypatch,
    seller,
):
    from celery.exceptions import Retry

    notification = Notification.objects.create(
        user=seller,
        notification_type=Notification.Type.MANAGER_MESSAGE,
    )
    device_token = DeviceToken.objects.create(
        user=seller,
        token="temporarily-unavailable",
        platform="android",
    )

    monkeypatch.setattr(
        "apps.notifications.tasks.get_firebase_app",
        lambda: object(),
    )
    monkeypatch.setattr(
        "apps.notifications.tasks.messaging.send",
        Mock(side_effect=RuntimeError("Firebase temporarily unavailable")),
    )

    retry = Mock(side_effect=Retry())
    monkeypatch.setattr(send_notification_push, "retry", retry)

    with pytest.raises(Retry):
        send_notification_push.run(notification.id)

    delivery = PushDelivery.objects.get(
        notification=notification,
        device_token=device_token,
    )
    assert delivery.status == PushDelivery.Status.PENDING
    assert delivery.attempt_count == 1
    assert "RuntimeError" in delivery.last_error
    retry.assert_called_once()


@pytest.mark.integration
@pytest.mark.django_db
@override_settings(
    IMAGE_PROCESSING_LEASE_SECONDS=600,
    IMAGE_PROCESSING_RECOVERY_BATCH_SIZE=100,
)
def test_image_recovery_resumes_known_generation_and_fails_unknown_one(
    monkeypatch,
    seller,
    product_factory,
    product_image_factory,
):
    old_time = timezone.now() - timedelta(seconds=601)

    resumable = product_image_factory(
        product=product_factory(
            owner=seller,
            status=Product.Status.SUBMITTED,
        )
    )
    resumable.processing_status = ProductImage.ProcessingStatus.PROCESSING
    resumable.processing_claimed_at = old_time
    resumable.processing_result = {
        "payload": {
            "product_id": 42,
            "status_url": "https://ai.example/result/42/",
        }
    }
    resumable.save(
        update_fields=(
            "processing_status",
            "processing_claimed_at",
            "processing_result",
        )
    )

    missing_external_id = product_image_factory(
        product=product_factory(
            owner=seller,
            status=Product.Status.SUBMITTED,
        )
    )
    missing_external_id.processing_status = ProductImage.ProcessingStatus.PROCESSING
    missing_external_id.processing_claimed_at = old_time
    missing_external_id.save(
        update_fields=(
            "processing_status",
            "processing_claimed_at",
        )
    )

    fresh = product_image_factory(
        product=product_factory(
            owner=seller,
            status=Product.Status.SUBMITTED,
        )
    )
    fresh.processing_status = ProductImage.ProcessingStatus.PROCESSING
    fresh.processing_claimed_at = timezone.now()
    fresh.processing_result = {"payload": {"product_id": 99}}
    fresh.save(
        update_fields=(
            "processing_status",
            "processing_claimed_at",
            "processing_result",
        )
    )

    schedule = Mock()
    monkeypatch.setattr(
        "apps.notifications.tasks.check_product_image_generation.apply_async",
        schedule,
    )

    result = recover_stale_product_image_processing.run()

    resumable.refresh_from_db()
    missing_external_id.refresh_from_db()
    fresh.refresh_from_db()

    assert result == {
        "candidates": 2,
        "resumed": 1,
        "failed": 1,
        "skipped": 0,
    }

    assert resumable.processing_status == ProductImage.ProcessingStatus.PROCESSING
    assert resumable.processing_claimed_at > old_time

    schedule.assert_called_once_with(
        args=(resumable.id, 42, 1),
    )

    assert missing_external_id.processing_status == ProductImage.ProcessingStatus.FAILED
    assert "external product ID" in missing_external_id.processing_error
    assert missing_external_id.processing_finished_at is not None

    assert fresh.processing_status == ProductImage.ProcessingStatus.PROCESSING
    schedule.assert_called_once()


@pytest.mark.integration
@pytest.mark.django_db
@override_settings(
    PUSH_DELIVERY_PROCESSING_LEASE_SECONDS=120,
    PUSH_DELIVERY_RECOVERY_BATCH_SIZE=100,
)
def test_push_recovery_resets_only_stale_processing_deliveries(
    monkeypatch,
    seller,
):
    notification = Notification.objects.create(
        user=seller,
        notification_type=Notification.Type.MANAGER_MESSAGE,
    )
    old_time = timezone.now() - timedelta(seconds=121)

    stale_token_one = DeviceToken.objects.create(
        user=seller,
        token="stale-push-one",
        platform="android",
    )
    stale_token_two = DeviceToken.objects.create(
        user=seller,
        token="stale-push-two",
        platform="ios",
    )
    fresh_token = DeviceToken.objects.create(
        user=seller,
        token="fresh-push",
        platform="android",
    )
    sent_token = DeviceToken.objects.create(
        user=seller,
        token="sent-push",
        platform="ios",
    )

    stale_one = PushDelivery.objects.create(
        notification=notification,
        device_token=stale_token_one,
        status=PushDelivery.Status.PROCESSING,
        last_attempt_at=old_time,
    )
    stale_two = PushDelivery.objects.create(
        notification=notification,
        device_token=stale_token_two,
        status=PushDelivery.Status.PROCESSING,
        last_attempt_at=old_time,
    )
    fresh = PushDelivery.objects.create(
        notification=notification,
        device_token=fresh_token,
        status=PushDelivery.Status.PROCESSING,
        last_attempt_at=timezone.now(),
    )
    sent = PushDelivery.objects.create(
        notification=notification,
        device_token=sent_token,
        status=PushDelivery.Status.SENT,
        sent_at=timezone.now(),
    )

    schedule = Mock()
    monkeypatch.setattr(
        "apps.notifications.tasks.send_notification_push.delay",
        schedule,
    )

    # В тестовой транзакции выполняем on_commit сразу.
    monkeypatch.setattr(
        "apps.notifications.tasks.transaction.on_commit",
        lambda callback: callback(),
    )

    result = recover_stale_push_deliveries.run()

    stale_one.refresh_from_db()
    stale_two.refresh_from_db()
    fresh.refresh_from_db()
    sent.refresh_from_db()

    assert result == {
        "candidate_notifications": 1,
        "recovered_deliveries": 2,
        "scheduled_notifications": 1,
        "skipped_notifications": 0,
    }

    assert stale_one.status == PushDelivery.Status.PENDING
    assert stale_two.status == PushDelivery.Status.PENDING
    assert "lease expired" in stale_one.last_error

    assert fresh.status == PushDelivery.Status.PROCESSING
    assert sent.status == PushDelivery.Status.SENT

    schedule.assert_called_once_with(notification.id)
