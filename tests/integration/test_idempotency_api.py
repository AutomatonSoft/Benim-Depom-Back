from unittest.mock import patch

import pytest

from apps.orchestrator.models import (
    MarketplaceContentGeneration,
    MarketplaceJob,
    MarketplacePublication,
)
from apps.products.models import Product


def product_payload():
    return {
        "title": "Idempotent chair",
        "product_type": "Dining chair",
        "unit_price": "299.00",
        "currency": "EUR",
        "otto_category_id": 26822,
        "otto_category_group_id": 3593,
        "variants": [
            {
                "color_hex": "#5B91C8",
                "materials": ["Wood", "Fabric"],
                "width_cm": "50.00",
                "height_cm": "90.00",
                "length_cm": "55.00",
                "quantity": 3,
            }
        ],
    }


@pytest.mark.integration
@pytest.mark.django_db
def test_product_create_is_idempotent(api_client, seller):
    api_client.force_authenticate(seller)
    headers = {"HTTP_IDEMPOTENCY_KEY": "product-create-key-1"}

    first = api_client.post(
        "/api/v1/products/",
        product_payload(),
        format="json",
        **headers,
    )
    second = api_client.post(
        "/api/v1/products/",
        product_payload(),
        format="json",
        **headers,
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.data["id"] == second.data["id"]
    assert Product.objects.filter(owner=seller).count() == 1


@pytest.mark.integration
@pytest.mark.django_db
def test_idempotency_key_cannot_be_reused_for_another_payload(
    api_client,
    seller,
):
    api_client.force_authenticate(seller)
    headers = {"HTTP_IDEMPOTENCY_KEY": "product-create-key-2"}

    first = api_client.post(
        "/api/v1/products/",
        product_payload(),
        format="json",
        **headers,
    )

    changed_payload = product_payload()
    changed_payload["title"] = "Different chair"

    second = api_client.post(
        "/api/v1/products/",
        changed_payload,
        format="json",
        **headers,
    )

    assert first.status_code == 201
    assert second.status_code == 422
    assert Product.objects.filter(owner=seller).count() == 1


@pytest.mark.integration
@pytest.mark.django_db
def test_submit_is_idempotent(
    api_client,
    seller,
    product_factory,
    product_image_factory,
):
    product = product_factory(owner=seller)
    product_image_factory(product=product)
    api_client.force_authenticate(seller)

    headers = {"HTTP_IDEMPOTENCY_KEY": "product-submit-key-1"}
    url = f"/api/v1/products/{product.id}/submit/"

    first = api_client.post(url, **headers)
    second = api_client.post(url, **headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.data["id"] == second.data["id"]
    assert second.data["status"] == Product.Status.SUBMITTED


@pytest.mark.integration
@pytest.mark.django_db
def test_marketplace_job_is_idempotent(
    api_client,
    manager,
    product_factory,
    django_capture_on_commit_callbacks,
):
    product = product_factory(owner=manager, status=Product.Status.APPROVED)
    api_client.force_authenticate(manager)

    headers = {"HTTP_IDEMPOTENCY_KEY": "marketplace-job-key-1"}
    url = f"/api/v1/orchestrator/products/{product.id}/search/"
    payload = {
        "targets": [{"marketplace": "hood", "account": "jv"}],
    }

    with (
        patch("apps.orchestrator.views.execute_marketplace_job.delay") as delay,
        django_capture_on_commit_callbacks(execute=True),
    ):
        first = api_client.post(url, payload, format="json", **headers)
        second = api_client.post(url, payload, format="json", **headers)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.data["id"] == second.data["id"]
    assert MarketplaceJob.objects.filter(product=product).count() == 1
    delay.assert_called_once()


@pytest.mark.integration
@pytest.mark.django_db
def test_listing_state_is_idempotent(
    api_client,
    manager,
    product_factory,
    django_capture_on_commit_callbacks,
):
    product = product_factory(
        owner=manager,
        status=Product.Status.APPROVED,
        ean_jv="4012345678901",
    )
    MarketplacePublication.objects.create(
        product=product,
        marketplace="otto",
        account="jv",
        ean=product.ean_jv,
        status=MarketplacePublication.Status.ACTIVE,
    )
    api_client.force_authenticate(manager)

    headers = {"HTTP_IDEMPOTENCY_KEY": "listing-state-key-1"}
    url = f"/api/v1/orchestrator/products/{product.id}/listing-state/"
    payload = {
        "action": "deactivate",
        "targets": [{"marketplace": "otto", "account": "jv"}],
    }

    with (
        patch("apps.orchestrator.views.execute_marketplace_job.delay") as delay,
        django_capture_on_commit_callbacks(execute=True),
    ):
        first = api_client.post(url, payload, format="json", **headers)
        second = api_client.post(url, payload, format="json", **headers)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.data["jobs"][0]["id"] == second.data["jobs"][0]["id"]
    assert MarketplaceJob.objects.filter(product=product).count() == 1
    delay.assert_called_once()


@pytest.mark.integration
@pytest.mark.django_db
def test_ai_generation_is_idempotent(
    api_client,
    manager,
    seller,
    product_factory,
    django_capture_on_commit_callbacks,
):
    product = product_factory(
        owner=seller,
        status=Product.Status.SUBMITTED,
    )
    api_client.force_authenticate(manager)

    headers = {"HTTP_IDEMPOTENCY_KEY": "ai-generation-key-1"}
    url = (
        f"/api/v1/orchestrator/products/{product.id}/"
        "ai-content/generate/"
    )
    payload = {
        "targets": [{"marketplace": "otto", "account": "jv"}],
    }

    with (
        patch(
            "apps.orchestrator.views.generate_marketplace_content.delay"
        ) as delay,
        django_capture_on_commit_callbacks(execute=True),
    ):
        first = api_client.post(url, payload, format="json", **headers)
        second = api_client.post(url, payload, format="json", **headers)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.data["id"] == second.data["id"]
    assert MarketplaceContentGeneration.objects.filter(
        product=product
    ).count() == 1
    delay.assert_called_once()


    