from unittest.mock import Mock

import pytest
import requests
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from apps.common.white_image_service import (
    WhiteImageServiceError,
    generate_white_background,
    get_generation_results,
)


@pytest.mark.unit
@override_settings(
    BULK_WHITE_IMAGE_SERVICE_URL="", BULK_WHITE_IMAGE_SERVICE_TOKEN="token"
)
def test_generation_requires_service_url(image_file):
    with pytest.raises(ImproperlyConfigured, match="URL"):
        generate_white_background(
            image_file=image_file(), title="Chair", product_type="Furniture"
        )


@pytest.mark.unit
@override_settings(
    BULK_WHITE_IMAGE_SERVICE_URL="https://ai.example/api/generate/",
    BULK_WHITE_IMAGE_SERVICE_TOKEN="token",
    BULK_WHITE_IMAGE_SERVICE_TIMEOUT_SECONDS=5,
)
def test_generation_posts_expected_data(monkeypatch, image_file):
    response = Mock(status_code=202, headers={"Content-Type": "application/json"})
    response.json.return_value = {"status": "queued", "product_id": 11}
    monkeypatch.setattr(
        "apps.common.white_image_service.requests.post", Mock(return_value=response)
    )

    result = generate_white_background(
        image_file=image_file(), title="Chair", product_type="Furniture"
    )

    assert result["http_status"] == 202
    assert result["payload"]["product_id"] == 11


@pytest.mark.unit
@override_settings(
    BULK_WHITE_IMAGE_SERVICE_URL="https://ai.example/api/generate/",
    BULK_WHITE_IMAGE_SERVICE_TOKEN="token",
)
def test_generation_wraps_http_failure(monkeypatch, image_file):
    monkeypatch.setattr(
        "apps.common.white_image_service.requests.post",
        Mock(side_effect=requests.RequestException("down")),
    )

    with pytest.raises(WhiteImageServiceError, match="request failed"):
        generate_white_background(
            image_file=image_file(), title="Chair", product_type="Furniture"
        )


@pytest.mark.unit
@override_settings(
    BULK_WHITE_IMAGE_SERVICE_URL="https://ai.example/api/generate/",
    BULK_WHITE_IMAGE_SERVICE_RESULTS_URL="https://ai.example/api/generation-results/{product_id}/",
    BULK_WHITE_IMAGE_SERVICE_TOKEN="token",
)
def test_generation_result_uses_only_trusted_https_host(monkeypatch):
    response = Mock()
    response.json.return_value = {"status": "completed"}
    get_mock = Mock(return_value=response)
    monkeypatch.setattr("apps.common.white_image_service.requests.get", get_mock)

    result = get_generation_results(
        product_id=11,
        result_url="https://ai.example/api/generation-results/11/",
    )

    assert result == {"status": "completed"}
    get_mock.assert_called_once()

    with pytest.raises(WhiteImageServiceError, match="not trusted"):
        get_generation_results(product_id=11, result_url="https://evil.example/result/")
