import logging
import os
import shutil
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.core import signing
from django.core.files.storage import FileSystemStorage, default_storage
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from rest_framework.exceptions import APIException, ValidationError

from .image_optimization import make_product_image_variants
from .models import Product, ProductImage, ProductMediaUpload

logger = logging.getLogger(__name__)
SIGNING_SALT = "products.staged-photo"
LEASE_SECONDS = 660
MAX_ATTEMPTS = 5


class PhotoStorageUnavailable(APIException):
    status_code = 503
    default_detail = "Photo storage is temporarily full. Please try again later."


def local_photo_storage():
    return FileSystemStorage(location=settings.PRODUCT_IMAGE_STAGING_ROOT)


def stage_photo_source(image_file):
    """Persist the original locally so the response does not wait for FTP."""
    storage = local_photo_storage()
    Path(storage.location).mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(storage.location).free < image_file.size + 100 * 1024 * 1024:
        raise PhotoStorageUnavailable()
    image_file.seek(0)
    with Image.open(image_file) as parsed:
        extension = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}[parsed.format]
    image_file.seek(0)
    return storage.save(f"{uuid4().hex}/source{extension}", image_file)


def stage_product_image(*, product, image_file, position, is_primary):
    """Append a local photo and reuse the product's durable transfer job."""
    from .models import product_image_upload_to

    name = stage_photo_source(image_file)
    saved = [name]
    try:
        with transaction.atomic():
            job, _ = ProductMediaUpload.objects.select_for_update().get_or_create(
                product=product
            )
            image = ProductImage(
                product=product,
                local_source=name,
                position=position,
                is_primary=is_primary,
            )
            image.image = product_image_upload_to(image, "photo.jpg")
            image.thumbnail = product_image_upload_to(image, "thumbnail.jpg")
            image.save()
            transaction.on_commit(saved.clear)
            if job.status != job.Status.PROCESSING:
                if job.status != job.Status.PENDING:
                    job.queued_at = None
                job.status = job.Status.PENDING
                job.attempts = 0
                job.available_at = timezone.now()
                job.finished_at = None
                job.error = ""
                job.save(
                    update_fields=[
                        "status",
                        "attempts",
                        "available_at",
                        "finished_at",
                        "error",
                        "queued_at",
                    ]
                )
                transaction.on_commit(
                    lambda: enqueue_photo_upload(product.pk), robust=True
                )
        return image
    except Exception:
        for name in saved:
            local_photo_storage().delete(name)
        raise


def staged_photo_url(image, kind):
    signature = signing.Signer(salt=SIGNING_SALT).signature(f"{image.pk}:{kind}")
    return reverse("products:staged-photo", args=[image.pk, kind, signature])


def enqueue_photo_upload(product_id):
    """Publishing may fail; the durable job is recovered by the periodic sweep."""
    from .media_tasks import transfer_product_photos

    now = timezone.now()
    claimed = (
        ProductMediaUpload.objects.filter(product_id=product_id)
        .filter(
            Q(queued_at=None) | Q(queued_at__lt=now - timedelta(seconds=LEASE_SECONDS))
        )
        .exclude(
            status__in=[
                ProductMediaUpload.Status.SUCCEEDED,
                ProductMediaUpload.Status.CANCELLED,
            ]
        )
        .update(queued_at=now)
    )
    if not claimed:
        return
    try:
        transfer_product_photos.apply_async(args=[product_id], retry=False)
    except Exception:
        ProductMediaUpload.objects.filter(product_id=product_id, queued_at=now).update(
            queued_at=None
        )
        logger.exception(
            "Could not enqueue product photo transfer (product=%s)", product_id
        )


def stage_product_photos(*, owner, data, variants_data, image_files, set_parts_data):
    """Save source files before replying; FTP work only runs after commit."""
    from .models import product_image_upload_to
    from .services import create_product

    storage = local_photo_storage()
    saved = []
    try:
        for file in image_files:
            saved.append(stage_photo_source(file))
        with transaction.atomic():
            product = create_product(
                owner=owner,
                data=data,
                variants_data=variants_data,
                set_parts_data=set_parts_data,
            )
            for index, name in enumerate(saved):
                image = ProductImage(product=product)
                image.image = product_image_upload_to(image, "photo.jpg")
                image.thumbnail = product_image_upload_to(image, "thumbnail.jpg")
                image.local_source = name
                image.position = index
                image.is_primary = index == 0
                image.save()
            ProductMediaUpload.objects.create(product=product)
            # A post-commit notification failure must not remove committed photos.
            transaction.on_commit(saved.clear)
            from apps.moderation.services import submit_product_for_moderation

            product = submit_product_for_moderation(product=product)
            transaction.on_commit(lambda: enqueue_photo_upload(product.pk), robust=True)
        return product
    except Exception:
        for name in saved:
            storage.delete(name)
        raise


