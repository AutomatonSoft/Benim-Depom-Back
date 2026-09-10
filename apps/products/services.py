import json
from typing import Any

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import F, Max
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import Product, ProductGeneratedImage, ProductImage, ProductVariant


def _json_ready(value: Any) -> Any:
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder))


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

    if product.status == Product.Status.SUBMITTED:
        product.catalog_revision += 1

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


_PENDING_PRODUCT_FIELDS = {
    "title",
    "product_type",
    "unit_price",
    "currency",
    "warehouse_city",
    "otto_category_id",
    "otto_category_group_id",
    "otto_category_name",
    "otto_category_group_name",
    "otto_attributes",
}


@transaction.atomic
def save_seller_pending_changes(
    *,
    product: Product,
    data: dict[str, Any],
    variants_data: list[dict[str, Any]] | None,
) -> Product:
    locked_product = Product.objects.select_for_update().get(pk=product.pk)
    if locked_product.status != Product.Status.APPROVED:
        raise ValidationError(
            {"detail": "Pending catalog changes are only stored for approved products."}
        )

    pending = dict(locked_product.pending_changes or {})
    updates = {
        field: _json_ready(value)
        for field, value in data.items()
        if field in _PENDING_PRODUCT_FIELDS
    }
    if not updates and variants_data is None:
        return locked_product

    was_empty = not pending
    pending.update(updates)
    if variants_data is not None:
        pending["variants"] = _json_ready(variants_data)

    locked_product.pending_changes = pending
    locked_product.pending_changes_submitted_at = timezone.now()
    locked_product.catalog_revision += 1
    locked_product.save(
        update_fields=(
            "pending_changes",
            "pending_changes_submitted_at",
            "catalog_revision",
            "updated_at",
        )
    )

    if was_empty:
        from apps.notifications.models import Notification
        from apps.notifications.services import create_notification, manager_inbox_users

        name = (locked_product.title or "").strip() or f"#{locked_product.pk}"
        seller = locked_product.owner
        seller_name = (seller.username or seller.email or "Seller").strip()
        for manager in manager_inbox_users(exclude_user=seller):
            create_notification(
                user=manager,
                sender=seller,
                product=locked_product,
                notification_type=Notification.Type.PRODUCT_CHANGE_REQUESTED,
                title="Seller wants to change a product",
                body=(
                    f"{seller_name} requested changes to '{name}'. "
                    "Open the product to compare current and new values."
                ),
            )

    return locked_product


@transaction.atomic
def apply_pending_seller_changes(*, product: Product) -> Product:
    locked_product = Product.objects.select_for_update().get(pk=product.pk)
    pending = dict(locked_product.pending_changes or {})
    if not pending:
        raise ValidationError({"detail": "This product has no pending seller changes."})

    variants_data = pending.pop("variants", None)
    product_data = {
        field: value
        for field, value in pending.items()
        if field in _PENDING_PRODUCT_FIELDS
    }
    locked_product = update_product(
        product=locked_product,
        data=product_data,
        variants_data=variants_data,
    )
    locked_product.pending_changes = {}
    locked_product.pending_changes_submitted_at = None
    locked_product.catalog_revision += 1
    locked_product.save(
        update_fields=(
            "pending_changes",
            "pending_changes_submitted_at",
            "catalog_revision",
            "updated_at",
        )
    )
    return locked_product


@transaction.atomic
def discard_pending_seller_changes(*, product: Product) -> Product:
    locked_product = Product.objects.select_for_update().get(pk=product.pk)
    if not locked_product.pending_changes:
        raise ValidationError({"detail": "This product has no pending seller changes."})

    locked_product.pending_changes = {}
    locked_product.pending_changes_submitted_at = None
    locked_product.save(
        update_fields=(
            "pending_changes",
            "pending_changes_submitted_at",
            "updated_at",
        )
    )
    return locked_product


EDITABLE_PRODUCT_STATUSES = {
    Product.Status.DRAFT,
    Product.Status.SUBMITTED,
    Product.Status.REJECTED,
    Product.Status.WITHDRAWN,
}


def ensure_product_is_editable(
    product: Product,
    *,
    allow_after_approval: bool = False,
) -> None:
    if not allow_after_approval and product.status not in EDITABLE_PRODUCT_STATUSES:
        raise ValidationError(
            {
                "detail": (
                    "Only draft, submitted, rejected or withdrawn products can be changed"
                )
            }
        )


