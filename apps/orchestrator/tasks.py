from __future__ import annotations

from datetime import timedelta
import re
from typing import Any

from celery import shared_task
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.marketplace.hood.services import execute as execute_hood
from apps.marketplace.otto.constants import OttoMarketplaceStatus
from django.db import transaction

from apps.ean.services import consume_ean_code

from .client import MarketplaceClient
from .models import (
    MarketplaceContentGeneration,
    MarketplaceJob,
    MarketplacePublication,
)
from .publication_services import (
    PUBLICATION_OPERATIONS,
    mark_publication_awaiting_confirmation,
    mark_publication_failed,
    mark_publication_succeeded,
    start_publication_attempt,
)

from apps.common.openai_text_service import (
    OpenAITextService,
    OpenAITextServiceError,
)
from .ai_content import (
    GeneratedContentValidationError,
    build_universal_content_request,
    validate_universal_content,
)

def path_with_ean(endpoint: str, ean: str) -> str:
    endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"

    if "{ean}" in endpoint:
        return endpoint.format(ean=ean)

    return f"{endpoint.rstrip('/')}/{ean}"


def path_with_process_id(endpoint: str, process_id: str) -> str:
    """Build a configured OTTO process-result endpoint safely."""
    endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"
    if "{process_id}" not in endpoint:
        raise ValueError("OTTO process endpoint must contain '{process_id}'.")
    return endpoint.format(process_id=process_id)


def normalize_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value

    return {"raw": value}

def get_otto_async_process_payload(
    response_payload: dict[str, Any],
) -> dict[str, Any]:
    """
    OTTO upsert returns process fields at the root level.

    OTTO activate/deactivate may return them inside:
    {
        "success": true,
        "response": {
            "state": "pending",
            "links": [...],
            "pingAfter": "..."
        }
    }
    """

    nested_response = response_payload.get("response")

    if (
        isinstance(nested_response, dict)
        and "state" in nested_response
    ):
        return nested_response

    return response_payload


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


def extract_otto_process_id(response_payload: dict[str, Any]) -> str:
    """Extract the OTTO update-task ID from root or nested API response."""

    process_payload = get_otto_async_process_payload(
        response_payload
    )

    for link in process_payload.get("links", []):
        if not isinstance(link, dict) or link.get("rel") != "self":
            continue

        href = str(link.get("href", ""))
        match = re.search(r"/update-tasks/([^/?]+)", href)

        if match:
            return match.group(1)

    return ""


def is_otto_process_pending(result: dict[str, Any]) -> bool:
    """
    Returns True when OTTO accepted a command but still processes it.

    Supports both:
    {"state": "pending"}
    and:
    {"response": {"state": "pending"}}
    """

    if not result.get("ok"):
        return False

    response_payload = result.get("details", {})

    if not isinstance(response_payload, dict):
        return False

    process_payload = get_otto_async_process_payload(
        response_payload
    )

    return str(process_payload.get("state", "")).lower() in {
        "pending",
        "in_progress",
        "running",
    }


def get_otto_process_result(
    client: MarketplaceClient,
    *,
    process_id: str,
    account: str,
) -> dict[str, Any] | None:
    """Return the final OTTO result, or ``None`` while it is still pending.

    The wrapper exposes separate endpoints for failed, succeeded and unchanged
    process results.  A result endpoint only returns a useful body when that
    final state has been reached.
    """
    checks = (
        ("failed", settings.OTTO_API_PROCESS_FAILED_ENDPOINT),
        ("succeeded", settings.OTTO_API_PROCESS_SUCCESS_ENDPOINT),
        ("unchanged", settings.OTTO_API_PROCESS_UNCHANGED_ENDPOINT),
    )
    for outcome, endpoint in checks:
        result = client.request(
            settings.OTTO_API_BASE_URL,
            "GET",
            path_with_process_id(endpoint, process_id),
            params={"controller": account},
        )
        details = result.get("details", {})
        if result.get("ok") and isinstance(details, dict) and details.get("results"):
            return {"outcome": outcome, "result": result}
    return None