def transfer_photos(product_id):
    """Transfer photos without changing the product's moderation status."""
    now = timezone.now()
    with transaction.atomic():
        job = ProductMediaUpload.objects.select_for_update().get(product_id=product_id)
        if job.status in {job.Status.SUCCEEDED, job.Status.CANCELLED}:
            return
        if job.status == job.Status.PROCESSING and job.claimed_at > now - timedelta(
            seconds=LEASE_SECONDS
        ):
            return
        if job.attempts >= MAX_ATTEMPTS or job.available_at > now:
            return
        if job.product.status == Product.Status.ARCHIVED:
            job.status = job.Status.CANCELLED
            job.finished_at = now
            job.save(update_fields=["status", "finished_at"])
            return
        job.status = job.Status.PROCESSING
        job.claimed_at = now
        job.queued_at = None
        job.attempts += 1
        job.save(update_fields=["status", "claimed_at", "attempts", "queued_at"])

    storage = local_photo_storage()
    try:
        from .services import _reuse_media_connection

        with _reuse_media_connection():
            for image in (
                ProductImage.objects.filter(product_id=product_id, uploaded_at=None)
                .exclude(local_source="")
                .order_by("position", "id")
            ):
                if not ProductImage.objects.filter(pk=image.pk).exists():
                    continue
                directory = str(Path(image.local_source).parent)
                optimized = f"{directory}/optimized.jpg"
                thumbnail = f"{directory}/thumbnail.jpg"
                if not (storage.exists(optimized) and storage.exists(thumbnail)):
                    with storage.open(image.local_source, "rb") as source:
                        full, small = make_product_image_variants(source)
                    for name, file in [(optimized, full), (thumbnail, small)]:
                        temporary = storage.save(f"{directory}/{uuid4().hex}.jpg", file)
                        os.replace(storage.path(temporary), storage.path(name))
                for local, remote in [
                    (optimized, image.image.name),
                    (thumbnail, image.thumbnail.name),
                ]:
                    if not default_storage.exists(remote):
                        with storage.open(local, "rb") as file:
                            actual = default_storage.save(remote, file)
                        if actual != remote:
                            default_storage.delete(actual)
                            raise OSError("Unexpected photo storage path.")
                updated = ProductImage.objects.filter(pk=image.pk).update(
                    uploaded_at=timezone.now()
                )
                if not updated:
                    default_storage.delete(image.image.name)
                    default_storage.delete(image.thumbnail.name)

        with transaction.atomic():
            job = ProductMediaUpload.objects.select_for_update().get(pk=job.pk)
            if job.status == job.Status.CANCELLED:
                return
            if (
                ProductImage.objects.filter(product_id=product_id, uploaded_at=None)
                .exclude(local_source="")
                .exists()
            ):
                job.status = job.Status.PENDING
                job.attempts = 0
                job.available_at = timezone.now()
                job.queued_at = None
                job.save(
                    update_fields=["status", "attempts", "available_at", "queued_at"]
                )
                transaction.on_commit(
                    lambda: enqueue_photo_upload(product_id), robust=True
                )
                return
            job.status = job.Status.SUCCEEDED
            job.finished_at = timezone.now()
            job.error = ""
            job.save(update_fields=["status", "finished_at", "error"])
    except Exception as exc:
        ProductMediaUpload.objects.filter(
            pk=job.pk, status=ProductMediaUpload.Status.PROCESSING
        ).update(
            status=ProductMediaUpload.Status.FAILED,
            queued_at=None,
            available_at=timezone.now()
            + timedelta(seconds=min(60 * 2 ** (job.attempts - 1), 1800)),
            error=type(exc).__name__,
        )
        logger.exception("Product photo transfer failed (product=%s)", product_id)
        raise


def retry_photo_upload(product):
    with transaction.atomic():
        job = get_object_or_404(
            ProductMediaUpload.objects.select_for_update(), product=product
        )
        if job.status != job.Status.FAILED or product.status == Product.Status.ARCHIVED:
            raise ValidationError("This photo upload cannot be retried right now.")
        job.status = job.Status.PENDING
        job.attempts = 0
        job.available_at = timezone.now()
        job.queued_at = None
        job.error = ""
        job.save(
            update_fields=["status", "attempts", "available_at", "queued_at", "error"]
        )
        transaction.on_commit(lambda: enqueue_photo_upload(product.pk), robust=True)


def recover_photo_uploads():
    now = timezone.now()
    ProductMediaUpload.objects.filter(
        status=ProductMediaUpload.Status.PROCESSING,
        claimed_at__lt=now - timedelta(seconds=LEASE_SECONDS),
        attempts__gte=MAX_ATTEMPTS,
    ).update(status=ProductMediaUpload.Status.FAILED, error="Worker interrupted")
    due = (
        ProductMediaUpload.objects.filter(attempts__lt=MAX_ATTEMPTS)
        .filter(
            Q(queued_at=None) | Q(queued_at__lt=now - timedelta(seconds=LEASE_SECONDS))
        )
        .filter(
            Q(
                status__in=[
                    ProductMediaUpload.Status.PENDING,
                    ProductMediaUpload.Status.FAILED,
                ],
                available_at__lte=now,
            )
            | Q(
                status=ProductMediaUpload.Status.PROCESSING,
                claimed_at__lt=now - timedelta(seconds=LEASE_SECONDS),
            )
        )
    )
    for product_id in due.values_list("product_id", flat=True)[:100]:
        enqueue_photo_upload(product_id)


def cleanup_local_photos():
    """Keep pending sources; only remove successful or orphaned files after 24h."""
    cutoff = timezone.now() - timedelta(hours=24)
    storage = local_photo_storage()
    root = Path(storage.location)
    if not root.exists():
        return 0
    removed = 0
    for directory in root.iterdir():
        if (
            not directory.is_dir()
            or directory.is_symlink()
            or len(directory.name) != 32
        ):
            continue
        try:
            int(directory.name, 16)
        except ValueError:
            continue
        images = ProductImage.objects.filter(
            local_source__startswith=f"{directory.name}/"
        )
        image = images.first()
        if image:
            job = getattr(image.product, "media_upload", None)
            if (
                not job
                or job.status not in {job.Status.SUCCEEDED, job.Status.CANCELLED}
                or not job.finished_at
                or job.finished_at > cutoff
            ):
                continue
        elif directory.stat().st_mtime > cutoff.timestamp():
            continue
        for file in directory.iterdir():
            if file.is_file() and not file.is_symlink():
                file.unlink()
        directory.rmdir()
        images.update(local_source="")
        removed += 1
    return removed
