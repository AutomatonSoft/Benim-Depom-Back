"""Reconcile local listing statuses without sending publication requests."""

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.common.external_json import compact_external_json
from apps.ean.services import consume_ean_code
from apps.marketplace.hood.client import HoodClient, path_with_ean
from apps.marketplace.kaufland.status import (
    classify_kaufland_status_outcome,
    extract_kaufland_external_id,
)
from apps.products.models import Product

from .client import MarketplaceClient
from .job_services import create_marketplace_job
from .models import MarketplaceJob, MarketplacePublication


def validate_reconciliation(publication):
    if publication.status not in {"failed", "active", "deactivated", "deleted"}:
        raise ValidationError({"detail": "This publication cannot be reconciled now."})
    expected_ean = getattr(publication.product, f"ean_{publication.account}")
    if not publication.ean or publication.ean != expected_ean:
        raise ValidationError({"detail": "Publication EAN does not match the product."})
    if MarketplaceJob.objects.filter(
        product=publication.product,
        status__in={"queued", "running", "pending_confirmation"},
    ).exists():
        raise ValidationError(
            {"detail": "Wait until the current marketplace requests finish."}
        )


@transaction.atomic
def create_reconciliation(*, publication_id, user, action, target_status=None):
    # Serialize manager confirmations for the same product.
    product_id = MarketplacePublication.objects.values_list(
        "product_id", flat=True
    ).get(pk=publication_id)
    Product.objects.select_for_update().get(pk=product_id)
    publication = (
        MarketplacePublication.objects.select_for_update()
        .select_related("product")
        .get(pk=publication_id)
    )
    validate_reconciliation(publication)
    if action == "set_manual":
        if target_status not in {"active", "deactivated", "failed"}:
            raise ValidationError({"status": "Select a publication status."})
        if target_status == "deactivated" and publication.marketplace != "otto":
            target_status = MarketplacePublication.Status.DELETED
    else:
        target_status = None
    if action == "confirm_manual" and publication.status != "failed":
        raise ValidationError(
            {"detail": "Only failed publications can be confirmed manually."}
        )
    job = create_marketplace_job(
        product=publication.product,
        requested_by=user,
        operation=MarketplaceJob.Operation.SEARCH,
        targets=[
            {"marketplace": publication.marketplace, "account": publication.account}
        ],
        extra_payload={
            "reconciliation_action": action,
            "publication_id": publication.pk,
            "publication_updated_at": publication.updated_at.isoformat(),
            "previous_status": publication.status,
            "target_status": target_status,
            "previous_error": publication.last_error,
            "requested_by_name": user.get_username(),
        },
    )
    if action in {"confirm_manual", "set_manual"}:
        finish_reconciliation(
            job,
            confirmed=True,
            details={"manual_confirmation": True, "status": target_status or "active"},
        )
    return job


