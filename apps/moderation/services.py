from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status as http_status
from rest_framework.exceptions import APIException, ValidationError

from apps.accounts.models import User
from apps.catalog.otto_catalog import OttoCatalogError, get_otto_catalog
from apps.ean.services import assign_ean_codes_to_product
from apps.notifications.models import Notification
from apps.notifications.services import create_notification
from apps.orchestrator.job_services import (
    MarketplacePayloadBuildError,
    create_update_job_for_active_listings,
)
from apps.products.models import Product
from apps.products.services import apply_pending_seller_changes

from .models import ModerationDecision


class ProductChangedBySeller(APIException):
    status_code = http_status.HTTP_409_CONFLICT
    default_code = "seller_changed_product"
    default_detail = (
        "The seller just changed this product. Reload the page to see the current data."
    )


def ensure_catalog_revision(
    *,
    product: Product,
    expected_revision: int | None,
    withdrawn_if_not_submitted: bool = False,
) -> None:
    if expected_revision is None:
        return
    if product.catalog_revision == expected_revision:
        return
    if withdrawn_if_not_submitted and product.status != Product.Status.SUBMITTED:
        raise ProductChangedBySeller(
            detail=(
                "The seller withdrew this product from review. "
                "Reload the page to see the current data."
            ),
            code="product_withdrawn_from_review",
        )
    raise ProductChangedBySeller()


def validate_product_otto_data_for_submission(product: Product) -> None:
    """
    Validates the selected OTTO category when the seller sends a product
    to moderation. Attribute relevance is used only by the UI for ordering;
    HIGH, MEDIUM and LOW attributes are all optional.
    """
    if product.unit_price is None:
        raise ValidationError(
            {"unit_price": ("Set the price per unit before submitting the product.")}
        )

    # OTTO category data is optional at seller submission time. When a
    # category is selected, both identifiers must still be present and valid.
    if product.otto_category_id is None and product.otto_category_group_id is None:
        return

    if product.otto_category_id is None:
        raise ValidationError(
            {
                "otto_category_id": (
                    "Select an OTTO category before submitting the product."
                )
            }
        )

    if product.otto_category_group_id is None:
        raise ValidationError(
            {
                "otto_category_group_id": (
                    "Select an OTTO category group before submitting the product."
                )
            }
        )

    try:
        catalog = get_otto_catalog()
    except OttoCatalogError as exc:
        raise ValidationError(
            {"detail": "OTTO catalog is temporarily unavailable."}
        ) from exc

    category = catalog["categories_by_id"].get(product.otto_category_id)

    if category is None:
        raise ValidationError(
            {"otto_category_id": "The selected OTTO category no longer exists."}
        )

    actual_group_id = int(category["category_group_id"])

    if actual_group_id != product.otto_category_group_id:
        raise ValidationError(
            {
                "otto_category_group_id": (
                    "The selected OTTO category does not belong to the selected group."
                )
            }
        )

    attributes = catalog["attributes_by_group_id"].get(
        product.otto_category_group_id,
    )

    if attributes is None:
        raise ValidationError(
            {
                "otto_category_group_id": (
                    "No attribute configuration was found for this OTTO category group."
                )
            }
        )


@transaction.atomic
def submit_product_for_moderation(*, product: Product) -> Product:
    product = Product.objects.select_for_update().get(pk=product.pk)

    if product.status not in {
        Product.Status.DRAFT,
        Product.Status.REJECTED,
    }:
        raise ValidationError(
            {"detail": "This product cannot be submitted for moderation."}
        )

    if not product.variants.exists():
        raise ValidationError(
            {"variants": "The product must contain at least one variant."}
        )

    if not product.images.exists():
        raise ValidationError(
            {"images": "The product must contain at least one image."}
        )

    validate_product_otto_data_for_submission(product)

    product.status = Product.Status.SUBMITTED
    product.save(update_fields=("status", "updated_at"))

    managers = User.objects.filter(
        Q(role__in=(User.Role.MANAGER, User.Role.ADMIN)) | Q(is_superuser=True),
        is_active=True,
    ).distinct()
    for manager in managers:
        transaction.on_commit(
            lambda manager=manager: create_notification(
                user=manager,
                sender=product.owner,
                product=product,
                notification_type=Notification.Type.PRODUCT_SUBMITTED_FOR_REVIEW,
                title="New product awaiting review",
                body=f"{product.owner.username} submitted '{product.title}' for moderation.",
            )
        )

    return product


