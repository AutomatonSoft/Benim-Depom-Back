from __future__ import annotations

from collections import defaultdict

from django.db import transaction
from django.utils import timezone

from .job_services import create_marketplace_job
from .models import MarketplaceJob, MarketplacePublication
from .publication_services import _status_during_operation


def _operation_for(*, marketplace: str, action: str) -> str | None:
    """Translate a business action to the API operation of each marketplace."""
    reversible = {
        MarketplacePublication.Marketplace.OTTO,
        MarketplacePublication.Marketplace.KAUFLAND,
    }
    if action == "deactivate":
        return (
            MarketplaceJob.Operation.DEACTIVATE
            if marketplace in reversible
            else MarketplaceJob.Operation.DELETE
        )
    if action == "activate" and marketplace in reversible:
        return MarketplaceJob.Operation.ACTIVATE
    return None


def create_listing_state_jobs(
    *,
    product,
    requested_by,
    action: str,
    requested_targets: list[dict[str, str]] | None = None,
) -> tuple[list[MarketplaceJob], list[dict[str, str]]]:
    """Create one job per external operation required by a business action.

    Hood has no reversible hide: deactivation deletes the offer. OTTO and
    Kaufland keep the card (deactivate / amount 0) so stock can bring it
    back without a new catalog review.
    """
    requested_targets = requested_targets or []
    desired_status = (
        MarketplacePublication.Status.ACTIVE
        if action == "deactivate"
        else MarketplacePublication.Status.DEACTIVATED
    )

    with transaction.atomic():
        publications = list(
            MarketplacePublication.objects.select_for_update().filter(
                product=product,
                status=desired_status,
            )
        )
        by_pair = {(item.marketplace, item.account): item for item in publications}

        if requested_targets:
            selected_pairs = {
                (target["marketplace"], target["account"])
                for target in requested_targets
            }
            selected = [by_pair[pair] for pair in selected_pairs if pair in by_pair]
            unavailable = [
                target
                for target in requested_targets
                if (target["marketplace"], target["account"]) not in by_pair
            ]
        else:
            selected = publications
            unavailable = []

        targets_by_operation: dict[str, list[dict[str, str]]] = defaultdict(list)
        for publication in selected:
            operation = _operation_for(
                marketplace=publication.marketplace,
                action=action,
            )
            target = {
                "marketplace": publication.marketplace,
                "account": publication.account,
            }
            if operation is None:
                unavailable.append({**target, "reason": "reactivation_not_supported"})
                continue
            targets_by_operation[operation].append(target)

        if not targets_by_operation:
            return [], unavailable

        jobs: list[MarketplaceJob] = []
        for operation, targets in targets_by_operation.items():
            jobs.append(
                create_marketplace_job(
                    product=product,
                    requested_by=requested_by,
                    operation=operation,
                    targets=targets,
                    extra_payload={"listing_state_action": action},
                )
            )

        jobs_by_operation = {job.operation: job for job in jobs}
        now = timezone.now()
        for publication in selected:
            operation = _operation_for(
                marketplace=publication.marketplace,
                action=action,
            )
            job = jobs_by_operation.get(operation or "")
            if operation is None or job is None:
                continue
            publication.status_before_operation = publication.status
            publication.status = _status_during_operation(operation)
            publication.last_job = job
            publication.last_error = {}
            publication.last_attempt_at = now
            publication.save(
                update_fields=(
                    "status",
                    "status_before_operation",
                    "last_job",
                    "last_error",
                    "last_attempt_at",
                    "updated_at",
                )
            )

        if action == "deactivate" and product.deactivation_requested_at is not None:
            product.deactivation_requested_at = None
            product.save(update_fields=("deactivation_requested_at", "updated_at"))

        return jobs, unavailable
