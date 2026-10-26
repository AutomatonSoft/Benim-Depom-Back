import os
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest
from django.core.files.storage import default_storage
from django.utils import timezone

from apps.notifications.models import Notification
from apps.products.media_uploads import (
    cleanup_local_photos,
    enqueue_photo_upload,
    local_photo_storage,
    recover_photo_uploads,
    staged_photo_url,
    transfer_photos,
)
from apps.products.models import Product, ProductImage, ProductMediaUpload
from apps.products.services import upload_product_image
from tests.integration.test_products_api import multipart_product_payload

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


@pytest.fixture
def create_pending(api_client, seller, product_type, image_file):
    api_client.force_authenticate(seller)

    def create(count=2, **headers):
        payload = multipart_product_payload(product_type, image_file)
        payload["images"] = [image_file(f"photo-{i}.png") for i in range(count)]
        with patch("apps.products.media_uploads.enqueue_photo_upload"):
            response = api_client.post(
                "/api/v1/products/", payload, format="multipart", **headers
            )
        assert response.status_code == 201, response.data
        return Product.objects.get(pk=response.data["id"]), response

    return create


def test_creation_stages_twenty_photos_without_any_ftp(create_pending, manager):
    with patch.object(
        default_storage, "save", side_effect=AssertionError("FTP in request")
    ):
        product, response = create_pending(20)
    assert product.status == Product.Status.SUBMITTED
    assert response.data["status"] == Product.Status.SUBMITTED
    assert "image_upload_status" not in response.data
    assert len(response.data["images"]) == 20
    assert [image["is_primary"] for image in response.data["images"]] == [True] + [
        False
    ] * 19
    assert all(
        local_photo_storage().exists(image.local_source)
        for image in product.images.all()
    )
    assert not Notification.objects.filter(product=product).exists()


@pytest.mark.django_db(transaction=True)
def test_local_photos_are_immediately_available_and_signed(create_pending, client):
    product, response = create_pending()
    image = product.images.first()
    for field in ("image", "thumbnail"):
        url = urlsplit(response.data["images"][0][field]).path
        shown = client.get(url)
        assert shown.status_code == 200
        assert shown["Content-Type"] == "image/png"
        assert b"".join(shown.streaming_content).startswith(b"\x89PNG")
        shown.close()
    url = staged_photo_url(image, "original")
    assert client.get(url.replace("/original/", "/thumbnail/")).status_code == 404
    assert client.get(url + "wrong/").status_code == 404


def test_local_proxy_photo_urls_use_the_browser_host(create_pending, settings):
    settings.USE_X_FORWARDED_HOST = True
    settings.ALLOWED_HOSTS = ["localhost", "192.168.0.1"]
    _, response = create_pending(
        HTTP_HOST="192.168.0.1:8000", HTTP_X_FORWARDED_HOST="localhost:3002"
    )
    for image in response.data["images"]:
        for field in ("image", "thumbnail"):
            assert image[field].startswith(
                "http://localhost:3002/api/v1/products/staged-photo/"
            )


def test_transfer_preserves_submission_and_is_idempotent(
    create_pending, manager, django_capture_on_commit_callbacks
):
    product, _ = create_pending()
    notifications = Notification.objects.filter(product=product).count()
    with django_capture_on_commit_callbacks(execute=True):
        transfer_photos(product.pk)
    product.refresh_from_db()
    assert product.status == Product.Status.SUBMITTED
    assert product.media_upload.status == ProductMediaUpload.Status.SUCCEEDED
    assert product.images.exclude(uploaded_at=None).count() == 2
    for image in product.images.all():
        assert default_storage.exists(image.image.name)
        assert default_storage.exists(image.thumbnail.name)
    assert Notification.objects.filter(product=product).count() == notifications
    transfer_photos(product.pk)
    assert Notification.objects.filter(product=product).count() == notifications
    assert ProductMediaUpload.objects.get(product=product).attempts == 1