def get_otto_marketplace_status(
    client: MarketplaceClient,
    *,
    sku: str,
    account: str,
) -> dict[str, Any]:
    """Gets the real listing status from OTTO marketplace."""

    return client.request(
        settings.OTTO_API_BASE_URL,
        "GET",
        settings.OTTO_API_MARKETPLACE_STATUS_ENDPOINT,
        params={
            "sku": sku,
            "controller": account,
        },
    )


def get_otto_marketplace_item(
    response_payload: dict[str, Any],
    *,
    sku: str,
) -> dict[str, Any] | None:
    """Returns the item matching our SKU from OTTO marketplace-status."""

    items = response_payload.get("marketPlaceStatus", [])

    if not isinstance(items, list):
        return None

    for item in items:
        if isinstance(item, dict) and str(item.get("sku", "")) == sku:
            return item

    return None

def get_expected_otto_marketplace_statuses(
    operation: str,
) -> tuple[str, ...]:
    """
    Returns real OTTO statuses that confirm the requested operation.
    """

    if operation in {
        MarketplaceJob.Operation.PUBLISH,
        MarketplaceJob.Operation.UPDATE,
        MarketplaceJob.Operation.ACTIVATE,
    }:
        return (
            OttoMarketplaceStatus.ONLINE,
        )

    if operation == MarketplaceJob.Operation.DEACTIVATE:
        return (
            OttoMarketplaceStatus.INACTIVE,
        )

    return ()

def _set_job_target_result(
    *,
    job: MarketplaceJob,
    marketplace: str,
    account: str,
    replacement: dict[str, Any],
) -> None:
    results = list(job.results or [])
    for index, result in enumerate(results):
        if (
            result.get("marketplace") == marketplace
            and result.get("account") == account
        ):
            results[index] = replacement
            break
    else:
        results.append(replacement)
    job.results = results


def _refresh_job_status(job: MarketplaceJob) -> None:
    results = list(job.results or [])
    if any(item.get("awaiting_marketplace_confirmation") for item in results):
        job.status = MarketplaceJob.Status.PENDING_CONFIRMATION
        job.finished_at = None
    elif results and all(item.get("ok") for item in results):
        job.status = MarketplaceJob.Status.SUCCEEDED
        job.finished_at = timezone.now()
    elif results and not any(item.get("ok") for item in results):
        job.status = MarketplaceJob.Status.FAILED
        job.finished_at = timezone.now()
    else:
        job.status = MarketplaceJob.Status.PARTIAL
        job.finished_at = timezone.now()
    job.save(update_fields=("status", "results", "finished_at"))


def _next_otto_poll_time(response_payload: dict[str, Any]):
    """Uses OTTO's pingAfter from root or nested async response."""

    process_payload = get_otto_async_process_payload(
        response_payload
    )

    raw_value = process_payload.get("pingAfter")

    parsed = (
        parse_datetime(raw_value)
        if isinstance(raw_value, str)
        else None
    )

    if parsed is not None:
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed)

        return max(
            parsed,
            timezone.now() + timedelta(seconds=1),
        )

    return timezone.now() + timedelta(
        seconds=settings.OTTO_PROCESS_POLL_INTERVAL_SECONDS
    )


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
    payload: dict[str, Any] | list[dict[str, Any]],
) -> dict[str, Any]:
    if marketplace == "kaufland":
        if operation == MarketplaceJob.Operation.SEARCH:
            return client.request(
                settings.KAUFLAND_API_BASE_URL,
                "GET",
                settings.KAUFLAND_API_GET_BY_EAN_ENDPOINT,
                params={
                    "ean": ean,
                    "controller": account,
                },
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
                settings.KAUFLAND_API_UPDATE_ENDPOINT,
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
            if isinstance(payload, list):
                body = payload
            elif isinstance(payload, dict):
                body = [dict(payload)]
                body[0].setdefault("productReference", ean)
                body[0].setdefault("sku", ean)
                body[0].setdefault("ean", ean)
            else:
                raise ValueError(
                    "OTTO payload must be an object or a list of variations."
                )

            if not body:
                raise ValueError(
                    "OTTO payload must contain at least one variation."
                )

            return client.request(
                settings.OTTO_API_BASE_URL,
                "POST",
                settings.OTTO_API_UPSERT_ENDPOINT,
                params={"controller": account},
                payload=body,
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
        awaiting_marketplace_confirmation = False
        otto_process_id = ""

        if publication is not None:
            otto_async_request_accepted = (
                marketplace == "otto"
                and is_otto_process_pending(result)
            )

            if otto_async_request_accepted:
                otto_process_id = extract_otto_process_id(response_payload)
                if not otto_process_id:
                    result = {
                        "ok": False,
                        "status_code": result["status_code"],
                        "details": {
                            "code": "otto_process_id_missing",
                            "response": response_payload,
                        },
                    }
                    response_payload = normalize_payload(result["details"])
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
                    publication = mark_publication_awaiting_confirmation(
                        publication=publication,
                        job=job,
                        response_payload=response_payload,
                        external_reference=otto_process_id,
                    )
                    first_successful_publication = False
                    awaiting_marketplace_confirmation = True
                    check_otto_publication_process.apply_async(
                        args=(publication.pk, 1),
                        eta=_next_otto_poll_time(response_payload),
                    )

            elif result["ok"]:
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
                "awaiting_marketplace_confirmation": (
                    awaiting_marketplace_confirmation
                ),
                **result,
            }
        )

    job.results = results
    _refresh_job_status(job)


