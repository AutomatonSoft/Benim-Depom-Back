import json
from decimal import Decimal
from typing import Any

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import F, Max, Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import (
    PriceNegotiation,
    Product,
    ProductGeneratedImage,
    ProductImage,
    ProductVariant,
)


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


SELLER_PENDING_COMMENT_KEY = "seller_comment"
SELLER_REVIEW_KIND_KEY = "review_kind"
SELLER_REVIEW_BASELINE_KEY = "baseline"
SELLER_REVIEW_MANAGER_COMMENT_KEY = "manager_comment"
SELLER_REVIEW_AT_KEY = "reviewed_at"
SELLER_REVIEW_META_KEYS = frozenset(
    {
        SELLER_PENDING_COMMENT_KEY,
        SELLER_REVIEW_KIND_KEY,
        SELLER_REVIEW_BASELINE_KEY,
        SELLER_REVIEW_MANAGER_COMMENT_KEY,
        SELLER_REVIEW_AT_KEY,
    }
)
SELLER_REVIEW_KIND_REJECTED_PENDING = "rejected_pending"
SELLER_REVIEW_KIND_RESUBMISSION = "resubmission"


def _current_variants_payload(product: Product) -> list[dict[str, Any]]:
    return [
        {
            "color": variant.color,
            "materials": list(variant.materials or []),
            "width_cm": variant.width_cm,
            "height_cm": variant.height_cm,
            "length_cm": variant.length_cm,
            "quantity": variant.quantity,
        }
        for variant in product.variants.all().order_by("id")
    ]


def _baseline_for_pending(product: Product, pending: dict[str, Any]) -> dict[str, Any]:
    baseline: dict[str, Any] = {}
    for field in pending:
        if field in SELLER_REVIEW_META_KEYS:
            continue
        if field == "variants":
            baseline[field] = _json_ready(_current_variants_payload(product))
        elif field in _PENDING_PRODUCT_FIELDS:
            baseline[field] = _json_ready(getattr(product, field))
    return baseline


def build_seller_resubmission_review(
    *,
    product: Product,
    data: dict[str, Any],
    variants_data: list[dict[str, Any]] | None,
    comment: str = "",
) -> dict[str, Any]:
    baseline: dict[str, Any] = {}
    changes: dict[str, Any] = {}
    for field in _PENDING_PRODUCT_FIELDS:
        if field not in data:
            continue
        old = _json_ready(getattr(product, field))
        new = _json_ready(data[field])
        if old != new:
            baseline[field] = old
            changes[field] = new

    if variants_data is not None:
        old_variants = _json_ready(_current_variants_payload(product))
        new_variants = _json_ready(variants_data)
        if old_variants != new_variants:
            baseline["variants"] = old_variants
            changes["variants"] = new_variants

    review: dict[str, Any] = {
        SELLER_REVIEW_KIND_KEY: SELLER_REVIEW_KIND_RESUBMISSION,
        SELLER_REVIEW_AT_KEY: timezone.now().isoformat(),
        **changes,
    }
    cleaned = str(comment or "").strip()
    if cleaned:
        review[SELLER_PENDING_COMMENT_KEY] = cleaned
    if baseline:
        review[SELLER_REVIEW_BASELINE_KEY] = baseline
    return review


def _normalize_variants_payload(
    variants_data: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "color": item.get("color"),
            "materials": list(item.get("materials") or []),
            "width_cm": item.get("width_cm"),
            "height_cm": item.get("height_cm"),
            "length_cm": item.get("length_cm"),
            "quantity": item.get("quantity"),
        }
        for item in variants_data
    ]


def _latest_accepted_unit_price(product: Product):
    negotiation = (
        PriceNegotiation.objects.filter(
            product=product,
            status=PriceNegotiation.Status.ACCEPTED,
        )
        .order_by("-responded_at", "-created_at")
        .first()
    )
    if negotiation is None:
        return None
    return _json_ready(negotiation.proposed_unit_price)


def _pending_field_diffs(product: Product, data: dict[str, Any]) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    accepted_price = None
    if "unit_price" in data:
        accepted_price = _latest_accepted_unit_price(product)
    for field, value in data.items():
        if field not in _PENDING_PRODUCT_FIELDS:
            continue
        new = _json_ready(value)
        old = _json_ready(getattr(product, field))
        if old == new:
            continue
        if field == "unit_price" and accepted_price is not None and new == accepted_price:
            continue
        updates[field] = new
    return updates


