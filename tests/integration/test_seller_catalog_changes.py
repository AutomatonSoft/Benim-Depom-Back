from unittest.mock import patch

import pytest

from apps.notifications.models import Notification
from apps.orchestrator.models import MarketplaceJob
from apps.products.models import Product


def authenticate(client, user):
    client.force_authenticate(user=user)
    return client


@pytest.mark.integration
@pytest.mark.django_db
def test_withdraw_then_stale_manager_approve_returns_conflict(
    api_client, seller, manager, product_factory, product_image_factory
):
    product = product_factory(owner=seller, status=Product.Status.SUBMITTED)
    product_image_factory(product=product)
    revision = product.catalog_revision

    authenticate(api_client, seller)
    assert (
        api_client.post(f"/api/v1/products/{product.id}/withdraw/").status_code == 204
    )

    authenticate(api_client, manager)
    response = api_client.post(
        f"/api/v1/manager/products/{product.id}/approve/",
        {"expected_catalog_revision": revision},
        format="json",
    )
    assert response.status_code == 409
    product.refresh_from_db()
    assert product.status == Product.Status.REJECTED
    assert Notification.objects.filter(
        notification_type=Notification.Type.PRODUCT_WITHDRAWN_FROM_REVIEW,
        product=product,
        user=manager,
    ).exists()


@pytest.mark.integration
@pytest.mark.django_db
def test_seller_approved_patch_is_pending_until_manager_approves(
    api_client,
    seller,
    manager,
    product_factory,
    django_capture_on_commit_callbacks,
):
    product = product_factory(owner=seller, status=Product.Status.APPROVED, title="Old")
    authenticate(api_client, seller)
    response = api_client.patch(
        f"/api/v1/products/{product.id}/",
        {"title": "New title"},
        format="json",
    )
    assert response.status_code == 200
    product.refresh_from_db()
    assert product.title == "Old"
    assert product.pending_changes["title"] == "New title"
    assert Notification.objects.filter(
        notification_type=Notification.Type.PRODUCT_CHANGE_REQUESTED,
        product=product,
        user=manager,
    ).exists()

    authenticate(api_client, manager)
    with (
        patch("apps.moderation.views.execute_marketplace_job.delay"),
        django_capture_on_commit_callbacks(execute=True),
    ):
        approved = api_client.post(
            f"/api/v1/manager/products/{product.id}/seller-changes/approve/",
            {"expected_catalog_revision": product.catalog_revision},
            format="json",
        )
    assert approved.status_code == 200
    product.refresh_from_db()
    assert product.title == "New title"
    assert product.pending_changes == {}
    assert approved.data["marketplace_job"] is None


@pytest.mark.integration
@pytest.mark.django_db
def test_marketplace_job_list_filters_in_progress(api_client, manager, product_factory):
    product = product_factory(owner=manager, status=Product.Status.APPROVED)
    queued = MarketplaceJob.objects.create(
        product=product,
        requested_by=manager,
        operation=MarketplaceJob.Operation.UPDATE,
        status=MarketplaceJob.Status.QUEUED,
        requested_channels=["otto"],
    )
    MarketplaceJob.objects.create(
        product=product,
        requested_by=manager,
        operation=MarketplaceJob.Operation.UPDATE,
        status=MarketplaceJob.Status.SUCCEEDED,
        requested_channels=["otto"],
    )
    authenticate(api_client, manager)
    response = api_client.get("/api/v1/orchestrator/jobs/?in_progress=true")
    assert response.status_code == 200
    ids = [item["id"] for item in response.data["results"]]
    assert str(queued.id) in ids
    assert all(item["in_progress"] for item in response.data["results"])


@pytest.mark.integration
@pytest.mark.django_db
def test_submitted_product_cannot_be_patched_until_withdrawn(
    api_client, seller, product_factory
):
    product = product_factory(owner=seller, status=Product.Status.SUBMITTED, title="Old")
    authenticate(api_client, seller)
    assert (
        api_client.patch(
            f"/api/v1/products/{product.id}/",
            {"title": "New"},
            format="json",
        ).status_code
        == 403
    )
    assert api_client.post(f"/api/v1/products/{product.id}/withdraw/").status_code == 204
    response = api_client.patch(
        f"/api/v1/products/{product.id}/",
        {"title": "New"},
        format="json",
    )
    assert response.status_code == 200
    product.refresh_from_db()
    assert product.status == Product.Status.REJECTED
    assert product.title == "New"
