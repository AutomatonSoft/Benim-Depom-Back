from __future__ import annotations

from django.conf import settings

from apps.orchestrator.models import MarketplaceJob

from .client import HoodClient, path_with_ean
from .models import HoodProductSnapshot


def execute(
    *,
    product,
    operation: str,
    ean: str,
    account: str,
    payload: dict,
    request_id: str,
) -> dict:
    if account not in HoodProductSnapshot.Account.values:
        return {
            "ok": False,
            "status_code": 400,
            "details": {
                "code": "hood_account_invalid",
                "detail": "Account must be jv or xl.",
            },
        }

    client = HoodClient(request_id)

    if operation == MarketplaceJob.Operation.SEARCH:
        result = client.request(
            "GET",
            path_with_ean(settings.HOOD_API_GET_ENDPOINT, ean),
            account=account,
        )

        if result["ok"]:
            HoodProductSnapshot.objects.update_or_create(
                product=product,
                account=account,
                defaults={
                    "ean": ean,
                    "payload": result["details"],
                },
            )

        return result

    if operation == MarketplaceJob.Operation.PUBLISH:
        return client.request(
            "POST",
            path_with_ean(settings.HOOD_API_CREATE_ENDPOINT, ean),
            account=account,
            payload=payload,
            query_params={"check_exists": "true"},
        )

    if operation == MarketplaceJob.Operation.UPDATE:
        return client.request(
            "PATCH",
            path_with_ean(settings.HOOD_API_PATCH_ENDPOINT, ean),
            account=account,
            payload=payload,
        )

    if operation == MarketplaceJob.Operation.DELETE:
        return client.request(
            "DELETE",
            path_with_ean(settings.HOOD_API_DELETE_ENDPOINT, ean),
            account=account,
        )

    if operation == MarketplaceJob.Operation.DEACTIVATE:
        return {
            "ok": False,
            "status_code": 400,
            "details": {
                "code": "hood_deactivate_not_supported",
                "detail": "Use delete for Hood publications.",
            },
        }

    return {
        "ok": False,
        "status_code": 400,
        "details": {
            "code": "hood_operation_unsupported",
            "detail": f"Unsupported Hood operation: {operation}.",
        },
    }
