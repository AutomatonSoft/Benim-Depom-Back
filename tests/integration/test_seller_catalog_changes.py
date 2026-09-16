from unittest.mock import patch

import pytest

from apps.ean.models import EanCode
from apps.moderation.models import ModerationDecision
from apps.notifications.models import Notification
from apps.orchestrator.models import MarketplaceJob
from apps.products.models import PriceNegotiation, Product


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
        title="Изменения товара одобрены",
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
            {
                "title": "New title",
                "change_comment": "Please use the new title",
            },
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
    assert product.seller_change_review["title"] == "New title"
    assert product.seller_change_review["seller_comment"] == "Please use the new title"
    assert product.seller_change_review["review_kind"] == "rejected_pending"
    assert product.seller_change_review["manager_comment"] == "Keep the old title."
    assert product.seller_change_review["baseline"]["title"] == "Old"
    notification = Notification.objects.get(
        notification_type=Notification.Type.PRODUCT_REJECTED,
        product=product,
        user=seller,
    )
    assert notification.title == "Изменения товара отклонены"
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


@pytest.mark.integration
@pytest.mark.django_db
def test_seller_change_comment_is_stored_and_sent_to_managers(
    api_client,
    seller,
    manager,
    product_factory,
):
    product = product_factory(owner=seller, status=Product.Status.APPROVED, title="Old")
    authenticate(api_client, seller)
    response = api_client.patch(
        f"/api/v1/products/{product.id}/",
        {
            "title": "New title",
            "change_comment": "  Fixed the color naming  ",
        },
        format="json",
    )
    assert response.status_code == 200
    product.refresh_from_db()
    assert product.pending_changes["title"] == "New title"
    assert product.pending_changes["seller_comment"] == "Fixed the color naming"

    notification = Notification.objects.get(
        notification_type=Notification.Type.PRODUCT_CHANGE_REQUESTED,
        product=product,
        user=manager,
    )
    assert "Fixed the color naming" in notification.body

    inbox = authenticate(api_client, manager).get("/api/v1/notifications/")
    assert inbox.status_code == 200
    row = next(item for item in inbox.data["results"] if item["id"] == notification.id)
    assert row["seller_comment"] == "Fixed the color naming"


@pytest.mark.integration
@pytest.mark.django_db
def test_change_comment_alone_is_rejected_for_approved_product(
    api_client,
    seller,
    product_factory,
):
    product = product_factory(owner=seller, status=Product.Status.APPROVED, title="Old")
    authenticate(api_client, seller)
    response = api_client.patch(
        f"/api/v1/products/{product.id}/",
        {"change_comment": "Just a note"},
        format="json",
    )
    assert response.status_code == 400
    assert "detail" in response.data
    product.refresh_from_db()
    assert product.pending_changes == {}


@pytest.mark.integration
@pytest.mark.django_db
def test_unchanged_approved_fields_are_not_stored_in_pending(
    api_client,
    seller,
    product_factory,
):
    product = product_factory(
        owner=seller,
        status=Product.Status.APPROVED,
        title="Old",
        unit_price="200.00",
        currency=Product.Currency.TRY,
    )
    authenticate(api_client, seller)
    response = api_client.patch(
        f"/api/v1/products/{product.id}/",
        {
            "title": "New title",
            "unit_price": "200.00",
            "currency": "TRY",
            "product_type": product.product_type,
            "change_comment": "Only title changed",
        },
        format="json",
    )
    assert response.status_code == 200
    product.refresh_from_db()
    assert product.pending_changes["title"] == "New title"
    assert product.pending_changes["seller_comment"] == "Only title changed"
    assert "unit_price" not in product.pending_changes
    assert "currency" not in product.pending_changes
    assert "product_type" not in product.pending_changes


@pytest.mark.integration
@pytest.mark.django_db
def test_change_comment_rejected_on_submitted_product(
    api_client,
    seller,
    product_factory,
):
    product = product_factory(owner=seller, status=Product.Status.SUBMITTED)
    authenticate(api_client, seller)
    response = api_client.patch(
        f"/api/v1/products/{product.id}/",
        {"title": "Still in review", "change_comment": "Should not work"},
        format="json",
    )
    assert response.status_code == 400
    assert "change_comment" in response.data