@shared_task(bind=True)
def check_otto_publication_process(
    self,
    publication_id: int,
    attempt: int = 1,
) -> None:
    """Poll the OTTO wrapper until one update task reaches a final state."""
    publication = MarketplacePublication.objects.select_related(
        "product",
        "last_job",
    ).get(pk=publication_id)

    if publication.marketplace != MarketplacePublication.Marketplace.OTTO:
        return
    if publication.status not in {
        MarketplacePublication.Status.PUBLISHING,
        MarketplacePublication.Status.DEACTIVATING,
    }:
        return
    if not publication.external_reference:
        return

    job = publication.last_job
    if job is None:
        return

    client = MarketplaceClient(str(job.request_id))
    final_result = get_otto_process_result(
        client,
        process_id=publication.external_reference,
        account=publication.account,
    )

    if final_result is None:
        if attempt >= settings.OTTO_PROCESS_MAX_POLL_ATTEMPTS:
            error_payload = {
                "code": "otto_process_poll_timeout",
                "process_id": publication.external_reference,
                "attempts": attempt,
            }
            publication = mark_publication_failed(
                publication=publication,
                job=job,
                error_payload=error_payload,
                response_payload=error_payload,
            )
            _set_job_target_result(
                job=job,
                marketplace="otto",
                account=publication.account,
                replacement={
                    "marketplace": "otto",
                    "account": publication.account,
                    "ean": publication.ean,
                    "publication_id": publication.pk,
                    "ok": False,
                    "status_code": 504,
                    "details": error_payload,
                    "awaiting_marketplace_confirmation": False,
                    "ean_consumed": False,
                },
            )
            _refresh_job_status(job)
            return

        check_otto_publication_process.apply_async(
            args=(publication.pk, attempt + 1),
            countdown=settings.OTTO_PROCESS_POLL_INTERVAL_SECONDS,
        )
        return

    outcome = final_result["outcome"]
    result = final_result["result"]
    response_payload = normalize_payload(result["details"])

    if outcome == "failed":
        publication = mark_publication_failed(
            publication=publication,
            job=job,
            error_payload={
                "status_code": result["status_code"],
                "details": response_payload,
            },
            response_payload=response_payload,
        )
        target_result = {
            "marketplace": "otto",
            "account": publication.account,
            "ean": publication.ean,
            "publication_id": publication.pk,
            "ok": False,
            "status_code": result["status_code"],
            "details": response_payload,
            "process_id": publication.external_reference,
            "awaiting_marketplace_confirmation": False,
            "ean_consumed": False,
        }
    else:
        # Wrapper API successfully validated the request, but this still does
        # not mean that the item is already visible on OTTO marketplace.
        # Keep publication in `publishing` and start marketplace-status polling.
        publication.last_response = {
            "initial_process_response": publication.last_response,
            "process_result": response_payload,
        }
        publication.last_error = {}
        publication.save(
            update_fields=(
                "last_response",
                "last_error",
                "updated_at",
            )
        )

        target_result = {
            "marketplace": "otto",
            "account": publication.account,
            "ean": publication.ean,
            "publication_id": publication.pk,
            "ok": True,
            "status_code": result["status_code"],
            "details": response_payload,
            "process_id": publication.external_reference,
            "process_outcome": outcome,
            "awaiting_marketplace_confirmation": True,
            "ean_consumed": False,
        }

        _set_job_target_result(
            job=job,
            marketplace="otto",
            account=publication.account,
            replacement=target_result,
        )
        _refresh_job_status(job)

        check_otto_marketplace_status.apply_async(
            args=(publication.pk, 1),
            countdown=settings.OTTO_MARKETPLACE_STATUS_POLL_INTERVAL_SECONDS,
        )
        return

    _set_job_target_result(
        job=job,
        marketplace="otto",
        account=publication.account,
        replacement=target_result,
    )
    _refresh_job_status(job)


