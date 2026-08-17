from __future__ import annotations

from typing import Any

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import MarketplaceJob, MarketplacePublication


PUBLICATION_OPERATIONS = {
    MarketplaceJob.Operation.PUBLISH,
    MarketplaceJob.Operation.UPDATE,
    MarketplaceJob.Operation.DELETE,
    MarketplaceJob.Operation.DEACTIVATE,
    MarketplaceJob.Operation.ACTIVATE,
}


def _validate_publication_input(
    *,
    marketplace: str,
    account: str,
    ean: str,
) -> None:
    if marketplace not in MarketplacePublication.Marketplace.values:
        raise ValueError(f"Unsupported marketplace: {marketplace}.")

    if account not in MarketplacePublication.Account.values:
        raise ValueError(f"Unsupported account: {account}.")

    if not ean:
        raise ValueError("EAN is required for marketplace publication.")


def _status_after_success(operation: str) -> str:
    statuses = MarketplacePublication.Status

    mapping = {
        MarketplaceJob.Operation.PUBLISH: statuses.ACTIVE,
        MarketplaceJob.Operation.UPDATE: statuses.ACTIVE,
        MarketplaceJob.Operation.DEACTIVATE: statuses.DEACTIVATED,
        MarketplaceJob.Operation.DELETE: statuses.DELETED,
        MarketplaceJob.Operation.ACTIVATE: statuses.ACTIVE,
    }

    try:
        return mapping[operation]
    except KeyError as exc:
        raise ValueError(
            f"Operation '{operation}' has no publication status."
        ) from exc


@transaction.atomic
def start_publication_attempt(
    *,
    job: MarketplaceJob,
    marketplace: str,
    account: str,
    ean: str,
    request_payload: dict[str, Any],
) -> MarketplacePublication:
    if job.operation not in PUBLICATION_OPERATIONS:
        raise ValueError(
            "Only publish, update, activate, delete and deactivate "
            "operations create marketplace publications."
        )

    _validate_publication_input(
        marketplace=marketplace,
        account=account,
        ean=ean,
    )

    publications = MarketplacePublication.objects.select_for_update()

    if job.operation == MarketplaceJob.Operation.PUBLISH:
        publication, created = publications.get_or_create(
            product=job.product,
            marketplace=marketplace,
            account=account,
            defaults={
                "ean": ean,
                "status": MarketplacePublication.Status.PENDING,
            },
        )
    else:
        publication = publications.filter(
            product=job.product,
            marketplace=marketplace,
            account=account,
        ).first()
        created = False

        if publication is None:
            raise ValueError(
                f"Cannot {job.operation}: this product was never "
                f"published on {marketplace}/{account}."
            )

    if not created and publication.ean != ean:
        raise ValueError(
            "Publication EAN does not match the EAN assigned to this product."
        )

    allowed_statuses = {
        MarketplaceJob.Operation.UPDATE: {
            MarketplacePublication.Status.ACTIVE,
        },
        MarketplaceJob.Operation.DEACTIVATE: {
            MarketplacePublication.Status.ACTIVE,
        },
        MarketplaceJob.Operation.ACTIVATE: {
            MarketplacePublication.Status.DEACTIVATED,
        },
        MarketplaceJob.Operation.DELETE: {
            MarketplacePublication.Status.ACTIVE,
            MarketplacePublication.Status.DEACTIVATED,
        },
    }

    allowed = allowed_statuses.get(job.operation)

    if allowed is not None and publication.status not in allowed:
        raise ValueError(
            f"Cannot {job.operation} publication with status "
            f"'{publication.status}'."
        )

    publication.last_job = job
    publication.last_request = request_payload
    publication.last_response = {}
    publication.last_error = {}
    publication.last_attempt_at = timezone.now()

    MarketplacePublication.objects.filter(pk=publication.pk).update(
        attempt_count=F("attempt_count") + 1,
    )

    publication.save(
        update_fields=(
            "last_job",
            "last_request",
            "last_response",
            "last_error",
            "last_attempt_at",
            "updated_at",
        )
    )
    publication.refresh_from_db(fields=("attempt_count",))

    return publication


@transaction.atomic
def mark_publication_succeeded(
    *,
    publication: MarketplacePublication,
    job: MarketplaceJob,
    response_payload: dict[str, Any],
    external_id: str = "",
) -> tuple[MarketplacePublication, bool]:
    """
    Saves a successful response.

    Returns:
        publication: refreshed publication record;
        first_successful_publication: True only once for this row.
    """
    publication = MarketplacePublication.objects.select_for_update().get(
        pk=publication.pk
    )

    target_status = _status_after_success(job.operation)
    first_successful_publication = (
        target_status == MarketplacePublication.Status.ACTIVE
        and publication.published_at is None
    )

    publication.status = target_status
    publication.last_job = job
    publication.last_response = response_payload
    publication.last_error = {}

    if external_id:
        publication.external_id = str(external_id)

    now = timezone.now()

    if (
        target_status == MarketplacePublication.Status.ACTIVE
        and publication.published_at is None
    ):
        publication.published_at = now

    if (
        target_status == MarketplacePublication.Status.DEACTIVATED
        and publication.deactivated_at is None
    ):
        publication.deactivated_at = now

    if (
        target_status == MarketplacePublication.Status.DELETED
        and publication.deleted_at is None
    ):
        publication.deleted_at = now

    publication.save(
        update_fields=(
            "status",
            "last_job",
            "last_response",
            "last_error",
            "external_id",
            "published_at",
            "deactivated_at",
            "deleted_at",
            "updated_at",
        )
    )

    return publication, first_successful_publication


@transaction.atomic
def mark_publication_failed(
    *,
    publication: MarketplacePublication,
    job: MarketplaceJob,
    error_payload: dict[str, Any],
    response_payload: dict[str, Any] | None = None,
) -> MarketplacePublication:
    """
    Stores an error without destroying the last confirmed external state.

    Example:
    an active Hood item stays active if an update attempt fails.
    """
    publication = MarketplacePublication.objects.select_for_update().get(
        pk=publication.pk
    )

    publication.last_job = job
    publication.last_error = error_payload

    if response_payload is not None:
        publication.last_response = response_payload

    # If the product was never published and the first publish fails,
    # it has no confirmed external state, so mark it as failed.
    if (
        job.operation == MarketplaceJob.Operation.PUBLISH
        and publication.status == MarketplacePublication.Status.PENDING
    ):
        publication.status = MarketplacePublication.Status.FAILED

    publication.save(
        update_fields=(
            "status",
            "last_job",
            "last_response",
            "last_error",
            "updated_at",
        )
    )

    return publication