def test_edit_stages_new_photo_without_ftp_and_preserves_approved_product(
    api_client, seller, product_factory, product_image_factory, image_file
):
    product = product_factory(owner=seller, status=Product.Status.APPROVED)
    existing = product_image_factory(product=product)
    api_client.force_authenticate(seller)

    with patch.object(
        default_storage, "save", side_effect=AssertionError("FTP in request")
    ):
        response = api_client.post(
            f"/api/v1/products/{product.pk}/images/",
            {"image": image_file("added.png")},
            format="multipart",
        )

    assert response.status_code == 201, response.data
    added = ProductImage.objects.get(pk=response.data["id"])
    assert added.local_source and local_photo_storage().exists(added.local_source)
    assert "/staged-photo/" in response.data["image"]
    assert product.images.count() == 2
    assert product.media_upload.status == ProductMediaUpload.Status.PENDING
    product.refresh_from_db()
    assert product.status == Product.Status.APPROVED

    with patch.object(default_storage, "save", wraps=default_storage.save) as save:
        transfer_photos(product.pk)
    assert save.call_count == 2
    existing.refresh_from_db()
    assert existing.local_source == ""
    assert product.media_upload.status == ProductMediaUpload.Status.SUCCEEDED


def test_edit_reopens_completed_photo_job_without_reuploading_existing_images(
    create_pending, image_file
):
    product, _ = create_pending(count=1)
    transfer_photos(product.pk)
    original = product.images.get()
    Product.objects.filter(pk=product.pk).update(status=Product.Status.WITHDRAWN)
    product.refresh_from_db()

    added = upload_product_image(
        product=product, image_file=image_file("later.png"), is_primary=False
    )
    assert (
        ProductMediaUpload.objects.get(product=product).status
        == ProductMediaUpload.Status.PENDING
    )
    with patch.object(default_storage, "save", wraps=default_storage.save) as save:
        transfer_photos(product.pk)
    assert save.call_count == 2
    original.refresh_from_db()
    added.refresh_from_db()
    assert original.uploaded_at and added.uploaded_at
    assert (
        ProductMediaUpload.objects.get(product=product).status
        == ProductMediaUpload.Status.SUCCEEDED
    )


def test_photo_added_during_transfer_gets_another_batch(create_pending, image_file):
    product, _ = create_pending(count=1)
    original = product.images.get()
    Product.objects.filter(pk=product.pk).update(status=Product.Status.WITHDRAWN)
    product.refresh_from_db()
    save = default_storage.save
    added = None

    def append_during_ftp(name, file, **kwargs):
        nonlocal added
        if added is None:
            added = upload_product_image(
                product=product,
                image_file=image_file("during.png"),
                is_primary=False,
            )
        return save(name, file, **kwargs)

    with patch.object(default_storage, "save", side_effect=append_during_ftp):
        transfer_photos(product.pk)
    original.refresh_from_db()
    added.refresh_from_db()
    assert original.uploaded_at and added.uploaded_at is None
    assert (
        ProductMediaUpload.objects.get(product=product).status
        == ProductMediaUpload.Status.PENDING
    )

    with patch.object(default_storage, "save", wraps=save) as next_batch:
        transfer_photos(product.pk)
    assert next_batch.call_count == 2
    added.refresh_from_db()
    assert added.uploaded_at
    assert (
        ProductMediaUpload.objects.get(product=product).status
        == ProductMediaUpload.Status.SUCCEEDED
    )


def test_failure_keeps_sources_and_retry_resumes_without_duplicate_uploads(
    create_pending,
):
    product, _ = create_pending()
    save = default_storage.save
    calls = []

    def interrupted(name, file, **kwargs):
        calls.append(name)
        if len(calls) == 3:
            raise OSError("FTP unavailable")
        return save(name, file, **kwargs)

    with (
        patch.object(default_storage, "save", side_effect=interrupted),
        pytest.raises(OSError),
    ):
        transfer_photos(product.pk)
    job = ProductMediaUpload.objects.get(product=product)
    assert job.status == job.Status.FAILED
    product.refresh_from_db()
    assert product.status == Product.Status.SUBMITTED
    assert product.images.exclude(uploaded_at=None).count() == 1
    assert all(
        local_photo_storage().exists(image.local_source)
        for image in product.images.all()
    )
    job.available_at = timezone.now() - timedelta(seconds=1)
    job.save(update_fields=["available_at"])
    with patch.object(default_storage, "save", wraps=save) as resumed:
        transfer_photos(product.pk)
    assert resumed.call_count == 2
    product.refresh_from_db()
    assert product.status == Product.Status.SUBMITTED


