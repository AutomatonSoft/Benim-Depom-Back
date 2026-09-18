from unittest.mock import patch

import pytest

from apps.orchestrator.models import MarketplaceJob, MarketplacePublication
from apps.products.models import Product


@pytest.mark.integration
@pytest.mark.django_db
def test_manager_deactivates_all_active_listings_without_changing_product_status(
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
    for marketplace in ("otto", "hood", "kaufland"):
        MarketplacePublication.objects.create(
            product=product,
            marketplace=marketplace,
            account="jv",
            ean=product.ean_jv,
            status=MarketplacePublication.Status.ACTIVE,
        )
    api_client.force_authenticate(manager)

    with (
        patch("apps.orchestrator.views.execute_marketplace_job.delay") as delay,
        django_capture_on_commit_callbacks(execute=True),
    ):
        response = api_client.post(
            f"/api/v1/orchestrator/products/{product.id}/listing-state/",
            {"action": "deactivate"},
            format="json",
        )

    assert response.status_code == 202
    jobs = MarketplaceJob.objects.filter(product=product).order_by("operation")
    assert {job.operation for job in jobs} == {
        MarketplaceJob.Operation.DEACTIVATE,
        MarketplaceJob.Operation.DELETE,
    }
    assert delay.call_count == 2
    deactivate_job = next(
        job for job in jobs if job.operation == MarketplaceJob.Operation.DEACTIVATE
    )
    marketplaces = {
        target["marketplace"] for target in deactivate_job.request_payload["targets"]
    }
    assert marketplaces == {"otto", "kaufland"}
    product.refresh_from_db()
    assert product.status == Product.Status.APPROVED
    assert MarketplacePublication.objects.get(
        product=product, marketplace="otto"
    ).status == MarketplacePublication.Status.DEACTIVATING
    assert MarketplacePublication.objects.get(
        product=product, marketplace="kaufland"
    ).status == MarketplacePublication.Status.DEACTIVATING
    assert MarketplacePublication.objects.get(
        product=product, marketplace="hood"
    ).status == MarketplacePublication.Status.DELETING


@pytest.mark.integration
@pytest.mark.django_db
def test_manager_can_choose_one_listing_target(
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
    MarketplacePublication.objects.create(
        product=product,
        marketplace="hood",
        account="jv",
        ean=product.ean_jv,
        status=MarketplacePublication.Status.ACTIVE,
    )
    api_client.force_authenticate(manager)

    with (
        patch("apps.orchestrator.views.execute_marketplace_job.delay"),
        django_capture_on_commit_callbacks(execute=True),
    ):
        response = api_client.post(
            f"/api/v1/orchestrator/products/{product.id}/listing-state/",
            {
                "action": "deactivate",
                "targets": [{"marketplace": "otto", "account": "jv"}],
            },
            format="json",
        )

    assert response.status_code == 202
    job = MarketplaceJob.objects.get(product=product)
    assert job.operation == MarketplaceJob.Operation.DEACTIVATE
    assert job.request_payload["targets"] == [{"marketplace": "otto", "account": "jv"}]
    otto = MarketplacePublication.objects.get(product=product, marketplace="otto")
    hood = MarketplacePublication.objects.get(product=product, marketplace="hood")
    assert otto.status == MarketplacePublication.Status.DEACTIVATING
    assert hood.status == MarketplacePublication.Status.ACTIVE