@pytest.mark.integration
@pytest.mark.django_db
def test_rejected_resubmit_can_include_optional_comment(
    api_client,
    seller,
    manager,
    product_factory,
    product_image_factory,
    django_capture_on_commit_callbacks,
):
    product = product_factory(owner=seller, status=Product.Status.REJECTED)
    product_image_factory(product=product)
    authenticate(api_client, seller)
    with django_capture_on_commit_callbacks(execute=True):
        response = api_client.patch(
            f"/api/v1/products/{product.id}/",
            {
                "title": "Corrected chair",
                "resubmit_for_moderation": True,
                "change_comment": "Updated photos and title",
            },
            format="json",
        )
    assert response.status_code == 200
    assert response.data["status"] == Product.Status.SUBMITTED
    product.refresh_from_db()
    assert product.title == "Corrected chair"
    assert product.seller_change_review["review_kind"] == "resubmission"
    assert product.seller_change_review["seller_comment"] == "Updated photos and title"
    assert product.seller_change_review["title"] == "Corrected chair"
    assert "title" in product.seller_change_review["baseline"]
    assert (
        response.data["seller_change_review"]["seller_comment"]
        == "Updated photos and title"
    )
    notification = Notification.objects.get(
        notification_type=Notification.Type.PRODUCT_SUBMITTED_FOR_REVIEW,
        product=product,
        user=manager,
    )
    assert "Updated photos and title" in notification.body


@pytest.mark.integration
@pytest.mark.django_db
def test_comment_alias_alone_is_rejected_for_approved_product(
    api_client, seller, product_factory
):
    product = product_factory(owner=seller, status=Product.Status.APPROVED)
    authenticate(api_client, seller)
    response = api_client.patch(
        f"/api/v1/products/{product.id}/",
        {"comment": "Just a note"},
        format="json",
    )
    assert response.status_code == 400
    product.refresh_from_db()
    assert product.pending_changes == {}


@pytest.mark.integration
@pytest.mark.django_db
def test_accepted_offer_price_is_not_stored_as_seller_edit(
    api_client, seller, manager, product_factory
):
    product = product_factory(
        owner=seller,
        status=Product.Status.APPROVED,
        unit_price="30.00",
    )
    PriceNegotiation.objects.create(
        product=product,
        manager=manager,
        currency=product.currency,
        current_unit_price="30.00",
        proposed_unit_price="50.00",
        message="Offer",
        status=PriceNegotiation.Status.ACCEPTED,
    )
    variant = product.variants.get()
    authenticate(api_client, seller)
    response = api_client.patch(
        f"/api/v1/products/{product.id}/",
        {
            "unit_price": "50.00",
            "variants": [
                {
                    "color": variant.color,
                    "materials": variant.materials,
                    "width_cm": str(variant.width_cm),
                    "height_cm": str(variant.height_cm),
                    "length_cm": str(variant.length_cm),
                    "quantity": 55,
                }
            ],
            "change_comment": "qty only",
        },
        format="json",
    )
    assert response.status_code == 200
    product.refresh_from_db()
    assert "unit_price" not in product.pending_changes
    assert product.pending_changes["variants"][0]["quantity"] == 55
    assert Notification.objects.filter(
        notification_type=Notification.Type.PRODUCT_CHANGE_REQUESTED,
        product=product,
        user=manager,
    ).exists()


@pytest.mark.integration
@pytest.mark.django_db
def test_later_seller_edit_still_notifies_managers(
    api_client, seller, manager, product_factory
):
    product = product_factory(owner=seller, status=Product.Status.APPROVED, title="Old")
    authenticate(api_client, seller)
    assert (
        api_client.patch(
            f"/api/v1/products/{product.id}/",
            {"title": "First"},
            format="json",
        ).status_code
        == 200
    )
    first_count = Notification.objects.filter(
        notification_type=Notification.Type.PRODUCT_CHANGE_REQUESTED,
        product=product,
    ).count()
    assert (
        api_client.patch(
            f"/api/v1/products/{product.id}/",
            {"title": "Second"},
            format="json",
        ).status_code
        == 200
    )
    assert (
        Notification.objects.filter(
            notification_type=Notification.Type.PRODUCT_CHANGE_REQUESTED,
            product=product,
        ).count()
        == first_count + 1
    )
