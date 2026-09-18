from __future__ import annotations

from apps.marketplace.hood.payload_builder import (
    HoodPayloadValidationError,
    build_hood_payload,
)
from apps.marketplace.kaufland.payload_builder import (
    KauflandPayloadValidationError,
    build_kaufland_create_payload,
    build_kaufland_update_payload,
)
from apps.marketplace.otto.payload_builder import (
    OttoPayloadValidationError,
    build_otto_payload,
)

from .models import (
    MarketplaceJob,
    MarketplaceListingConfiguration,
    MarketplacePublication,
)


def payload_contains_truncation_markers(value) -> bool:
    """True when compact_external_json markers leaked into a stored payload."""
    if isinstance(value, dict):
        if value.get("_truncated") is True:
            return True
        return any(payload_contains_truncation_markers(item) for item in value.values())
    if isinstance(value, list):
        return any(payload_contains_truncation_markers(item) for item in value)
    return False


class MarketplacePayloadBuildError(Exception):
    def __init__(self, data: dict):
        self.data = data
        super().__init__(data.get("detail", "Marketplace payload is invalid"))


IN_PROGRESS_JOB_STATUSES = (
    MarketplaceJob.Status.QUEUED,
    MarketplaceJob.Status.RUNNING,
    MarketplaceJob.Status.PENDING_CONFIRMATION,
)

IN_PROGRESS_PUBLICATION_STATUSES = (
    MarketplacePublication.Status.PENDING,
    MarketplacePublication.Status.PUBLISHING,
    MarketplacePublication.Status.DEACTIVATING,
    MarketplacePublication.Status.DELETING,
)


def _configuration_for(*, product, marketplace: str, account: str) -> dict:
    configuration = MarketplaceListingConfiguration.objects.filter(
        product=product,
        marketplace=marketplace,
        account=account,
    ).first()
    if configuration is None:
        return {}
    return configuration.configuration if configuration.configuration else {}


def build_target_payloads(
    *,
    product,
    operation: str,
    targets: list[dict[str, str]],
) -> dict:
    target_payloads: dict = {}

    for target in targets:
        marketplace = target["marketplace"]
        account = target["account"]
        if marketplace not in {"otto", "hood", "kaufland"}:
            continue

        configuration_data = _configuration_for(
            product=product,
            marketplace=marketplace,
            account=account,
        )

        try:
            if marketplace == "otto":
                if operation not in {
                    MarketplaceJob.Operation.PUBLISH,
                    MarketplaceJob.Operation.UPDATE,
                }:
                    continue
                payload = build_otto_payload(
                    product=product,
                    account=account,
                    configuration=configuration_data,
                )
            elif marketplace == "hood":
                if operation not in {
                    MarketplaceJob.Operation.PUBLISH,
                    MarketplaceJob.Operation.UPDATE,
                }:
                    continue
                payload = build_hood_payload(
                    product=product,
                    account=account,
                    configuration=configuration_data,
                )
            elif operation == MarketplaceJob.Operation.ACTIVATE:
                variant = product.variants.first()
                payload = {
                    "amount": int(variant.quantity) if variant is not None else 0,
                }
            elif operation == MarketplaceJob.Operation.PUBLISH:
                payload = build_kaufland_create_payload(
                    product=product,
                    account=account,
                    configuration=configuration_data,
                )
            elif operation == MarketplaceJob.Operation.UPDATE:
                payload = build_kaufland_update_payload(
                    product=product,
                    account=account,
                    configuration=configuration_data,
                )
            else:
                continue
        except OttoPayloadValidationError as exc:
            raise MarketplacePayloadBuildError(
                {
                    "detail": (
                        "OTTO listing is not ready for publication. "
                        "Fix the manager configuration first."
                    ),
                    "target": target,
                    "errors": exc.errors,
                }
            ) from exc
        except HoodPayloadValidationError as exc:
            raise MarketplacePayloadBuildError(
                {
                    "detail": (
                        "Hood listing is not ready for publication. "
                        "Fix the manager configuration first."
                    ),
                    "target": target,
                    "errors": exc.errors,
                }
            ) from exc
        except KauflandPayloadValidationError as exc:
            action = (
                "publication"
                if operation == MarketplaceJob.Operation.PUBLISH
                else "update"
            )
            raise MarketplacePayloadBuildError(
                {
                    "detail": (
                        f"Kaufland listing is not ready for {action}. "
                        "Fix the manager configuration first."
                    ),
                    "target": target,
                    "errors": exc.errors,
                }
            ) from exc

        target_payloads[f"{marketplace}:{account}"] = payload

    return target_payloads


def create_marketplace_job(
    *,
    product,
    requested_by,
    operation: str,
    targets: list[dict[str, str]],
    channels: list[str] | None = None,
    request_payloads: dict | None = None,
    target_payloads: dict | None = None,
    accounts: dict | None = None,
    extra_payload: dict | None = None,
) -> MarketplaceJob:
    payloads = dict(request_payloads or {})
    if target_payloads is None and operation in {
        MarketplaceJob.Operation.PUBLISH,
        MarketplaceJob.Operation.UPDATE,
        MarketplaceJob.Operation.ACTIVATE,
    }:
        target_payloads = build_target_payloads(
            product=product,
            operation=operation,
            targets=targets,
        )
    elif target_payloads is None:
        target_payloads = {}

    payload = {
        "payloads": payloads,
        "target_payloads": target_payloads,
        "accounts": accounts or {},
        "targets": targets,
    }
    if extra_payload:
        payload.update(extra_payload)

    return MarketplaceJob.objects.create(
        product=product,
        requested_by=requested_by,
        operation=operation,
        requested_channels=channels
        or list(dict.fromkeys(target["marketplace"] for target in targets)),
        # Keep the full outbound marketplace body. compact_external_json is for
        # diagnostics only — applying it here truncated OTTO attribute values
        # (depth limit) and those corrupted payloads were sent to the API.
        request_payload=payload,
    )


def create_update_job_for_active_listings(
    *, product, requested_by
) -> MarketplaceJob | None:
    targets = [
        {"marketplace": publication.marketplace, "account": publication.account}
        for publication in MarketplacePublication.objects.filter(
            product=product,
            status=MarketplacePublication.Status.ACTIVE,
        ).order_by("marketplace", "account")
    ]
    if not targets:
        return None

    return create_marketplace_job(
        product=product,
        requested_by=requested_by,
        operation=MarketplaceJob.Operation.UPDATE,
        targets=targets,
    )
