from __future__ import annotations

from typing import Any

import requests

from apps.common.marketplace_http import (
    build_marketplace_session,
    marketplace_timeout,
    response_payload,
)


class MarketplaceClient:
    """HTTP-клиент для OTTO и Kaufland."""

    def __init__(self, request_id: str) -> None:
        self.request_id = request_id
        self.session = build_marketplace_session()

    def request(
        self,
        base_url: str,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        payload: dict | list | None = None,
        auth: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        if not base_url:
            return {
                "ok": False,
                "status_code": 503,
                "details": {"code": "marketplace_api_not_configured"},
            }

        headers = {
            "Accept": "application/json",
            "X-Request-Id": self.request_id,
        }

        if payload is not None:
            headers["Content-Type"] = "application/json"

        try:
            response = self.session.request(
                method=method,
                url=f"{base_url}{path}",
                headers=headers,
                params=params,
                json=payload,
                auth=auth,
                timeout=marketplace_timeout(),
            )

            return {
                "ok": response.ok,
                "status_code": response.status_code,
                "details": response_payload(response),
            }

        except requests.RequestException as exc:
            return {
                "ok": False,
                "status_code": 502,
                "details": {
                    "code": "marketplace_api_unreachable",
                    "reason": str(exc),
                },
            }