def test_live_lease_does_not_start_another_transfer(create_pending):
    product, _ = create_pending()
    ProductMediaUpload.objects.filter(product=product).update(
        status="processing", claimed_at=timezone.now()
    )
    with patch.object(
        default_storage, "save", side_effect=AssertionError("duplicate worker")
    ):
        transfer_photos(product.pk)
    assert product.images.filter(uploaded_at=None).count() == 2


def test_broker_outage_keeps_a_recoverable_job(
    create_pending, django_capture_on_commit_callbacks
):
    with (
        patch(
            "apps.products.media_tasks.transfer_product_photos.apply_async",
            side_effect=OSError("broker"),
        ),
        django_capture_on_commit_callbacks(execute=True),
    ):
        product, response = create_pending()
    assert response.status_code == 201
    assert ProductMediaUpload.objects.filter(product=product, status="pending").exists()
    with patch("apps.products.media_uploads.enqueue_photo_upload") as enqueue:
        recover_photo_uploads()
    enqueue.assert_called_once_with(product.pk)


def test_last_interrupted_attempt_is_visible_and_owner_can_retry(
    create_pending, api_client, second_seller
):
    product, _ = create_pending()
    ProductMediaUpload.objects.filter(product=product).update(
        status="processing", claimed_at=timezone.now() - timedelta(hours=1), attempts=5
    )
    recover_photo_uploads()
    assert ProductMediaUpload.objects.get(product=product).status == "failed"
    url = f"/api/v1/products/{product.pk}/photo-upload/retry/"
    api_client.force_authenticate(second_seller)
    assert api_client.post(url, {}, format="json").status_code == 404
    api_client.force_authenticate(product.owner)
    assert api_client.post(url, {}, format="json").status_code == 202
    job = ProductMediaUpload.objects.get(product=product)
    assert job.status == "pending" and job.attempts == 0


def test_pending_transfer_does_not_block_withdraw_edit_and_resubmit(
    create_pending, api_client
):
    product, _ = create_pending()
    url = f"/api/v1/products/{product.pk}/"
    assert api_client.post(url + "withdraw/", {}, format="json").status_code == 204
    edited = api_client.patch(url, {"title": "Changed"}, format="json")
    assert edited.status_code == 200, edited.data
    assert (
        api_client.patch(
            f"/api/v1/products/{product.pk}/",
            {"resubmit_for_moderation": True},
            format="json",
        ).status_code
        == 200
    )
    transfer_photos(product.pk)
    product.refresh_from_db()
    assert product.title == "Changed" and product.status == Product.Status.SUBMITTED


def test_cleanup_retains_pending_and_recent_files_and_old_links_work(
    create_pending, client
):
    pending, _ = create_pending()
    ready, response = create_pending()
    transfer_photos(ready.pk)
    assert cleanup_local_photos() == 0
    ProductMediaUpload.objects.filter(product=ready).update(
        finished_at=timezone.now() - timedelta(hours=25)
    )
    assert cleanup_local_photos() == 2
    assert all(
        local_photo_storage().exists(image.local_source)
        for image in pending.images.all()
    )
    ready.refresh_from_db()
    assert all(not image.local_source for image in ready.images.all())
    url = urlsplit(response.data["images"][0]["image"]).path
    assert client.get(url).status_code == 302


def test_creation_rollback_removes_sources(create_pending, settings):
    with (
        patch(
            "apps.products.media_uploads.ProductMediaUpload.objects.create",
            side_effect=RuntimeError("DB failure"),
        ),
        pytest.raises(RuntimeError),
    ):
        create_pending()
    assert Product.objects.count() == 0
    assert not list(Path(settings.PRODUCT_IMAGE_STAGING_ROOT).rglob("source.*"))


@pytest.mark.django_db(transaction=True)
def test_notification_failure_after_commit_keeps_photo_sources(create_pending, manager):
    with (
        patch(
            "apps.moderation.services.create_notification",
            side_effect=RuntimeError("notification"),
        ),
        pytest.raises(RuntimeError),
    ):
        create_pending()
    product = Product.objects.get()
    assert product.status == Product.Status.SUBMITTED
    assert all(
        local_photo_storage().exists(image.local_source)
        for image in product.images.all()
    )


