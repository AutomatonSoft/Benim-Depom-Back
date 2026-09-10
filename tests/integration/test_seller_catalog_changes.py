from unittest.mock import patch

import pytest

from apps.ean.models import EanCode
from apps.moderation.models import ModerationDecision
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
    assert product.status == Product.Status.WITHDRAWN
    assert Notification.objects.filter(
        notification_type=Notification.Type.PRODUCT_WITHDRAWN_FROM_REVIEW,
        product=product,
        user=manager,
    ).exists()
    assert ModerationDecision.objects.filter(
        product=product,
        decision=ModerationDecision.Decision.WITHDRAWN,
    ).exists()
    history = api_client.get(f"/api/v1/products/{product.id}/moderation-history/")
    assert history.status_code == 200
    decisions = [item["decision"] for item in history.data["results"]]
    assert ModerationDecision.Decision.WITHDRAWN in decisions


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
    assert Notification.objects.filter(
        notification_type=Notification.Type.PRODUCT_APPROVED,
        product=product,
        user=seller,
        title="Product changes approved",
    ).exists()


@pytest.mark.integration
@pytest.mark.django_db
def test_approve_seller_changes_succeeds_when_broker_is_down(
    api_client,
    seller,
    manager,
    product_factory,
    django_capture_on_commit_callbacks,
):
    product = product_factory(owner=seller, status=Product.Status.APPROVED, title="Old")
    authenticate(api_client, seller)
    assert (
        api_client.patch(
            f"/api/v1/products/{product.id}/",
            {"title": "New title"},
            format="json",
        ).status_code
        == 200
    )
    product.refresh_from_db()
    authenticate(api_client, manager)
    with (
        patch(
            "apps.moderation.views.execute_marketplace_job.delay",
            side_effect=ConnectionError("broker"),
        ),
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
    assert Notification.objects.filter(
        notification_type=Notification.Type.PRODUCT_APPROVED,
        product=product,
        user=seller,
    ).exists()


@pytest.mark.integration
@pytest.mark.django_db
def test_reject_seller_changes_discards_pending_and_notifies(
    api_client,
    seller,
    manager,
    product_factory,
    django_capture_on_commit_callbacks,
):
    product = product_factory(owner=seller, status=Product.Status.APPROVED, title="Old")
    authenticate(api_client, seller)
    assert (
        api_client.patch(
            f"/api/v1/products/{product.id}/",
            {"title": "New title"},
            format="json",
        ).status_code
        == 200
    )
    product.refresh_from_db()
    authenticate(api_client, manager)
    with django_capture_on_commit_callbacks(execute=True):
        rejected = api_client.post(
            f"/api/v1/manager/products/{product.id}/seller-changes/reject/",
            {
                "expected_catalog_revision": product.catalog_revision,
                "comment": "Keep the old title.",
            },
            format="json",
        )
    assert rejected.status_code == 200
    product.refresh_from_db()
    assert product.title == "Old"
    assert product.status == Product.Status.APPROVED
    assert product.pending_changes == {}
    notification = Notification.objects.get(
        notification_type=Notification.Type.PRODUCT_REJECTED,
        product=product,
        user=seller,
    )
    assert notification.title == "Product changes rejected"
    assert notification.body == "Keep the old title."


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
def test_submitted_product_can_be_patched_without_withdraw(
    api_client, seller, manager, product_factory, product_image_factory
):
    product = product_factory(
        owner=seller, status=Product.Status.SUBMITTED, title="Old"
    )
    product_image_factory(product=product)
    revision = product.catalog_revision
    authenticate(api_client, seller)
    response = api_client.patch(
        f"/api/v1/products/{product.id}/",
        {"title": "New"},
        format="json",
    )
    assert response.status_code == 200
    product.refresh_from_db()
    assert product.status == Product.Status.SUBMITTED
    assert product.title == "New"
    assert product.catalog_revision == revision + 1

    authenticate(api_client, manager)
    stale = api_client.post(
        f"/api/v1/manager/products/{product.id}/approve/",
        {"expected_catalog_revision": revision},
        format="json",
    )
    assert stale.status_code == 409
    product.refresh_from_db()
    assert product.status == Product.Status.SUBMITTED

    EanCode.objects.create(
        code="4006381333931", account=EanCode.Account.JV, imported_by=manager
    )
    EanCode.objects.create(
        code="9501101530003", account=EanCode.Account.XL, imported_by=manager
    )
    approved = api_client.post(
        f"/api/v1/manager/products/{product.id}/approve/",
        {"expected_catalog_revision": product.catalog_revision},
        format="json",
    )
    assert approved.status_code == 200
    product.refresh_from_db()
    assert product.status == Product.Status.APPROVED
    assert product.title == "New"