@shared_task(bind=True)
def check_otto_marketplace_status(
    self,
    publication_id: int,
    attempt: int = 1,
) -> None:
    """
    Confirms that a variation is actually visible on OTTO marketplace.

    The expected final OTTO marketplace status depends on the requested
    operation: ONLINE for publish/update/activate and a configured final
    value for deactivation.
    """

    publication = MarketplacePublication.objects.select_related(
        "product",
        "last_job",
    ).get(pk=publication_id)

    if publication.marketplace != MarketplacePublication.Marketplace.OTTO:
        return

    if publication.status not in {
        MarketplacePublication.Status.PUBLISHING,
        MarketplacePublication.Status.DEACTIVATING,
    }:
        return

    job = publication.last_job

    if job is None:
        return

    expected_statuses = get_expected_otto_marketplace_statuses(
        job.operation
    )

    client = MarketplaceClient(str(job.request_id))

    result = get_otto_marketplace_status(
        client,
        sku=publication.ean,
        account=publication.account,
    )
    response_payload = normalize_payload(result.get("details", {}))
    marketplace_item = get_otto_marketplace_item(
        response_payload,
        sku=publication.ean,
    )

    marketplace_status = ""
    if marketplace_item is not None:
        marketplace_status = str(
            marketplace_item.get("status", "")
        ).upper()

    if result.get("ok") and marketplace_status in expected_statuses:
        combined_response = {
            "process": publication.last_response,
            "marketplace_status": response_payload,
        }

        with transaction.atomic():
            publication, first_successful_publication = (
                mark_publication_succeeded(
                    publication=publication,
                    job=job,
                    response_payload=combined_response,
                    external_id=str(
                        marketplace_item.get("moin", "")
                    ),
                )
            )

            ean_consumed = False

            if (
                job.operation == MarketplaceJob.Operation.PUBLISH
                and first_successful_publication
            ):
                ean_consumed = consume_ean_code(
                    product=publication.product,
                    account=publication.account,
                )

        target_result = {
            "marketplace": "otto",
            "account": publication.account,
            "ean": publication.ean,
            "publication_id": publication.pk,
            "ok": True,
            "status_code": result["status_code"],
            "details": response_payload,
            "process_id": publication.external_reference,
            "marketplace_status": marketplace_status,
            "moin": marketplace_item.get("moin", ""),
            "shop_url": next(
                (
                    link.get("href")
                    for link in marketplace_item.get("links", [])
                    if isinstance(link, dict)
                    and link.get("rel") == "shop"
                ),
                "",
            ),
            "awaiting_marketplace_confirmation": False,
            "ean_consumed": ean_consumed,
        }

        _set_job_target_result(
            job=job,
            marketplace="otto",
            account=publication.account,
            replacement=target_result,
        )
        _refresh_job_status(job)
        return

    # Save the latest response so the manager can see the actual OTTO state
    # while a publication/deactivation is still being processed.
    publication.last_response = {
        "process": publication.last_response,
        "marketplace_status": response_payload,
    }
    publication.save(update_fields=("last_response", "updated_at"))

    if attempt >= settings.OTTO_MARKETPLACE_STATUS_MAX_POLL_ATTEMPTS:
        error_payload = {
            "code": "otto_marketplace_status_timeout",
            "detail": (
                "OTTO did not confirm the expected marketplace status "
                "within the configured waiting period."
            ),
            "attempts": attempt,
            "expected_statuses": list(expected_statuses),
            "last_marketplace_status": response_payload,
        }

        publication = mark_publication_failed(
            publication=publication,
            job=job,
            error_payload=error_payload,
            response_payload=error_payload,
        )

        _set_job_target_result(
            job=job,
            marketplace="otto",
            account=publication.account,
            replacement={
                "marketplace": "otto",
                "account": publication.account,
                "ean": publication.ean,
                "publication_id": publication.pk,
                "ok": False,
                "status_code": 504,
                "details": error_payload,
                "process_id": publication.external_reference,
                "awaiting_marketplace_confirmation": False,
                "ean_consumed": False,
            },
        )
        _refresh_job_status(job)
        return

    check_otto_marketplace_status.apply_async(
        args=(publication.pk, attempt + 1),
        countdown=settings.OTTO_MARKETPLACE_STATUS_POLL_INTERVAL_SECONDS,
    )


