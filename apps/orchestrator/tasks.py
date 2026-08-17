from __future__ import annotations

from typing import Any

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from apps.marketplace.hood.services import execute as execute_hood
from django.db import transaction

from apps.ean.services import consume_ean_code

from .client import MarketplaceClient
from .models import MarketplaceJob
from .publication_services import (
    PUBLICATION_OPERATIONS,
    mark_publication_failed,
    mark_publication_succeeded,
    start_publication_attempt,
)


def path_with_ean(endpoint: str, ean: str) -> str:
    endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"

    if "{ean}" in endpoint:
        return endpoint.format(ean=ean)

    return f"{endpoint.rstrip('/')}/{ean}"


def normalize_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value

    return {"raw": value}


def extract_external_id(
    *,
    marketplace: str,
    response_payload: dict[str, Any],
) -> str:
    possible_keys = {
        "hood": ("item_id", "itemID", "id"),
        "otto": ("id", "sku", "productId"),
        "kaufland": ("id", "product_id", "productId"),
    }

    for key in possible_keys.get(marketplace, ()):
        value = response_payload.get(key)

        if value is not None:
            return str(value)

    return ""


def get_targets_for_job(job: MarketplaceJob) -> list[dict[str, str]]:
    """
    New format:
    targets = [{"marketplace": "hood", "account": "jv"}]

    Legacy format:
    channels = ["hood"]
    accounts = {"hood": "jv"}

    If no account is supplied in the legacy format, both accounts are used.
    """
    targets = job.request_payload.get("targets", [])

    if targets:
        return [
            {
                "marketplace": target["marketplace"],
                "account": target["account"],
            }
            for target in targets
        ]

    accounts = job.request_payload.get("accounts", {})
    result = []

    for marketplace in job.requested_channels:
        selected_account = accounts.get(marketplace)

        if selected_account:
            result.append(
                {
                    "marketplace": marketplace,
                    "account": selected_account,
                }
            )
            continue

        result.extend(
            [
                {"marketplace": marketplace, "account": "jv"},
                {"marketplace": marketplace, "account": "xl"},
            ]
        )

    return result


def get_product_ean(*, product, account: str) -> str:
    if account == "jv":
        return product.ean_jv.strip()

    if account == "xl":
        return product.ean_xl.strip()

    raise ValueError("Account must be jv or xl.")