def bump_in_review_catalog_revision(product: Product) -> None:
    """Invalidate a manager approve if the seller edits a product in review."""
    if product.status != Product.Status.SUBMITTED:
        return
    product.catalog_revision += 1
    product.save(update_fields=("catalog_revision", "updated_at"))


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

    created = ProductImage.objects.create(
        product=locked_product,
        image=image_file,
        position=position,
        is_primary=is_primary,
    )
    bump_in_review_catalog_revision(locked_product)
    return created


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
        *(generated.image for generated in image.generated_images.all()),
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
    bump_in_review_catalog_revision(locked_product)


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
    bump_in_review_catalog_revision(locked_product)
    return image


@transaction.atomic
def delete_generated_product_image(
    *,
    product: Product,
    generated,
    allow_after_approval: bool = False,
) -> None:
    locked_product = Product.objects.select_for_update().get(pk=product.pk)
    ensure_product_is_editable(
        locked_product,
        allow_after_approval=allow_after_approval,
    )
    generated = (
        ProductGeneratedImage.objects.select_for_update()
        .select_related("source_image")
        .get(pk=generated.pk, source_image__product=locked_product)
    )
    source = generated.source_image
    image_file = generated.image

    if generated.mode == ProductGeneratedImage.Mode.WHITE:
        source.processed_image = None
        source.save(update_fields=("processed_image",))

    generated.delete()

    def remove_file():
        if image_file:
            image_file.delete(save=False)

    transaction.on_commit(remove_file)


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

    bump_in_review_catalog_revision(locked_product)


@transaction.atomic
def confirm_product_availability(
    *,
    product: Product,
    is_available: bool,
    seller=None,
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

    from apps.notifications.services import (
        mark_product_availability_reminders_responded,
        notify_managers_of_availability_confirmation,
    )

    mark_product_availability_reminders_responded(product=locked_product)
    notify_managers_of_availability_confirmation(
        product=locked_product,
        seller=seller or locked_product.owner,
        is_available=is_available,
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
    """Take a submitted product off moderation.

    Editing a submitted product no longer requires this step: PATCH updates
    the live review copy and bumps catalog_revision so a stale manager
    approve fails. Withdraw is for removing it from the queue entirely.
    """
    locked_product = Product.objects.select_for_update().get(pk=product.pk)

    if locked_product.status != Product.Status.SUBMITTED:
        raise ValidationError({"detail": "Only a submitted product can be withdrawn."})

    from apps.ean.models import EanCode
    from apps.moderation.models import ModerationDecision
    from apps.notifications.models import Notification
    from apps.notifications.services import create_notification, manager_inbox_users

    EanCode.objects.filter(
        product=locked_product,
        state=EanCode.State.RESERVED,
    ).update(
        product=None,
        state=EanCode.State.AVAILABLE,
        assigned_at=None,
    )
    locked_product.status = Product.Status.WITHDRAWN
    locked_product.catalog_revision += 1
    locked_product.save(update_fields=("status", "catalog_revision", "updated_at"))
    ModerationDecision.objects.create(
        product=locked_product,
        manager=locked_product.owner,
        decision=ModerationDecision.Decision.WITHDRAWN,
        comment="Seller withdrew the product from review.",
    )

    name = (locked_product.title or "").strip() or f"#{locked_product.pk}"
    seller = locked_product.owner
    seller_name = (seller.username or seller.email or "Seller").strip()
    for manager in manager_inbox_users(exclude_user=seller):
        create_notification(
            user=manager,
            sender=seller,
            product=locked_product,
            notification_type=Notification.Type.PRODUCT_WITHDRAWN_FROM_REVIEW,
            title="Seller withdrew a product from review",
            body=(
                f"{seller_name} withdrew '{name}' from moderation. "
                "Reload the product before continuing."
            ),
        )
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
    from apps.notifications.services import (
        create_notification,
        product_availability_reminder_copy,
    )

    title, body = product_availability_reminder_copy(locked_product)
    transaction.on_commit(
        lambda: create_notification(
            user=locked_product.owner,
            sender=manager,
            product=locked_product,
            notification_type=Notification.Type.PRODUCT_AVAILABILITY_REMINDER,
            title=title,
            body=body,
        )
    )
    return locked_product


@transaction.atomic
def request_product_image_processing(
    *, product: Product, image: ProductImage
) -> ProductImage:
    locked_product = Product.objects.select_for_update().get(pk=product.pk)

    if locked_product.status != Product.Status.APPROVED:
        raise ValidationError(
            {"detail": "Images can be generated only after the product is approved."}
        )

    locked_image = ProductImage.objects.select_for_update().get(
        pk=image.pk,
        product=locked_product,
    )

    if not locked_image.is_primary:
        raise ValidationError(
            {"detail": "AI images can be generated only for the cover photo."}
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