def _pending_variants_diff(
    product: Product, variants_data: list[dict[str, Any]] | None
) -> list[dict[str, Any]] | None:
    if variants_data is None:
        return None
    old = _json_ready(_current_variants_payload(product))
    new = _json_ready(_normalize_variants_payload(variants_data))
    if old == new:
        return None
    return new


@transaction.atomic
def save_seller_pending_changes(
    *,
    product: Product,
    data: dict[str, Any],
    variants_data: list[dict[str, Any]] | None,
    comment: str | None = None,
) -> Product:
    locked_product = Product.objects.select_for_update().get(pk=product.pk)
    if locked_product.status != Product.Status.APPROVED:
        raise ValidationError(
            {"detail": "Pending catalog changes are only stored for approved products."}
        )

    pending = dict(locked_product.pending_changes or {})
    updates = _pending_field_diffs(locked_product, data)
    variants_update = _pending_variants_diff(locked_product, variants_data)
    comment_provided = comment is not None

    if not updates and variants_update is None and not comment_provided:
        return locked_product

    if not updates and variants_update is None:
        raise ValidationError(
            {
                "detail": (
                    "Send at least one changed product field. "
                    "A comment alone is not enough."
                )
            }
        )

    pending.update(updates)
    if variants_update is not None:
        pending["variants"] = variants_update
    if comment_provided:
        cleaned = str(comment or "").strip()
        if cleaned:
            pending[SELLER_PENDING_COMMENT_KEY] = cleaned
        else:
            pending.pop(SELLER_PENDING_COMMENT_KEY, None)

    locked_product.pending_changes = pending
    locked_product.pending_changes_submitted_at = timezone.now()
    locked_product.seller_change_review = {}
    locked_product.catalog_revision += 1
    locked_product.save(
        update_fields=(
            "pending_changes",
            "pending_changes_submitted_at",
            "seller_change_review",
            "catalog_revision",
            "updated_at",
        )
    )

    if updates or variants_update is not None:
        from apps.notifications.models import Notification
        from apps.notifications.services import create_notification, manager_inbox_users

        seller = locked_product.owner
        seller_comment = str(pending.get(SELLER_PENDING_COMMENT_KEY) or "").strip()
        for manager in manager_inbox_users(exclude_user=seller):
            create_notification(
                user=manager,
                sender=seller,
                product=locked_product,
                notification_type=Notification.Type.PRODUCT_CHANGE_REQUESTED,
                comment=seller_comment,
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
    locked_product.seller_change_review = {}
    locked_product.catalog_revision += 1
    locked_product.save(
        update_fields=(
            "pending_changes",
            "pending_changes_submitted_at",
            "seller_change_review",
            "catalog_revision",
            "updated_at",
        )
    )
    return locked_product


@transaction.atomic
def discard_pending_seller_changes(
    *,
    product: Product,
    archive: bool = False,
    manager_comment: str = "",
) -> Product:
    locked_product = Product.objects.select_for_update().get(pk=product.pk)
    if not locked_product.pending_changes:
        raise ValidationError({"detail": "This product has no pending seller changes."})

    pending = dict(locked_product.pending_changes or {})
    update_fields = [
        "pending_changes",
        "pending_changes_submitted_at",
        "updated_at",
    ]
    if archive and pending:
        locked_product.seller_change_review = {
            **pending,
            SELLER_REVIEW_KIND_KEY: SELLER_REVIEW_KIND_REJECTED_PENDING,
            SELLER_REVIEW_AT_KEY: timezone.now().isoformat(),
            SELLER_REVIEW_MANAGER_COMMENT_KEY: str(manager_comment or "").strip(),
            SELLER_REVIEW_BASELINE_KEY: _baseline_for_pending(locked_product, pending),
        }
        update_fields.append("seller_change_review")

    locked_product.pending_changes = {}
    locked_product.pending_changes_submitted_at = None
    locked_product.save(update_fields=update_fields)
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

    seller = locked_product.owner
    for manager in manager_inbox_users(exclude_user=seller):
        create_notification(
            user=manager,
            sender=seller,
            product=locked_product,
            notification_type=Notification.Type.PRODUCT_WITHDRAWN_FROM_REVIEW,
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


PRICE_NEGOTIATION_STATUSES = {
    Product.Status.SUBMITTED,
    Product.Status.APPROVED,
}


@transaction.atomic
def create_price_negotiation(
    *,
    product: Product,
    manager,
    proposed_unit_price: Decimal,
    message: str,
) -> PriceNegotiation:
    locked_product = Product.objects.select_for_update().get(pk=product.pk)

    if locked_product.status not in PRICE_NEGOTIATION_STATUSES:
        raise ValidationError(
            {
                "detail": (
                    "Price negotiation is only available for submitted or "
                    "approved products."
                )
            }
        )

    price = Decimal(str(proposed_unit_price))
    if price < Decimal("0.01"):
        raise ValidationError(
            {"proposed_unit_price": "Enter a price of at least 0.01."}
        )

    cleaned_message = str(message or "").strip()
    if not cleaned_message:
        raise ValidationError({"message": "Enter a message for the seller."})

    PriceNegotiation.objects.filter(
        product=locked_product,
        status=PriceNegotiation.Status.PENDING,
    ).update(
        status=PriceNegotiation.Status.SUPERSEDED,
        updated_at=timezone.now(),
    )

    negotiation = PriceNegotiation.objects.create(
        product=locked_product,
        manager=manager,
        currency=locked_product.currency,
        current_unit_price=locked_product.unit_price,
        proposed_unit_price=price,
        message=cleaned_message,
        status=PriceNegotiation.Status.PENDING,
    )

    from apps.notifications.models import Notification
    from apps.notifications.services import create_notification

    owner = locked_product.owner
    transaction.on_commit(
        lambda owner=owner, negotiation=negotiation, manager=manager: (
            create_notification(
                user=owner,
                sender=manager,
                product=negotiation.product,
                notification_type=Notification.Type.PRICE_NEGOTIATION_OFFER,
                copy_key="price_negotiation_offer",
                message=negotiation.message,
                price=f"{negotiation.proposed_unit_price:.2f}",
                currency=negotiation.currency,
                price_negotiation=negotiation,
            )
        )
    )
    return negotiation


@transaction.atomic
def respond_to_price_negotiation(
    *,
    product: Product,
    seller,
    accepted: bool,
    comment: str = "",
) -> PriceNegotiation:
    locked_product = Product.objects.select_for_update().get(pk=product.pk)

    if locked_product.owner_id != seller.id:
        raise ValidationError(
            {"detail": "Only the product seller can respond to a price offer."}
        )

    if locked_product.status not in PRICE_NEGOTIATION_STATUSES:
        raise ValidationError(
            {
                "detail": (
                    "Price negotiation responses are only accepted for submitted "
                    "or approved products."
                )
            }
        )

    negotiation = (
        PriceNegotiation.objects.select_for_update()
        .filter(
            product=locked_product,
            status=PriceNegotiation.Status.PENDING,
        )
        .order_by("-created_at")
        .first()
    )
    if negotiation is None:
        raise ValidationError(
            {"detail": "This product has no pending price negotiation."}
        )

    negotiation.status = (
        PriceNegotiation.Status.ACCEPTED
        if accepted
        else PriceNegotiation.Status.REJECTED
    )
    negotiation.seller_comment = str(comment or "").strip()
    negotiation.responded_at = timezone.now()
    negotiation.save(
        update_fields=(
            "status",
            "seller_comment",
            "responded_at",
            "updated_at",
        )
    )

    if accepted:
        # Catalog price only — do not enqueue marketplace listing updates.
        locked_product.unit_price = negotiation.proposed_unit_price
        locked_product.save(update_fields=("unit_price", "updated_at"))

    from apps.notifications.models import Notification
    from apps.notifications.services import create_notification, manager_inbox_users

    Notification.objects.filter(
        product=locked_product,
        notification_type=Notification.Type.PRICE_NEGOTIATION_OFFER,
    ).filter(
        Q(price_negotiation=negotiation) | Q(price_negotiation_id__isnull=True)
    ).update(
        is_read=True,
        read_at=negotiation.responded_at,
        responded_at=negotiation.responded_at,
    )

    copy_key = (
        "price_negotiation_accepted" if accepted else "price_negotiation_rejected"
    )
    price_text = f"{negotiation.proposed_unit_price:.2f}"
    currency = negotiation.currency
    seller_comment = negotiation.seller_comment
    for manager in manager_inbox_users(exclude_user=seller):
        transaction.on_commit(
            lambda manager=manager, negotiation=negotiation, copy_key=copy_key, price_text=price_text, currency=currency, seller_comment=seller_comment, seller=seller: (
                create_notification(
                    user=manager,
                    sender=seller,
                    product=negotiation.product,
                    notification_type=Notification.Type.PRICE_NEGOTIATION_RESPONSE,
                    copy_key=copy_key,
                    price=price_text,
                    currency=currency,
                    comment=seller_comment,
                    price_negotiation=negotiation,
                )
            )
        )

    return negotiation


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
