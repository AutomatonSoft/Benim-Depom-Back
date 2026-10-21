from unittest.mock import patch

import pytest

from apps.products.models import Product
from tests.integration.test_products_api import (
    multipart_product_payload,
    product_payload,
)

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


@pytest.mark.parametrize("request_format", ["json", "multipart"])
def test_create_enqueues_after_commit_once_even_on_replay(
    api_client,
    seller,
    product_type,
    image_file,
    django_capture_on_commit_callbacks,
    request_format,
):
    api_client.force_authenticate(seller)

    def payload():
        if request_format == "multipart":
            return multipart_product_payload(product_type, image_file)
        return product_payload(product_type)

    with patch("apps.products.views.enqueue_product_created_email") as enqueue:
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            first = api_client.post(
                "/api/v1/products/",
                payload(),
                format=request_format,
                HTTP_IDEMPOTENCY_KEY="product-email-create",
            )
            assert first.status_code == 201
            enqueue.assert_not_called()
        assert callbacks
        enqueue.assert_called_once_with(
            seller_name=seller.username,
            seller_email=seller.email,
            title=first.data["title"],
        )
        with django_capture_on_commit_callbacks(execute=True):
            replay = api_client.post(
                "/api/v1/products/",
                payload(),
                format=request_format,
                HTTP_IDEMPOTENCY_KEY="product-email-create",
            )
        assert replay.status_code == 201
        assert replay.data["id"] == first.data["id"]
        assert enqueue.call_count == 1


def test_broker_failure_does_not_fail_create(
    api_client,
    seller,
    product_type,
    settings,
    django_capture_on_commit_callbacks,
):
    settings.PRODUCT_CREATE_EMAIL = "info@jvmoebel.de"
    api_client.force_authenticate(seller)
    with (
        patch(
            "apps.products.tasks.send_product_created_email.app.connection_for_write",
            side_effect=OSError("Broker unavailable"),
        ),
        django_capture_on_commit_callbacks(execute=True),
    ):
        response = api_client.post(
            "/api/v1/products/",
            product_payload(product_type),
            format="json",
        )
    assert response.status_code == 201
    assert Product.objects.filter(pk=response.data["id"]).exists()


def test_invalid_create_does_not_enqueue(
    api_client,
    seller,
    django_capture_on_commit_callbacks,
):
    api_client.force_authenticate(seller)
    with (
        patch("apps.products.views.enqueue_product_created_email") as enqueue,
        django_capture_on_commit_callbacks(execute=True),
    ):
        response = api_client.post("/api/v1/products/", {}, format="json")
    assert response.status_code == 400
    enqueue.assert_not_called()
