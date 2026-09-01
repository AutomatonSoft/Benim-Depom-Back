from __future__ import annotations

from django.db import transaction
from rest_framework.exceptions import APIException, ValidationError

from apps.accounts.models import User
from apps.accounts.services import revoke_refresh_tokens
from apps.common.permissions import is_manager
from apps.ean.models import EanCode
from apps.orchestrator.listing_state_services import create_listing_state_jobs
from apps.orchestrator.models import (
    MarketplaceContentGeneration,
    MarketplaceJob,
    MarketplaceListingConfiguration,
    MarketplacePublication,
)
from apps.orchestrator.tasks import execute_marketplace_job
from apps.products.models import Product

IN_PROGRESS_PUBLICATION_STATUSES = (
    MarketplacePublication.Status.PUBLISHING,
    MarketplacePublication.Status.DEACTIVATING,
    MarketplacePublication.Status.DELETING,
)

BUSY_JOB_STATUSES = (
    MarketplaceJob.Status.QUEUED,
    MarketplaceJob.Status.RUNNING,
    MarketplaceJob.Status.PENDING_CONFIRMATION,
)


class SellerPurgeConflict(APIException):
    status_code = 409
    default_code = "seller_purge_conflict"


def _seller_products(seller: User):
    return Product.objects.filter(owner=seller)


def _seller_has_busy_marketplace_work(seller: User) -> bool:
    products = _seller_products(seller)
    if products.filter(
        marketplace_publications__status__in=IN_PROGRESS_PUBLICATION_STATUSES
    ).exists():
        return True
    return MarketplaceJob.objects.filter(
        product__owner=seller,
        status__in=BUSY_JOB_STATUSES,
    ).exists()


def _collect_media_files(products) -> list:
    files = []
    for product in products:
        for image in product.images.all():
            if image.image:
                files.append(image.image)
            if image.processed_image:
                files.append(image.processed_image)
            for generated in image.generated_images.all():
                if generated.image:
                    files.append(generated.image)
    return files


def _detach_eans(*, product_ids: list[int]) -> None:
    eans = EanCode.objects.select_for_update().filter(product_id__in=product_ids)
    for ean in eans:
        if ean.state == EanCode.State.RESERVED:
            ean.state = EanCode.State.AVAILABLE
            ean.assigned_at = None
        ean.product = None
        ean.save(update_fields=("product", "state", "assigned_at"))


def _hard_delete_seller_rows(*, seller: User) -> int:
    products = list(
        _seller_products(seller)
        .prefetch_related("images", "images__generated_images")
        .all()
    )
    product_ids = [product.id for product in products]
    media_files = _collect_media_files(products)

    if product_ids:
        MarketplacePublication.objects.filter(product_id__in=product_ids).delete()
        MarketplaceJob.objects.filter(product_id__in=product_ids).delete()
        MarketplaceListingConfiguration.objects.filter(
            product_id__in=product_ids
        ).delete()
        MarketplaceContentGeneration.objects.filter(product_id__in=product_ids).delete()
        _detach_eans(product_ids=product_ids)
        Product.objects.filter(id__in=product_ids).delete()

    MarketplaceJob.objects.filter(requested_by=seller).delete()
    MarketplaceContentGeneration.objects.filter(requested_by=seller).delete()
    revoke_refresh_tokens(user=seller)
    seller.delete()

    def remove_files():
        for media_file in media_files:
            media_file.delete(save=False)

    transaction.on_commit(remove_files)
    return len(product_ids)


def purge_seller(*, seller: User, requested_by: User) -> dict:
    if seller.role != User.Role.SELLER:
        raise ValidationError({"detail": "Only seller accounts can be deleted."})

    is_self_delete = seller.id == requested_by.id
    if not is_self_delete and not is_manager(requested_by):
        raise ValidationError({"detail": "A seller can only delete their own account."})

    with transaction.atomic():
        seller = User.objects.select_for_update().get(pk=seller.pk)

        if _seller_has_busy_marketplace_work(seller):
            raise SellerPurgeConflict(
                {
                    "detail": (
                        "Wait until in-progress marketplace jobs finish, "
                        "then call delete again."
                    )
                }
            )

        jobs: list[MarketplaceJob] = []
        for product in _seller_products(seller):
            created, _unavailable = create_listing_state_jobs(
                product=product,
                requested_by=requested_by,
                action="deactivate",
            )
            jobs.extend(created)

        if jobs:
            if seller.is_active:
                seller.is_active = False
                seller.save(update_fields=("is_active",))
            revoke_refresh_tokens(user=seller)

            for job in jobs:
                transaction.on_commit(
                    lambda job_id=str(job.id): execute_marketplace_job.delay(job_id)
                )
            transaction.on_commit(lambda: _schedule_finalize(seller.id))
            return {
                "deleted": False,
                "seller_id": seller.id,
                "marketplace_job_ids": [str(job.id) for job in jobs],
                "detail": (
                    "Seller blocked. Marketplace listings are being removed. "
                    "The account and products are deleted automatically after "
                    "those jobs finish."
                ),
            }

        products_deleted = _hard_delete_seller_rows(seller=seller)
        return {
            "deleted": True,
            "seller_id": seller.id,
            "products_deleted": products_deleted,
            "detail": "Seller and products were deleted.",
        }


def try_finalize_seller_purge(*, seller_id: int) -> str:
    """Returns 'deleted', 'busy', or 'missing'."""
    with transaction.atomic():
        seller = (
            User.objects.select_for_update()
            .filter(pk=seller_id, role=User.Role.SELLER)
            .first()
        )
        if seller is None:
            return "missing"
        if _seller_has_busy_marketplace_work(seller):
            return "busy"
        if (
            _seller_products(seller)
            .filter(
                marketplace_publications__status=MarketplacePublication.Status.ACTIVE
            )
            .exists()
        ):
            return "busy"
        _hard_delete_seller_rows(seller=seller)
        return "deleted"


def _schedule_finalize(seller_id: int) -> None:
    from apps.accounts.tasks import finalize_seller_purge

    finalize_seller_purge.delay(seller_id)