@transaction.atomic
def approve_product(
    *,
    product: Product,
    manager,
    comment: str = "",
    expected_catalog_revision: int | None = None,
) -> Product:
    product = Product.objects.select_for_update().get(pk=product.pk)
    ensure_catalog_revision(
        product=product,
        expected_revision=expected_catalog_revision,
        withdrawn_if_not_submitted=True,
    )

    if product.status != Product.Status.SUBMITTED:
        raise ValidationError({"detail": "Only submitted products can be approved."})

    # EANs are consumed only for a product the manager actually approves.
    # The same transaction prevents a partial approval if a pool is empty.
    assign_ean_codes_to_product(product=product)

    product.status = Product.Status.APPROVED
    product.approved_at = timezone.now()
    product.is_available = True
    product.availability_reminder_sent_at = None
    product.save(
        update_fields=(
            "status",
            "approved_at",
            "is_available",
            "availability_reminder_sent_at",
            "updated_at",
        )
    )

    ModerationDecision.objects.create(
        product=product,
        manager=manager,
        decision=ModerationDecision.Decision.APPROVED,
        comment=comment,
    )

    transaction.on_commit(
        lambda: create_notification(
            user=product.owner,
            product=product,
            notification_type=Notification.Type.PRODUCT_APPROVED,
        )
    )

    return product


@transaction.atomic
def reject_product(
    *,
    product: Product,
    manager,
    comment: str,
    expected_catalog_revision: int | None = None,
) -> Product:
    product = Product.objects.select_for_update().get(pk=product.pk)
    ensure_catalog_revision(
        product=product,
        expected_revision=expected_catalog_revision,
        withdrawn_if_not_submitted=True,
    )

    if product.status != Product.Status.SUBMITTED:
        raise ValidationError({"detail": "Only submitted products can be rejected."})

    product.status = Product.Status.REJECTED
    product.save(update_fields=("status", "updated_at"))

    ModerationDecision.objects.create(
        product=product,
        manager=manager,
        decision=ModerationDecision.Decision.REJECTED,
        comment=comment,
    )

    transaction.on_commit(
        lambda: create_notification(
            user=product.owner,
            product=product,
            notification_type=Notification.Type.PRODUCT_REJECTED,
            title="Product rejected",
            body=comment,
        )
    )

    return product


BLOCKING_PUBLICATION_STATUSES = (
    "pending",
    "publishing",
    "active",
    "deactivating",
    "deleting",
)


def product_has_blocking_listings(product: Product) -> bool:
    return product.marketplace_publications.filter(
        status__in=BLOCKING_PUBLICATION_STATUSES
    ).exists()


@transaction.atomic
def change_approved_product_status(
    *,
    product: Product,
    manager,
    status: str,
    comment: str = "",
    expected_catalog_revision: int | None = None,
) -> Product:
    product = Product.objects.select_for_update().get(pk=product.pk)

    if product.status == Product.Status.REJECTED:
        raise ValidationError(
            {
                "detail": (
                    "A rejected product cannot be moved by a manager. "
                    "The seller must edit it and submit it again."
                )
            }
        )

    if product.status != Product.Status.APPROVED:
        raise ValidationError(
            {"detail": "Only an approved product can be moved this way."}
        )

    ensure_catalog_revision(
        product=product,
        expected_revision=expected_catalog_revision,
    )

    if status not in {Product.Status.SUBMITTED, Product.Status.REJECTED}:
        raise ValidationError({"status": "Choose submitted or rejected."})

    if product_has_blocking_listings(product):
        raise ValidationError(
            {
                "detail": (
                    "Deactivate marketplace listings before changing this "
                    "product status."
                )
            }
        )

    if status == Product.Status.REJECTED:
        comment = comment.strip()
        if not comment:
            raise ValidationError({"comment": "A rejection reason is required."})
        product.status = Product.Status.REJECTED
        product.is_available = False
        product.save(update_fields=("status", "is_available", "updated_at"))
        ModerationDecision.objects.create(
            product=product,
            manager=manager,
            decision=ModerationDecision.Decision.REJECTED,
            comment=comment,
        )
        transaction.on_commit(
            lambda: create_notification(
                user=product.owner,
                product=product,
                notification_type=Notification.Type.PRODUCT_REJECTED,
                title="Product rejected",
                body=comment,
            )
        )
        return product

    product.status = Product.Status.SUBMITTED
    product.is_available = False
    product.save(update_fields=("status", "is_available", "updated_at"))
    ModerationDecision.objects.create(
        product=product,
        manager=manager,
        decision=ModerationDecision.Decision.RETURNED_TO_REVIEW,
        comment=comment.strip(),
    )
    return product


@transaction.atomic
def approve_seller_changes(
    *,
    product: Product,
    manager,
    expected_catalog_revision: int | None = None,
) -> tuple[Product, object]:
    product = Product.objects.select_for_update().get(pk=product.pk)
    ensure_catalog_revision(
        product=product,
        expected_revision=expected_catalog_revision,
    )
    if product.status != Product.Status.APPROVED:
        raise ValidationError(
            {"detail": "Only approved products can have seller changes approved."}
        )

    product = apply_pending_seller_changes(product=product)
    try:
        job = create_update_job_for_active_listings(
            product=product,
            requested_by=manager,
        )
    except MarketplacePayloadBuildError:
        job = None
    return product, job
