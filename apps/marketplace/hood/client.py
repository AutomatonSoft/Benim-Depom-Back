from __future__ import annotations

from typing import Any

import requests
from django.conf import settings


def path_with_ean(endpoint: str, ean: str) -> str:
    endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"
    return endpoint.format(ean=ean) if "{ean}" in endpoint else f"{endpoint.rstrip('/')}/{ean}"


class HoodClient:
    def __init__(self, request_id: str) -> None:
        self.request_id = request_id

    def request(
        self,
        method: str,
        path: str,
        *,
        account: str,
        payload: dict | None = None,
    ) -> dict[str, Any]:
        if not settings.HOOD_API_BASE_URL:
            return {"ok": False, "status_code": 503, "details": {"code": "hood_api_not_configured"}}
        headers = {"Accept": "application/json", "X-Request-Id": self.request_id}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        auth = (settings.HOOD_LOGIN, settings.HOOD_PASSWORD) if settings.HOOD_LOGIN and settings.HOOD_PASSWORD else None
        try:
            response = requests.request(
                method,
                f"{settings.HOOD_API_BASE_URL}{path}",
                headers=headers,
                params={"account": account},
                json=payload,
                auth=auth,
                timeout=settings.MARKETPLACE_HTTP_TIMEOUT_SECONDS,
            )
            try:
                body: Any = response.json()
            except ValueError:
                body = {"raw": response.text[:1500]}
            return {"ok": response.ok, "status_code": response.status_code, "details": body}
        except requests.RequestException as exc:
            return {"ok": False, "status_code": 502, "details": {"code": "hood_api_unreachable", "reason": str(exc)}}