@transaction.atomic
def finish_reconciliation(job, *, confirmed, details, external_id=""):
    Product.objects.select_for_update().get(pk=job.product_id)
    publication = (
        MarketplacePublication.objects.select_for_update()
        .select_related("product")
        .get(pk=job.request_payload["publication_id"], product_id=job.product_id)
    )
    unchanged = publication.updated_at.isoformat() == job.request_payload[
        "publication_updated_at"
    ] and publication.ean == getattr(publication.product, f"ean_{publication.account}")
    other_job_running = (
        MarketplaceJob.objects.filter(
            product_id=job.product_id,
            status__in={"queued", "running", "pending_confirmation"},
        )
        .exclude(pk=job.pk)
        .exists()
    )
    if not unchanged or other_job_running:
        confirmed = False
        details = {
            "detail": "Publication changed during the check. Check its status again."
        }

    now = timezone.now()
    if confirmed:
        target_status = (
            job.request_payload.get("target_status")
            or MarketplacePublication.Status.ACTIVE
        )
        if target_status != MarketplacePublication.Status.FAILED:
            try:
                consume_ean_code(
                    product=publication.product, account=publication.account
                )
            except (ObjectDoesNotExist, DjangoValidationError) as exc:
                raise ValidationError(
                    {
                        "detail": "Unable to confirm publication: check the EAN assigned to the product."
                    }
                ) from exc
        publication.status = target_status
        publication.status_before_operation = ""
        publication.last_error = (
            (
                job.request_payload.get("previous_error")
                or {"detail": "Status changed manually."}
            )
            if target_status == MarketplacePublication.Status.FAILED
            else {}
        )
        publication.last_job = job
        if target_status == MarketplacePublication.Status.ACTIVE:
            publication.published_at = publication.published_at or now
        elif target_status == MarketplacePublication.Status.DEACTIVATED:
            publication.deactivated_at = now
        elif target_status == MarketplacePublication.Status.DELETED:
            publication.deleted_at = now
        if external_id:
            publication.external_id = external_id
        publication.last_response = {
            **(
                publication.last_response
                if isinstance(publication.last_response, dict)
                else {}
            ),
            "reconciliation": compact_external_json(
                {
                    "action": job.request_payload["reconciliation_action"],
                    "requested_by_id": job.requested_by_id,
                    "checked_at": now.isoformat(),
                    "details": details,
                }
            ),
        }
        publication.save(
            update_fields=(
                "status",
                "status_before_operation",
                "last_error",
                "last_job",
                "published_at",
                "deactivated_at",
                "deleted_at",
                "external_id",
                "last_response",
                "updated_at",
            )
        )

    job.status = (
        MarketplaceJob.Status.SUCCEEDED if confirmed else MarketplaceJob.Status.FAILED
    )
    job.started_at = job.started_at or now
    job.finished_at = now
    job.results = [
        {
            "marketplace": publication.marketplace,
            "account": publication.account,
            "ok": confirmed,
            "details": compact_external_json(details),
        }
    ]
    job.error = (
        {}
        if confirmed
        else {
            "detail": "Publication was not confirmed. Local status was not changed.",
        }
    )
    job.save(update_fields=("status", "started_at", "finished_at", "results", "error"))


def check_publication(job):
    # Import existing status helpers lazily to avoid the tasks import cycle.
    from .tasks import (
        get_kaufland_product_status,
        get_otto_marketplace_item,
        get_otto_marketplace_status,
    )

    publication = MarketplacePublication.objects.get(
        pk=job.request_payload["publication_id"]
    )
    client = MarketplaceClient(str(job.request_id))
    external_id = ""
    if publication.marketplace == "otto":
        result = get_otto_marketplace_status(
            client, sku=publication.ean, account=publication.account
        )
        details = result.get("details", {})
        details = details if isinstance(details, dict) else {}
        item = get_otto_marketplace_item(details, sku=publication.ean)
        confirmed = bool(item and item.get("status") == "ONLINE") or (
            str(details.get("sku", "")) == publication.ean
            and details.get("found") is True
            and details.get("is_live") is True
        )
        external_id = str((item or details).get("moin") or "")
    elif publication.marketplace == "kaufland":
        result = get_kaufland_product_status(
            client, ean=publication.ean, account=publication.account
        )
        details = result.get("details", {})
        details = details if isinstance(details, dict) else {}
        confirmed = (
            classify_kaufland_status_outcome(operation="publish", payload=details)
            == "success"
        )
        external_id = extract_kaufland_external_id(details)
    else:
        result = HoodClient(str(job.request_id)).request(
            "GET",
            path_with_ean(settings.HOOD_API_GET_ENDPOINT, publication.ean),
            account=publication.account,
        )
        details = result.get("details", {})
        details = details if isinstance(details, dict) else {}
        # Unknown response shapes require manual confirmation, never HTTP 200 alone.
        external_id = str(details.get("item_id") or "")
        confirmed = (
            bool(external_id)
            and details.get("success") is True
            and (
                details.get("is_live") is True
                or str(details.get("status", "")).upper()
                in {"ACTIVE", "LIVE", "ONLINE", "RUNNING"}
            )
        )
    finish_reconciliation(
        job,
        confirmed=bool(result.get("ok") and confirmed),
        details=details,
        external_id=external_id,
    )