@shared_task(
    bind=True,
    name="apps.orchestrator.tasks.generate_marketplace_content",
)
def generate_marketplace_content(
    self,
    generation_id: str,
) -> dict[str, Any]:
    """Generate one universal German draft, regardless of selected targets."""
    with transaction.atomic():
        generation = (
            MarketplaceContentGeneration.objects.select_for_update()
            .get(pk=generation_id)
        )

        if generation.status in {
            MarketplaceContentGeneration.Status.SUCCEEDED,
            MarketplaceContentGeneration.Status.PARTIAL,
            MarketplaceContentGeneration.Status.FAILED,
        }:
            return {
                "generation_id": str(generation.id),
                "status": generation.status,
                "skipped": True,
            }

        generation.status = MarketplaceContentGeneration.Status.RUNNING
        generation.started_at = timezone.now()
        generation.celery_task_id = self.request.id or ""
        generation.save(
            update_fields=(
                "status",
                "started_at",
                "celery_task_id",
            )
        )

    if not generation.input_snapshot:
        generation.status = MarketplaceContentGeneration.Status.FAILED
        generation.error = {
            "detail": (
                "The AI generation job has no product input snapshot."
            )
        }
        generation.finished_at = timezone.now()
        generation.save(
            update_fields=(
                "status",
                "error",
                "finished_at",
            )
        )
        return {
            "generation_id": str(generation.id),
            "status": generation.status,
        }

    if not generation.targets:
        generation.status = MarketplaceContentGeneration.Status.FAILED
        generation.error = {
            "code": "missing_targets",
            "detail": "Select at least one marketplace target.",
        }
        generation.finished_at = timezone.now()
        generation.save(
            update_fields=(
                "status",
                "error",
                "finished_at",
            )
        )
        return {
            "generation_id": str(generation.id),
            "status": generation.status,
        }

    try:
        service = OpenAITextService()
        request = build_universal_content_request(
            product_snapshot=generation.input_snapshot,
        )
        ai_result = service.generate_json(
            instructions=request.instructions,
            input_text=request.input_text,
            schema_name=request.schema_name,
            schema=request.schema,
        )
        content = validate_universal_content(ai_result.data)
    except (
        OpenAITextServiceError,
        GeneratedContentValidationError,
    ) as error:
        generation.status = MarketplaceContentGeneration.Status.FAILED
        generation.error = {
            "code": "generation_failed",
            "detail": str(error),
        }
        generation.finished_at = timezone.now()
        generation.save(
            update_fields=(
                "status",
                "error",
                "finished_at",
            )
        )
        return {
            "generation_id": str(generation.id),
            "status": generation.status,
        }

    generation.status = MarketplaceContentGeneration.Status.SUCCEEDED
    generation.result = {
        "universal": {
            "model": ai_result.model,
            "content": content,
        }
    }
    generation.error = {}
    generation.model = ai_result.model
    generation.finished_at = timezone.now()
    generation.save(
        update_fields=(
            "status",
            "result",
            "error",
            "model",
            "finished_at",
        )
    )

    return {
        "generation_id": str(generation.id),
        "status": generation.status,
        "generated_targets": generation.targets,
        "model": ai_result.model,
    }