def test_publishing_reserves_a_job_once_and_recovers_lost_messages(create_pending):
    product, _ = create_pending()
    with patch(
        "apps.products.media_tasks.transfer_product_photos.apply_async"
    ) as publish:
        enqueue_photo_upload(product.pk)
        enqueue_photo_upload(product.pk)
        recover_photo_uploads()
        assert publish.call_count == 1
        ProductMediaUpload.objects.filter(product=product).update(
            queued_at=timezone.now() - timedelta(hours=1)
        )
        recover_photo_uploads()
        assert publish.call_count == 2


def test_broker_failure_releases_dispatch_reservation(create_pending):
    product, _ = create_pending()
    with patch(
        "apps.products.media_tasks.transfer_product_photos.apply_async",
        side_effect=OSError("broker"),
    ):
        enqueue_photo_upload(product.pk)
    assert ProductMediaUpload.objects.get(product=product).queued_at is None


def test_interrupted_worker_can_be_reclaimed(create_pending):
    product, _ = create_pending()
    ProductMediaUpload.objects.filter(product=product).update(
        status="processing", claimed_at=timezone.now() - timedelta(hours=1), attempts=1
    )
    transfer_photos(product.pk)
    job = ProductMediaUpload.objects.get(product=product)
    assert job.status == "succeeded" and job.attempts == 2


def test_cleanup_keeps_failed_sources_and_removes_only_old_orphans(
    create_pending, settings
):
    product, _ = create_pending()
    ProductMediaUpload.objects.filter(product=product).update(
        status="failed", attempts=5
    )
    root = Path(settings.PRODUCT_IMAGE_STAGING_ROOT)
    old = (timezone.now() - timedelta(days=2)).timestamp()
    for image in product.images.all():
        os.utime(root / Path(image.local_source).parent, (old, old))
    orphan = root / ("a" * 32)
    orphan.mkdir()
    (orphan / "source.jpg").write_bytes(b"orphan")
    os.utime(orphan, (old, old))
    young = root / ("b" * 32)
    young.mkdir()
    assert cleanup_local_photos() == 1
    assert not orphan.exists() and young.exists()
    assert all(
        local_photo_storage().exists(image.local_source)
        for image in product.images.all()
    )


def test_failed_upload_can_be_deleted_without_retrying_it(create_pending, api_client):
    product, _ = create_pending()
    assert (
        api_client.post(
            f"/api/v1/products/{product.pk}/withdraw/", {}, format="json"
        ).status_code
        == 204
    )
    ProductMediaUpload.objects.filter(product=product).update(
        status="failed", attempts=5
    )
    assert api_client.delete(f"/api/v1/products/{product.pk}/").status_code == 204
    job = ProductMediaUpload.objects.get(product=product)
    assert job.status == "cancelled"
    with patch("apps.products.media_uploads.enqueue_photo_upload") as enqueue:
        recover_photo_uploads()
    enqueue.assert_not_called()


@pytest.mark.parametrize(
    "status",
    [Product.Status.APPROVED, Product.Status.REJECTED, Product.Status.WITHDRAWN],
)
def test_transfer_never_overwrites_moderation_status(create_pending, status):
    product, _ = create_pending()
    Product.objects.filter(pk=product.pk).update(status=status)
    transfer_photos(product.pk)
    product.refresh_from_db()
    assert product.status == status
    assert product.media_upload.status == ProductMediaUpload.Status.SUCCEEDED


def test_submission_notification_precedes_ftp(
    create_pending, manager, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        product, _ = create_pending()
    notifications = Notification.objects.filter(product=product).count()
    assert notifications > 0
    transfer_photos(product.pk)
    assert Notification.objects.filter(product=product).count() == notifications


def test_deleted_photo_is_not_left_on_ftp_after_transfer(create_pending):
    product, _ = create_pending()
    image = product.images.first()
    save = default_storage.save

    def delete_while_uploading(name, file, **kwargs):
        product.images.filter(pk=image.pk).delete()
        return save(name, file, **kwargs)

    with patch.object(default_storage, "save", side_effect=delete_while_uploading):
        transfer_photos(product.pk)
    assert not default_storage.exists(image.image.name)
    assert not default_storage.exists(image.thumbnail.name)
    product.refresh_from_db()
    assert product.status == Product.Status.SUBMITTED
