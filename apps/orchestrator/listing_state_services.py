from __future__ import annotations

from collections import defaultdict

from django.db import transaction

from apps.common.external_json import compact_external_json

from .models import MarketplaceJob, MarketplacePublication


def _operation_for(*, marketplace: str, action: str) -> str | None:
    """Translate a business action to the API operation of each marketplace."""
    if action == "deactivate":
        return (
            MarketplaceJob.Operation.DEACTIVATE
            if marketplace == MarketplacePublication.Marketplace.OTTO
            else MarketplaceJob.Operation.DELETE
        )
    if action == "activate" and marketplace == MarketplacePublication.Marketplace.OTTO:
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

    Hood and Kaufland do not offer a reversible deactivate API.  Their
    deactivation is therefore a delete; only a deactivated OTTO listing can be
    activated later.  Returned ``unavailable`` targets let the web UI explain
    this distinction instead of silently doing the wrong thing.
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
                MarketplaceJob.objects.create(
                    product=product,
                    requested_by=requested_by,
                    operation=operation,
                    requested_channels=list(
                        dict.fromkeys(target["marketplace"] for target in targets)
                    ),
                    request_payload=compact_external_json(
                        {
                            "payloads": {},
                            "target_payloads": {},
                            "accounts": {},
                            "targets": targets,
                            "listing_state_action": action,
                        }
                    ),
                )
            )

        if action == "deactivate" and product.deactivation_requested_at is not None:
            product.deactivation_requested_at = None
            product.save(update_fields=("deactivation_requested_at", "updated_at"))

        return jobs, unavailable
