from __future__ import annotations

from typing import Any

import requests
from django.conf import settings
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

RETRYABLE_STATUS_CODES = (429, 500, 502, 503, 504)

# Только безопасные запросы можно автоматически повторять.
# POST/PUT/PATCH/DELETE намеренно не входят сюда:
# при таймауте нельзя точно знать, обработал ли маркетплейс запрос.
SAFE_RETRY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def build_marketplace_session() -> requests.Session:
    retry = Retry(
        total=settings.MARKETPLACE_HTTP_RETRY_TOTAL,
        connect=settings.MARKETPLACE_HTTP_RETRY_TOTAL,
        read=settings.MARKETPLACE_HTTP_RETRY_TOTAL,
        status=settings.MARKETPLACE_HTTP_RETRY_TOTAL,
        allowed_methods=SAFE_RETRY_METHODS,
        status_forcelist=RETRYABLE_STATUS_CODES,
        backoff_factor=settings.MARKETPLACE_HTTP_RETRY_BACKOFF_FACTOR,
        respect_retry_after_header=True,
        raise_on_status=False,
    )

    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=10,
        pool_maxsize=20,
        pool_block=True,
    )

    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def marketplace_timeout() -> tuple[int, int]:
    """
    (connect timeout, read timeout) для requests.
    """
    return (
        settings.MARKETPLACE_HTTP_CONNECT_TIMEOUT_SECONDS,
        settings.MARKETPLACE_HTTP_READ_TIMEOUT_SECONDS,
    )


def response_payload(response: requests.Response) -> dict[str, Any] | list[Any]:
    """
    Безопасно получает ответ внешнего API.
    Неструктурированный ответ ограничиваем, чтобы не раздувать БД.
    """
    try:
        return response.json()
    except ValueError:
        return {"raw": response.text[:1500]}
