import pytest

from apps.ean.models import EanCode
from apps.moderation.models import ModerationDecision
from apps.notifications.models import Notification
from apps.products.models import Product, ProductImage


def authenticate(client, user):
    client.force_authenticate(user=user)
    return client


@pytest.mark.integration
@pytest.mark.django_db
def test_health_and_ean_list_access(api_client, manager):
    assert api_client.get("/api/v1/health/").json() == {"status": "ok"}
    authenticate(api_client, manager)
    EanCode.objects.create(code="4006381333931", account="jv", imported_by=manager)
    EanCode.objects.create(code="9501101530003", account="xl", imported_by=manager)
    response = api_client.get("/api/v1/manager/eans/?account=jv&is_assigned=false")
    assert response.status_code == 200
    assert [item["account"] for item in response.data["results"]] == ["jv"]


@pytest.mark.integration
@pytest.mark.django_db
def test_moderation_history_permissions_manager_listing_and_image_process_endpoint(
    api_client,
    seller,
    second_seller,
    manager,
    product_factory,
    product_image_factory,
    monkeypatch,
):
    product = product_factory(owner=seller, status=Product.Status.SUBMITTED)
    image = product_image_factory(product=product)
    ModerationDecision.objects.create(
        product=product,
        manager=manager,
        decision=ModerationDecision.Decision.REJECTED,
        comment="Previous review",
    )
    authenticate(api_client, second_seller)
    assert (
        api_client.get(f"/api/v1/products/{product.id}/moderation-history/").status_code
        == 404
    )
    assert (
        api_client.post(
            f"/api/v1/products/{product.id}/images/{image.id}/process/"
        ).status_code
        == 403
    )

    authenticate(api_client, seller)
    response = api_client.get(f"/api/v1/products/{product.id}/moderation-history/")
    assert response.status_code == 200
    assert response.data["results"][0]["comment"] == "Previous review"

    authenticate(api_client, manager)
    monkeypatch.setattr(
        "apps.notifications.tasks.process_product_image.delay", lambda image_id: None
    )
    response = api_client.get("/api/v1/manager/products/?owner_id=not-number")
    assert response.status_code == 400
    response = api_client.post(
        f"/api/v1/products/{product.id}/images/{image.id}/process/"
    )
    assert response.status_code == 202
    image.refresh_from_db()
    assert image.processing_status == ProductImage.ProcessingStatus.PENDING


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_seller_deactivation_creates_manager_approval_request(
    api_client, seller, manager, product_factory
):
    product = product_factory(owner=seller, status=Product.Status.APPROVED)
    authenticate(api_client, seller)
    response = api_client.post(f"/api/v1/products/{product.id}/deactivate/")
    assert response.status_code == 202
    product.refresh_from_db()
    assert product.status == Product.Status.APPROVED
    assert product.deactivation_requested_at is not None
    assert Notification.objects.filter(
        user=manager,
        product=product,
        notification_type=Notification.Type.PRODUCT_DEACTIVATION_REQUESTED,
    ).exists()
    assert (
        api_client.post(f"/api/v1/products/{product.id}/deactivate/").status_code == 400
    )

    authenticate(api_client, manager)
    response = api_client.post(f"/api/v1/manager/products/{product.id}/deactivate/")
    # A local product without an external listing has nothing to deactivate.
    # Its global moderation status must remain independent of marketplace state.
    assert response.status_code == 400
    product.refresh_from_db()
    assert product.status == Product.Status.APPROVED
    assert product.deactivation_requested_at is not None


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_manager_can_deactivate_without_a_seller_request(
    api_client,
    seller,
    manager,
    product_factory,
):
    product = product_factory(owner=seller, status=Product.Status.APPROVED)
    authenticate(api_client, manager)

    response = api_client.post(f"/api/v1/manager/products/{product.id}/deactivate/")

    assert response.status_code == 400
    product.refresh_from_db()
    assert product.status == Product.Status.APPROVED
    assert product.is_available is True
    assert product.deactivation_requested_at is None