def request_for_non_hood_channel(
    client: MarketplaceClient,
    *,
    marketplace: str,
    operation: str,
    ean: str,
    account: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if marketplace == "kaufland":
        if operation == MarketplaceJob.Operation.SEARCH:
            return client.request(
                settings.KAUFLAND_API_BASE_URL,
                "GET",
                path_with_ean(
                    settings.KAUFLAND_API_GET_BY_EAN_ENDPOINT,
                    ean,
                ),
                params={"controller": account},
            )

        if operation == MarketplaceJob.Operation.PUBLISH:
            return client.request(
                settings.KAUFLAND_API_BASE_URL,
                "PUT",
                settings.KAUFLAND_API_CREATE_ENDPOINT,
                params={"controller": account},
                payload={
                    "ean": ean,
                    "controller": account,
                    **payload,
                },
            )

        if operation == MarketplaceJob.Operation.UPDATE:
            return client.request(
                settings.KAUFLAND_API_BASE_URL,
                "PATCH",
                path_with_ean(
                    settings.KAUFLAND_API_UPDATE_ENDPOINT,
                    ean,
                ),
                params={"controller": account},
                payload={
                    "ean": ean,
                    "controller": account,
                    **payload,
                },
            )

        if operation == MarketplaceJob.Operation.DELETE:
            return client.request(
                settings.KAUFLAND_API_BASE_URL,
                "DELETE",
                path_with_ean(
                    settings.KAUFLAND_API_DELETE_ENDPOINT,
                    ean,
                ),
                params={"controller": account},
            )

        if operation == MarketplaceJob.Operation.DEACTIVATE:
            return {
                "ok": False,
                "status_code": 400,
                "details": {
                    "code": "kaufland_deactivate_not_supported",
                    "detail": "Use delete for Kaufland publications.",
                },
            }

    if marketplace == "otto":
        if operation == MarketplaceJob.Operation.SEARCH:
            return client.request(
                settings.OTTO_API_BASE_URL,
                "GET",
                settings.OTTO_API_PRODUCTS_ENDPOINT,
                params={
                    "ean": ean,
                    "page": 0,
                    "limit": 10,
                    "controller": account,
                },
            )

        if operation in {
            MarketplaceJob.Operation.PUBLISH,
            MarketplaceJob.Operation.UPDATE,
        }:
            body = dict(payload)
            body.setdefault("productReference", ean)
            body.setdefault("sku", ean)
            body.setdefault("ean", ean)

            return client.request(
                settings.OTTO_API_BASE_URL,
                "POST",
                settings.OTTO_API_UPSERT_ENDPOINT,
                params={"controller": account},
                payload=[body],
            )

        if operation == MarketplaceJob.Operation.ACTIVATE:
            return client.request(
                settings.OTTO_API_BASE_URL,
                "POST",
                settings.OTTO_API_ACTIVATE_ENDPOINT,
                payload={
                    "ean": ean,
                    "controller": account,
                },
            )
        if operation == MarketplaceJob.Operation.DEACTIVATE:
            return client.request(
                settings.OTTO_API_BASE_URL,
                "POST",
                settings.OTTO_API_DEACTIVATE_ENDPOINT,
                payload={
                    "ean": ean,
                    "controller": account,
                },
            )

        if operation == MarketplaceJob.Operation.DELETE:
            return {
                "ok": False,
                "status_code": 400,
                "details": {
                    "code": "otto_delete_not_supported",
                    "detail": "Use deactivate for OTTO publications.",
                },
            }

    raise ValueError(f"Unsupported marketplace: {marketplace}.")


@shared_task(bind=True)
def execute_marketplace_job(self, job_id: str) -> None:
    job = MarketplaceJob.objects.select_related("product").get(pk=job_id)

    if job.status != MarketplaceJob.Status.QUEUED:
        return

    job.status = MarketplaceJob.Status.RUNNING
    job.started_at = timezone.now()
    job.celery_task_id = self.request.id or ""
    job.error = {}
    job.save(
        update_fields=(
            "status",
            "started_at",
            "celery_task_id",
            "error",
        )
    )

    payloads = job.request_payload.get("payloads", {})
    target_payloads = job.request_payload.get("target_payloads", {})
    targets = get_targets_for_job(job)
    client = MarketplaceClient(str(job.request_id))
    results = []

    for target in targets:
        marketplace = target["marketplace"]
        account = target["account"]
        payload = target_payloads.get(
            f"{marketplace}:{account}",
            payloads.get(marketplace, {}),
        )
        publication = None
        ean_consumed = False
        try:
            ean = get_product_ean(
                product=job.product,
                account=account,
            )

            if not ean:
                raise ValueError(
                    f"Product does not have an EAN for account '{account}'."
                )

            if job.operation in PUBLICATION_OPERATIONS:
                publication = start_publication_attempt(
                    job=job,
                    marketplace=marketplace,
                    account=account,
                    ean=ean,
                    request_payload=payload,
                )

            if marketplace == "hood":
                result = execute_hood(
                    product=job.product,
                    operation=job.operation,
                    ean=ean,
                    account=account,
                    payload=payload,
                    request_id=str(job.request_id),
                )
            else:
                result = request_for_non_hood_channel(
                    client,
                    marketplace=marketplace,
                    operation=job.operation,
                    ean=ean,
                    account=account,
                    payload=payload,
                )

        except (TypeError, ValueError) as exc:
            result = {
                "ok": False,
                "status_code": 400,
                "details": {
                    "code": "marketplace_dispatch_failed",
                    "reason": str(exc),
                },
            }
            ean = ""

        response_payload = normalize_payload(
            result.get("details", {})
        )

        if publication is not None:
            if result["ok"]:
                with transaction.atomic():
                    publication, first_successful_publication = (
                        mark_publication_succeeded(
                            publication=publication,
                            job=job,
                            response_payload=response_payload,
                            external_id=extract_external_id(
                                marketplace=marketplace,
                                response_payload=response_payload,
                            ),
                        )
                    )

                    if (
                        job.operation == MarketplaceJob.Operation.PUBLISH
                        and first_successful_publication
                    ):
                        ean_consumed = consume_ean_code(
                            product=job.product,
                            account=account,
                        )
            else:
                publication = mark_publication_failed(
                    publication=publication,
                    job=job,
                    error_payload={
                        "status_code": result["status_code"],
                        "details": response_payload,
                    },
                    response_payload=response_payload,
                )
                first_successful_publication = False
        else:
            first_successful_publication = False

        results.append(
            {
                "marketplace": marketplace,
                "account": account,
                "ean": ean,
                "publication_id": (
                    publication.id if publication is not None else None
                ),
                "first_successful_publication": (
                    first_successful_publication
                ),
                "ean_consumed": ean_consumed,
                **result,
            }
        )

    success_count = sum(result["ok"] for result in results)

    if success_count == len(results):
        job.status = MarketplaceJob.Status.SUCCEEDED
    elif success_count == 0:
        job.status = MarketplaceJob.Status.FAILED
    else:
        job.status = MarketplaceJob.Status.PARTIAL  

    job.results = results
    job.finished_at = timezone.now()
    job.save(
        update_fields=(
            "status",
            "results",
            "finished_at",
        )
    )