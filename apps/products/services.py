from typing import Any

from django.db import transaction
from django.db.models import F, Max
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import Product, ProductImage, ProductVariant


@transaction.atomic
def create_product(
    *, owner, data: dict[str, Any], variants_data: list[dict[str, Any]]
) -> Product:
    product = Product.objects.create(owner=owner, **data)

    ProductVariant.objects.bulk_create(
        [
            ProductVariant(product=product, **variant_data)
            for variant_data in variants_data
        ]
    )

    return product


def create_product_with_images(
    *,
    owner,
    data: dict[str, Any],
    variants_data: list[dict[str, Any]],
    image_files: list,
) -> Product:
    """Create and submit a product with its initial images atomically.

    The temporary draft exists only inside this transaction: image upload
    requires an editable product, while moderation requires persisted images.
    """
    saved_image_files = []

    try:
        with transaction.atomic():
            product = create_product(
                owner=owner,
                data=data,
                variants_data=variants_data,
            )

            for position, image_file in enumerate(image_files):
                image = upload_product_image(
                    product=product,
                    image_file=image_file,
                    is_primary=position == 0,
                )
                saved_image_files.append(image.image)

            # Lazy import avoids a products <-> moderation import cycle.
            from apps.moderation.services import submit_product_for_moderation

            return submit_product_for_moderation(product=product)
    except Exception:
        # Database changes are rolled back by the transaction, while FTP/media
        # storage is external to that transaction. Remove any already uploaded
        # files on a best-effort basis to avoid orphaned media.
        for saved_image in saved_image_files:
            saved_image.delete(save=False)
        raise


@transaction.atomic
def update_product(
    *,
    product: Product,
    data: dict[str, Any],
    variants_data: list[dict[str, Any]] | None,
) -> Product:
    for field, value in data.items():
        setattr(product, field, value)

    product.save()

    if variants_data is not None:
        product.variants.all().delete()

        ProductVariant.objects.bulk_create(
            [
                ProductVariant(product=product, **variant_data)
                for variant_data in variants_data
            ]
        )

    return product


EDITABLE_PRODUCT_STATUSES = {Product.Status.DRAFT, Product.Status.REJECTED}


def ensure_product_is_editable(
    product: Product,
    *,
    allow_after_approval: bool = False,
) -> None:
    if not allow_after_approval and product.status not in EDITABLE_PRODUCT_STATUSES:
        raise ValidationError(
            {"detail": ("Only draft or rejected products can be changed")}
        )


@transaction.atomic
def upload_product_image(
    *,
    product: Product,
    image_file,
    is_primary: bool,
    allow_after_approval: bool = False,
) -> ProductImage:

    locked_product = Product.objects.select_for_update().get(pk=product.pk)
    ensure_product_is_editable(
        locked_product,
        allow_after_approval=allow_after_approval,
    )

    images_queryset = ProductImage.objects.select_for_update().filter(
        product=locked_product
    )

    if images_queryset.count() >= 10:
        raise ValidationError({"image": "A product cannot have more than 10 images"})

    max_position = images_queryset.aggregate(max_position=Max("position"))[
        "max_position"
    ]

    position = 0 if max_position is None else max_position + 1
    has_primary = images_queryset.filter(is_primary=True).exists()

    if is_primary or not has_primary:
        images_queryset.update(is_primary=False)
        is_primary = True

    return ProductImage.objects.create(
        product=locked_product,
        image=image_file,
        position=position,
        is_primary=is_primary,
    )


@transaction.atomic
def delete_product_image(
    *,
    product: Product,
    image: ProductImage,
    allow_after_approval: bool = False,
) -> None:
    locked_product = Product.objects.select_for_update().get(pk=product.pk)
    ensure_product_is_editable(
        locked_product,
        allow_after_approval=allow_after_approval,
    )

    image = ProductImage.objects.select_for_update().get(
        pk=image.pk, product=locked_product
    )

    image_files = [
        image.image,
        image.processed_image,
    ]
    was_primary = image.is_primary

    image.delete()

    if was_primary:
        next_image = (
            ProductImage.objects.filter(product=locked_product)
            .order_by("position", "id")
            .first()
        )

        if next_image:
            next_image.is_primary = True
            next_image.save(update_fields=("is_primary",))

    def remove_files():
        for image_file in image_files:
            if image_file:
                image_file.delete(save=False)

    transaction.on_commit(remove_files)


@transaction.atomic
def make_product_image_primary(
    *, product: Product, image: ProductImage, allow_after_approval: bool = False
) -> ProductImage:
    locked_product = Product.objects.select_for_update().get(pk=product.pk)
    ensure_product_is_editable(
        locked_product,
        allow_after_approval=allow_after_approval,
    )

    image = ProductImage.objects.select_for_update().get(
        pk=image.pk, product=locked_product
    )

    ProductImage.objects.filter(product=locked_product).update(is_primary=False)

    image.is_primary = True
    image.save(update_fields=("is_primary",))

    return image


