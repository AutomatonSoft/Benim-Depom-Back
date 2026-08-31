from unittest.mock import Mock, patch

from django.test import override_settings

from apps.common.marketplace_http import (
    SAFE_RETRY_METHODS,
    build_marketplace_session,
    marketplace_timeout,
)
from apps.orchestrator.client import MarketplaceClient


def test_marketplace_session_retries_only_safe_methods():
    session = build_marketplace_session()
    retry = session.get_adapter("https://").max_retries

    assert retry.total == 3
    assert retry.connect == 3
    assert retry.read == 3
    assert retry.status == 3
    assert retry.status_forcelist == (429, 500, 502, 503, 504)

    assert retry.allowed_methods == SAFE_RETRY_METHODS
    assert "GET" in retry.allowed_methods
    assert "POST" not in retry.allowed_methods
    assert "PUT" not in retry.allowed_methods
    assert "PATCH" not in retry.allowed_methods
    assert "DELETE" not in retry.allowed_methods


@override_settings(
    MARKETPLACE_HTTP_CONNECT_TIMEOUT_SECONDS=4,
    MARKETPLACE_HTTP_READ_TIMEOUT_SECONDS=19,
)
def test_marketplace_timeout_is_connect_and_read_pair():
    assert marketplace_timeout() == (4, 19)


@override_settings(
    MARKETPLACE_HTTP_CONNECT_TIMEOUT_SECONDS=4,
    MARKETPLACE_HTTP_READ_TIMEOUT_SECONDS=19,
)
@patch("apps.orchestrator.client.build_marketplace_session")
def test_marketplace_client_passes_timeout_and_payload_to_session(mock_build_session):
    response = Mock()
    response.ok = True
    response.status_code = 200
    response.json.return_value = {"success": True}

    session = Mock()
    session.request.return_value = response
    mock_build_session.return_value = session

    client = MarketplaceClient(request_id="request-123")

    result = client.request(
        base_url="https://example.test",
        method="POST",
        path="/products",
        params={"account": "jv"},
        payload={"ean": "1234567890123"},
    )

    assert result == {
        "ok": True,
        "status_code": 200,
        "details": {"success": True},
    }

    session.request.assert_called_once_with(
        method="POST",
        url="https://example.test/products",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Request-Id": "request-123",
        },
        params={"account": "jv"},
        json={"ean": "1234567890123"},
        auth=None,
        timeout=(4, 19),
    )
