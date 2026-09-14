from __future__ import annotations

import re
from typing import Any, Literal

from apps.orchestrator.models import MarketplaceJob

KauflandStatusOutcome = Literal["success", "failure", "pending"]

KAUFLAND_STATUS_CONFIRM_OPERATIONS = frozenset(
    {
        MarketplaceJob.Operation.PUBLISH,
        MarketplaceJob.Operation.UPDATE,
        MarketplaceJob.Operation.DELETE,
    }
)


def extract_kaufland_status(payload: dict[str, Any]) -> str:
    return str(payload.get("status", "")).strip().upper()


def extract_kaufland_external_id(payload: dict[str, Any]) -> str:
    for key in ("id", "product_id", "productId"):
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()

    product_url = str(payload.get("product_url", "")).strip()
    if not product_url:
        return ""

    match = re.search(r"/product/(\d+)/?", product_url)
    if match:
        return match.group(1)

    return product_url


def classify_kaufland_status_outcome(
    *,
    operation: str,
    payload: dict[str, Any],
) -> KauflandStatusOutcome:
    """Map Kaufland status-check payload to a terminal or pending outcome.

    Upload/update can return HTTP success while catalog validation still
    fails later (``BLOCKED`` / ``is_valid: false``). Delete is confirmed
    only when the product is gone (``NOT_FOUND``).
    """

    status = extract_kaufland_status(payload)
    is_live = payload.get("is_live") is True

    if operation == MarketplaceJob.Operation.DELETE:
        if status == "NOT_FOUND":
            return "success"
        return "pending"

    if status == "BLOCKED" or payload.get("is_valid") is False:
        return "failure"

    if is_live or status in {"LIVE", "ONLINE"}:
        return "success"

    return "pending"


def kaufland_status_failure_payload(
    *,
    payload: dict[str, Any],
    attempts: int | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {
        "code": "kaufland_product_status_blocked",
        "detail": str(
            payload.get("summary")
            or "Kaufland rejected the product after catalog validation."
        ),
        "status": extract_kaufland_status(payload),
        "is_live": payload.get("is_live"),
        "is_valid": payload.get("is_valid"),
        "issues_detected": payload.get("issues_detected") or [],
        "product_status": payload,
    }
    if attempts is not None:
        error["attempts"] = attempts
    return error