@transaction.atomic
def reorder_product_images(
    *,
    product: Product,
    image_ids: list[int],
    allow_after_approval: bool = False,
) -> None:
    locked_product = Product.objects.select_for_update().get(pk=product.pk)
    ensure_product_is_editable(
        locked_product,
        allow_after_approval=allow_after_approval,
    )

    images = list(
        ProductImage.objects.select_for_update()
        .filter(product=locked_product)
        .order_by("position", "id")
    )

    current_image_ids = {image.id for image in images}

    if set(image_ids) != current_image_ids:
        raise ValidationError(
            {
                "image_ids": (
                    "The list must contain every image of this productexactly once."
                )
            }
        )

    max_position = max((image.position for image in images), default=0)

    ProductImage.objects.filter(product=locked_product).update(
        position=F("position") + max_position + len(images) + 1
    )

    for position, image_id in enumerate(image_ids):
        ProductImage.objects.filter(
            product=locked_product,
            pk=image_id,
        ).update(position=position)


@transaction.atomic
def confirm_product_availability(
    *,
    product: Product,
    is_available: bool,
) -> Product:
    locked_product = Product.objects.select_for_update().get(pk=product.pk)

    if locked_product.status != Product.Status.APPROVED:
        raise ValidationError(
            {"detail": ("Availability can only be confirmed for an approved product.")}
        )

    locked_product.is_available = is_available
    locked_product.availability_confirmed_at = timezone.now()
    locked_product.availability_reminder_sent_at = timezone.now()
    locked_product.save(
        update_fields=(
            "is_available",
            "availability_confirmed_at",
            "availability_reminder_sent_at",
            "updated_at",
        )
    )

    return locked_product


@transaction.atomic
def request_product_deactivation(*, product: Product) -> Product:
    locked_product = Product.objects.select_for_update().get(pk=product.pk)

    if locked_product.status != Product.Status.APPROVED:
        raise ValidationError(
            {"detail": "Only approved products can be requested for deactivation."}
        )

    if locked_product.deactivation_requested_at is not None:
        raise ValidationError({"detail": "A deactivation request is already pending."})

    locked_product.deactivation_requested_at = timezone.now()
    locked_product.save(update_fields=("deactivation_requested_at", "updated_at"))
    return locked_product


@transaction.atomic
def deactivate_product(*, product: Product) -> Product:
    """Deprecated guard kept for callers of the former product-level API.

    A product can be active on only part of the marketplace/account targets,
    so there is no correct single global ``deactivated`` product status. Use
    the orchestrator listing-state service instead.
    """
    raise ValidationError(
        {
            "detail": (
                "Use marketplace listing-state actions to deactivate selected "
                "marketplace targets."
            )
        }
    )


@transaction.atomic
def withdraw_product_submission(*, product: Product) -> Product:
    """Hide a not-yet-approved product without notifying managers.

    EANs are normally assigned only during approval. Releasing any attached
    codes also makes withdrawal safe for products submitted by an older app
    version that reserved them earlier.
    """
    locked_product = Product.objects.select_for_update().get(pk=product.pk)

    if locked_product.status not in {
        Product.Status.SUBMITTED,
        Product.Status.UNDER_REVIEW,
    }:
        raise ValidationError(
            {"detail": ("Only a submitted or under-review product can be withdrawn.")}
        )

    from apps.ean.models import EanCode

    EanCode.objects.filter(
        product=locked_product,
        state=EanCode.State.RESERVED,
    ).update(
        product=None,
        state=EanCode.State.AVAILABLE,
        assigned_at=None,
    )
    locked_product.status = Product.Status.ARCHIVED
    locked_product.save(update_fields=("status", "updated_at"))
    return locked_product


@transaction.atomic
def request_product_availability(*, product: Product, manager) -> Product:
    locked_product = Product.objects.select_for_update().get(pk=product.pk)

    if locked_product.status != Product.Status.APPROVED:
        raise ValidationError(
            {"detail": "Only approved products can receive an availability request."}
        )

    locked_product.availability_reminder_sent_at = timezone.now()
    locked_product.save(update_fields=("availability_reminder_sent_at", "updated_at"))

    from apps.notifications.models import Notification
    from apps.notifications.services import create_notification

    transaction.on_commit(
        lambda: create_notification(
            user=locked_product.owner,
            sender=manager,
            product=locked_product,
            notification_type=Notification.Type.PRODUCT_AVAILABILITY_REMINDER,
            title="Product availability",
            body="Please confirm whether this product is still available.",
            data={"product_id": locked_product.id},
        )
    )
    return locked_product


@transaction.atomic
def request_product_image_processing(
    *, product: Product, image: ProductImage
) -> ProductImage:
    locked_product = Product.objects.select_for_update().get(pk=product.pk)

    if locked_product.status not in {
        Product.Status.SUBMITTED,
        Product.Status.UNDER_REVIEW,
        Product.Status.APPROVED,
    }:
        raise ValidationError(
            {"detail": "Images can be generated only after product submission."}
        )

    locked_image = ProductImage.objects.select_for_update().get(
        pk=image.pk,
        product=locked_product,
    )

    if locked_image.processing_status == ProductImage.ProcessingStatus.PROCESSING:
        raise ValidationError({"detail": "Image processing is already in progress"})

    locked_image.processing_status = ProductImage.ProcessingStatus.PENDING
    locked_image.processing_error = ""
    locked_image.processing_result = {}
    locked_image.save(
        update_fields=("processing_status", "processing_error", "processing_result")
    )

    from apps.notifications.tasks import process_product_image

    transaction.on_commit(lambda: process_product_image.delay(locked_image.id))

    return locked_image
