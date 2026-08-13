from datetime import timedelta
from unittest.mock import Mock

import pytest
import requests
from django.test import override_settings
from django.utils import timezone

from apps.notifications.models import DeviceToken, Notification
from apps.notifications.services import create_notification
from apps.notifications.tasks import (
    check_product_image_generation,
    process_product_image,
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
    monkeypatch.setattr("apps.notifications.services.send_notification_push.delay", delay)
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
def test_push_task_sends_and_deactivates_unregistered_token(monkeypatch, seller):
    notification = Notification.objects.create(
        user=seller, notification_type=Notification.Type.MANAGER_MESSAGE, title="Title", body="Body"
    )
    DeviceToken.objects.create(user=seller, token="valid", platform="android")
    DeviceToken.objects.create(user=seller, token="expired", platform="ios")
    monkeypatch.setattr("apps.notifications.tasks.get_firebase_app", lambda: object())
    send = Mock(side_effect=[None, __import__("firebase_admin").messaging.UnregisteredError("gone")])
    monkeypatch.setattr("apps.notifications.tasks.messaging.send", send)

    result = send_notification_push.run(notification.id)
    assert result == {"status": "completed", "sent": 1, "deactivated": 1, "failed": 0}
    assert DeviceToken.objects.filter(user=seller, is_active=False).count() == 1


@pytest.mark.integration
@pytest.mark.django_db
def test_availability_reminders_include_only_stale_approved_products(product_factory, seller):
    old = product_factory(owner=seller, status=Product.Status.APPROVED, title="Old")
    recent = product_factory(owner=seller, status=Product.Status.APPROVED, title="Recent")
    old_time = timezone.now() - timedelta(days=30)
    Product.objects.filter(pk=old.pk).update(approved_at=old_time)
    Product.objects.filter(pk=recent.pk).update(approved_at=timezone.now())

    result = send_product_availability_reminders.run()
    old.refresh_from_db()
    assert result["sent"] == 1
    assert old.availability_reminder_sent_at is not None
    assert Notification.objects.filter(product=old, notification_type=Notification.Type.PRODUCT_AVAILABILITY_REMINDER).exists()
    assert not Notification.objects.filter(product=recent).exists()


@pytest.mark.integration
@pytest.mark.django_db
def test_image_processing_queues_external_job_and_handles_service_failure(
    monkeypatch, seller, product_factory, product_image_factory
):
    product = product_factory(owner=seller, status=Product.Status.SUBMITTED)
    image = product_image_factory(product=product)
    monkeypatch.setattr(
        "apps.common.white_image_service.generate_white_background",
        Mock(return_value={"payload": {"status": "queued", "product_id": 42}}),
    )
    schedule = Mock()
    monkeypatch.setattr("apps.notifications.tasks.check_product_image_generation.apply_async", schedule)

    result = process_product_image.run(image.id)
    image.refresh_from_db()
    assert result == {"status": "queued", "image_id": image.id, "external_product_id": 42}
    assert image.processing_status == ProductImage.ProcessingStatus.PROCESSING
    schedule.assert_called_once()

    failed = product_image_factory(
        product=product_factory(owner=seller, status=Product.Status.SUBMITTED)
    )
    monkeypatch.setattr(
        "apps.common.white_image_service.generate_white_background",
        Mock(side_effect=__import__("apps.common.white_image_service", fromlist=["WhiteImageServiceError"]).WhiteImageServiceError("down")),
    )
    assert process_product_image.run(failed.id) == {"status": "failed"}
    failed.refresh_from_db()
    assert failed.processing_status == ProductImage.ProcessingStatus.FAILED


@pytest.mark.integration
@pytest.mark.django_db
def test_image_processing_skips_unknown_and_non_pending_and_rejects_bad_payload(
    monkeypatch, seller, product_factory, product_image_factory
):
    assert process_product_image.run(999999) == {"status": "skipped", "reason": "image_not_found"}
    image = product_image_factory(product=product_factory(owner=seller, status=Product.Status.SUBMITTED))
    image.processing_status = ProductImage.ProcessingStatus.SUCCEEDED
    image.save(update_fields=["processing_status"])
    assert process_product_image.run(image.id) == {"status": "skipped", "reason": "not_pending"}

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
    image.processing_result = {"payload": {"status_url": "https://ai.example/result/42/"}}
    image.save(update_fields=["processing_status", "processing_result"])
    schedule = Mock()
    monkeypatch.setattr("apps.notifications.tasks.check_product_image_generation.apply_async", schedule)
    monkeypatch.setattr(
        "apps.common.white_image_service.get_generation_results",
        Mock(return_value={"status": "queued"}),
    )
    assert check_product_image_generation.run(image.id, 42, 1) == {"status": "queued", "attempt": 1}
    schedule.assert_called_once()

    monkeypatch.setattr(
        "apps.common.white_image_service.get_generation_results",
        Mock(return_value={
            "status": "completed",
            "images": {"white": "https://ai.example/w.jpg", "interior": "https://ai.example/i.jpg", "human": "https://ai.example/h.jpg"},
        }),
    )
    response = Mock(content=b"image-bytes")
    response.raise_for_status.return_value = None
    monkeypatch.setattr(requests, "get", Mock(return_value=response))
    assert check_product_image_generation.run(image.id, 42, 2)["status"] == "completed"
    image.refresh_from_db()
    assert image.processing_status == ProductImage.ProcessingStatus.SUCCEEDED
    assert ProductGeneratedImage.objects.filter(source_image=image).count() == 3


@pytest.mark.integration
@pytest.mark.django_db
@override_settings(BULK_WHITE_IMAGE_SERVICE_MAX_POLL_ATTEMPTS=1)
def test_image_poll_fails_on_timeout_or_incomplete_result(monkeypatch, seller, product_factory, product_image_factory):
    image = product_image_factory(product=product_factory(owner=seller, status=Product.Status.SUBMITTED))
    assert check_product_image_generation.run(image.id, 42, 2) == {"status": "failed", "reason": "timeout"}
    image.refresh_from_db()
    assert image.processing_status == ProductImage.ProcessingStatus.FAILED

    image.processing_status = ProductImage.ProcessingStatus.PROCESSING
    image.save(update_fields=["processing_status"])
    monkeypatch.setattr(
        "apps.common.white_image_service.get_generation_results",
        Mock(return_value={"status": "completed", "images": {"white": "https://ai.example/w.jpg"}}),
    )
    assert check_product_image_generation.run(image.id, 42, 1) == {"status": "failed"}


@pytest.mark.integration
@pytest.mark.django_db
def test_image_poll_handles_missing_image_external_error_and_download_error(
    monkeypatch, seller, product_factory, product_image_factory
):
    assert check_product_image_generation.run(999999, 42, 1) == {"status": "skipped", "reason": "image_not_found"}
    image = product_image_factory(product=product_factory(owner=seller, status=Product.Status.SUBMITTED))
    monkeypatch.setattr(
        "apps.common.white_image_service.get_generation_results",
        Mock(side_effect=__import__("apps.common.white_image_service", fromlist=["WhiteImageServiceError"]).WhiteImageServiceError("remote down")),
    )
    assert check_product_image_generation.run(image.id, 42, 1) == {"status": "failed"}
    image.refresh_from_db()
    assert image.processing_status == ProductImage.ProcessingStatus.FAILED

    image.processing_status = ProductImage.ProcessingStatus.PROCESSING
    image.save(update_fields=["processing_status"])
    monkeypatch.setattr(
        "apps.common.white_image_service.get_generation_results",
        Mock(return_value={"status": "completed", "images": {
            "white": "https://ai.example/w.jpg", "interior": "https://ai.example/i.jpg", "human": "https://ai.example/h.jpg",
        }}),
    )
    monkeypatch.setattr(requests, "get", Mock(side_effect=requests.RequestException("download failed")))
    assert check_product_image_generation.run(image.id, 42, 1) == {"status": "failed"}
    image.refresh_from_db()
    assert image.processing_error == "Unable to download generated image."
