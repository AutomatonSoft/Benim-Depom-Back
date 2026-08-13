from __future__ import annotations

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from apps.marketplace.hood.services import execute as execute_hood

from .client import MarketplaceClient
from .models import MarketplaceJob


def _request_for_channel(client, *, channel, operation, ean, account, payload):
    account = account or "jv"
    if channel == "hood":
        raise ValueError("Hood dispatch requires a product and is handled separately.")
    if channel == "kaufland":
        if operation == MarketplaceJob.Operation.SEARCH:
            return client.request(settings.KAUFLAND_API_BASE_URL, "GET", settings.KAUFLAND_API_GET_BY_EAN_ENDPOINT, params={"ean": ean, "controller": account})
        path = settings.KAUFLAND_API_CREATE_ENDPOINT if operation == MarketplaceJob.Operation.PUBLISH else settings.KAUFLAND_API_UPDATE_ENDPOINT
        method = "PUT" if operation == MarketplaceJob.Operation.PUBLISH else "PATCH"
        return client.request(settings.KAUFLAND_API_BASE_URL, method, path, payload={"ean": ean, "controller": account, **payload})
    if channel == "otto":
        if operation == MarketplaceJob.Operation.SEARCH:
            return client.request(settings.OTTO_API_BASE_URL, "GET", settings.OTTO_API_PRODUCTS_ENDPOINT, params={"sku": ean, "page": 1, "limit": 100, "controller": account})
        body = dict(payload)
        body.setdefault("productReference", ean)
        body.setdefault("sku", ean)
        body.setdefault("ean", ean)
        return client.request(settings.OTTO_API_BASE_URL, "POST", settings.OTTO_API_UPSERT_ENDPOINT, params={"controller": account}, payload=[body])
    raise ValueError(f"Unsupported marketplace channel: {channel}")


@shared_task(bind=True)
def execute_marketplace_job(self, job_id: str) -> None:
    job = MarketplaceJob.objects.select_related("product").get(pk=job_id)
    if job.status != MarketplaceJob.Status.QUEUED:
        return
    job.status = MarketplaceJob.Status.RUNNING
    job.started_at = timezone.now()
    job.celery_task_id = self.request.id or ""
    job.save(update_fields=("status", "started_at", "celery_task_id"))
    eans = [
        ("jv", job.product.ean_jv.strip()),
        ("xl", job.product.ean_xl.strip()),
    ]
    eans = [(account, ean) for account, ean in eans if ean]
    if not eans:
        job.status = MarketplaceJob.Status.FAILED
        job.error = {"code": "product_has_no_ean", "detail": "Set Product.ean_jv or Product.ean_xl before marketplace dispatch."}
        job.finished_at = timezone.now()
        job.save(update_fields=("status", "error", "finished_at"))
        return
    client = MarketplaceClient(str(job.request_id))
    accounts = job.request_payload.get("accounts", {})
    payloads = job.request_payload.get("payloads", {})
    results = []
    for channel in job.requested_channels:
        for ean_account, ean in eans:
            try:
                account = accounts.get(channel) or ean_account
                if channel == "hood":
                    result = execute_hood(
                        product=job.product,
                        operation=job.operation,
                        ean=ean,
                        account=account,
                        payload=payloads.get(channel, {}),
                        request_id=str(job.request_id),
                    )
                else:
                    result = _request_for_channel(client, channel=channel, operation=job.operation, ean=ean, account=account, payload=payloads.get(channel, {}))
            except (TypeError, ValueError) as exc:
                result = {"ok": False, "status_code": 502, "details": {"code": "marketplace_dispatch_failed", "reason": str(exc)}}
            results.append({"channel": channel, "ean": ean, **result})
    success = sum(result["ok"] for result in results)
    job.results = results
    job.status = MarketplaceJob.Status.SUCCEEDED if success == len(results) else MarketplaceJob.Status.FAILED if success == 0 else MarketplaceJob.Status.PARTIAL
    job.finished_at = timezone.now()
    job.save(update_fields=("results", "status", "finished_at"))
