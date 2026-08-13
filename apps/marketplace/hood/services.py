from __future__ import annotations

from django.conf import settings

from apps.orchestrator.models import MarketplaceJob

from .client import HoodClient, path_with_ean
from .models import HoodProductSnapshot


def execute(*, product, operation: str, ean: str, account: str, payload: dict, request_id: str) -> dict:
    if account not in HoodProductSnapshot.Account.values:
        return {"ok": False, "status_code": 400, "details": {"code": "hood_account_invalid"}}
    client = HoodClient(request_id)
    if operation == MarketplaceJob.Operation.SEARCH:
        result = client.request(
            "GET",
            path_with_ean(settings.HOOD_API_PATCH_ENDPOINT, ean),
            account=account,
        )
        if result["ok"]:
            HoodProductSnapshot.objects.update_or_create(
                product=product,
                account=account,
                defaults={"ean": ean, "payload": result["details"]},
            )
        return result
    endpoint = settings.HOOD_API_CREATE_ENDPOINT if operation == MarketplaceJob.Operation.PUBLISH else settings.HOOD_API_PATCH_ENDPOINT
    method = "POST" if operation == MarketplaceJob.Operation.PUBLISH else "PATCH"
    return client.request(method, path_with_ean(endpoint, ean), account=account, payload=payload)
