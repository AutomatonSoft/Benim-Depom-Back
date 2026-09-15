from decimal import Decimal

import pytest

from apps.notifications.models import Notification
from apps.orchestrator.models import MarketplaceJob, MarketplacePublication
from apps.products.models import PriceNegotiation, Product


def authenticate(client, user):
    client.force_authenticate(user=user)
    return client


@pytest.mark.integration
@pytest.mark.django_db
def test_manager_can_create_price_offer_on_submitted_and_approved(
    api_client,
    seller,
    manager,
    product_factory,
    django_capture_on_commit_callbacks,
):
    submitted = product_factory(
        owner=seller,
        status=Product.Status.SUBMITTED,
        unit_price=Decimal("200.00"),
        currency=Product.Currency.TRY,
    )
    authenticate(api_client, manager)
    with django_capture_on_commit_callbacks(execute=True):
        response = api_client.post(
            f"/api/v1/manager/products/{submitted.id}/price-negotiation/",
            {
                "proposed_unit_price": "150.00",
                "message": "Let's list this at 150 TRY to sell faster.",
            },
            format="json",
        )
    assert response.status_code == 201
    assert response.data["active_price_negotiation"]["proposed_unit_price"] == "150.00"
    assert response.data["active_price_negotiation"]["currency"] == "TRY"
    assert response.data["active_price_negotiation"]["status"] == "pending"
    assert Notification.objects.filter(
        notification_type=Notification.Type.PRICE_NEGOTIATION_OFFER,
        product=submitted,
        user=seller,
    ).exists()

    approved = product_factory(
        owner=seller,
        status=Product.Status.APPROVED,
        unit_price=Decimal("90.00"),
        currency=Product.Currency.EUR,
    )
    response = api_client.post(
        f"/api/v1/manager/products/{approved.id}/price-negotiation/",
        {
            "proposed_unit_price": "80.00",
            "message": "We recommend 80 EUR.",
        },
        format="json",
    )
    assert response.status_code == 201
    assert response.data["active_price_negotiation"]["currency"] == "EUR"


@pytest.mark.integration
@pytest.mark.django_db
def test_price_negotiation_rejected_for_draft_product(
    api_client, seller, manager, product_factory
):
    product = product_factory(owner=seller, status=Product.Status.DRAFT)
    authenticate(api_client, manager)
    response = api_client.post(
        f"/api/v1/manager/products/{product.id}/price-negotiation/",
        {
            "proposed_unit_price": "100.00",
            "message": "Please consider this price.",
        },
        format="json",
    )
    assert response.status_code == 400


@pytest.mark.integration
@pytest.mark.django_db
def test_new_price_offer_supersedes_previous_pending(
    api_client, seller, manager, product_factory
):
    product = product_factory(
        owner=seller,
        status=Product.Status.APPROVED,
        unit_price=Decimal("200.00"),
    )
    authenticate(api_client, manager)
    first = api_client.post(
        f"/api/v1/manager/products/{product.id}/price-negotiation/",
        {
            "proposed_unit_price": "180.00",
            "message": "First offer",
        },
        format="json",
    )
    assert first.status_code == 201
    first_id = first.data["active_price_negotiation"]["id"]

    second = api_client.post(
        f"/api/v1/manager/products/{product.id}/price-negotiation/",
        {
            "proposed_unit_price": "150.00",
            "message": "Second offer",
        },
        format="json",
    )
    assert second.status_code == 201
    assert second.data["active_price_negotiation"]["id"] != first_id
    assert second.data["active_price_negotiation"]["proposed_unit_price"] == "150.00"

    old = PriceNegotiation.objects.get(pk=first_id)
    assert old.status == PriceNegotiation.Status.SUPERSEDED
    assert (
        PriceNegotiation.objects.filter(
            product=product, status=PriceNegotiation.Status.PENDING
        ).count()
        == 1
    )


@pytest.mark.integration
@pytest.mark.django_db
def test_seller_accept_updates_catalog_price_without_marketplace_jobs(
    api_client,
    seller,
    manager,
    product_factory,
    django_capture_on_commit_callbacks,
):
    product = product_factory(
        owner=seller,
        status=Product.Status.APPROVED,
        unit_price=Decimal("200.00"),
        currency=Product.Currency.TRY,
    )
    MarketplacePublication.objects.create(
        product=product,
        marketplace="otto",
        account="jv",
        status=MarketplacePublication.Status.ACTIVE,
        ean=product.ean_jv or "4012345678901",
    )
    authenticate(api_client, manager)
    offer = api_client.post(
        f"/api/v1/manager/products/{product.id}/price-negotiation/",
        {
            "proposed_unit_price": "150.00",
            "message": "150 TRY would help this sell faster.",
        },
        format="json",
    )
    assert offer.status_code == 201

    authenticate(api_client, seller)
    with django_capture_on_commit_callbacks(execute=True):
        response = api_client.post(
            f"/api/v1/products/{product.id}/price-negotiation/respond/",
            {"accepted": True, "comment": "Ok, let's try it"},
            format="json",
        )
    assert response.status_code == 200
    product.refresh_from_db()
    assert product.unit_price == Decimal("150.00")
    assert product.currency == Product.Currency.TRY
    assert response.data["unit_price"] == "150.00"
    assert response.data["active_price_negotiation"] is None
    assert response.data["latest_price_negotiation"]["status"] == "accepted"
    assert response.data["latest_price_negotiation"]["seller_comment"] == (
        "Ok, let's try it"
    )
    assert MarketplaceJob.objects.filter(product=product).count() == 0
    assert Notification.objects.filter(
        notification_type=Notification.Type.PRICE_NEGOTIATION_RESPONSE,
        product=product,
        user=manager,
    ).exists()


@pytest.mark.integration
@pytest.mark.django_db
def test_seller_reject_keeps_price_and_notifies_managers(
    api_client,
    seller,
    manager,
    product_factory,
    django_capture_on_commit_callbacks,
):
    product = product_factory(
        owner=seller,
        status=Product.Status.SUBMITTED,
        unit_price=Decimal("200.00"),
    )
    authenticate(api_client, manager)
    with django_capture_on_commit_callbacks(execute=True):
        assert (
            api_client.post(
                f"/api/v1/manager/products/{product.id}/price-negotiation/",
                {
                    "proposed_unit_price": "140.00",
                    "message": "Could we try 140?",
                },
                format="json",
            ).status_code
            == 201
        )

    authenticate(api_client, seller)
    with django_capture_on_commit_callbacks(execute=True):
        response = api_client.post(
            f"/api/v1/products/{product.id}/price-negotiation/respond/",
            {"accepted": False, "comment": "Too low for now"},
            format="json",
        )
    assert response.status_code == 200
    product.refresh_from_db()
    assert product.unit_price == Decimal("200.00")
    assert response.data["latest_price_negotiation"]["status"] == "rejected"
    notification = Notification.objects.get(
        notification_type=Notification.Type.PRICE_NEGOTIATION_RESPONSE,
        product=product,
        user=manager,
    )
    assert "Too low for now" in notification.body
    authenticate(api_client, seller)
    listed = api_client.get("/api/v1/notifications/")
    assert listed.status_code == 200
    offer_row = next(
        row
        for row in listed.data["results"]
        if row["notification_type"] == "price_negotiation_offer"
    )
    assert offer_row["price_negotiation_status"] == "rejected"
    assert offer_row["price_accepted"] is False
    assert offer_row["seller_comment"] == "Too low for now"
    assert offer_row["responded_at"] is not None
    assert offer_row["is_read"] is True

    authenticate(api_client, manager)
    manager_listed = api_client.get("/api/v1/notifications/?category=price")
    assert manager_listed.status_code == 200
    response_row = next(
        row
        for row in manager_listed.data["results"]
        if row["notification_type"] == "price_negotiation_response"
    )
    assert response_row["price_accepted"] is False
    assert response_row["seller_comment"] == "Too low for now"
    assert response_row["price_negotiation_status"] == "rejected"


@pytest.mark.integration
@pytest.mark.django_db
def test_replaced_offer_notification_is_marked_superseded(
    api_client, seller, manager, product_factory, django_capture_on_commit_callbacks
):
    product = product_factory(
        owner=seller,
        status=Product.Status.APPROVED,
        unit_price=Decimal("200.00"),
    )
    authenticate(api_client, manager)
    with django_capture_on_commit_callbacks(execute=True):
        assert (
            api_client.post(
                f"/api/v1/manager/products/{product.id}/price-negotiation/",
                {
                    "proposed_unit_price": "100.00",
                    "message": "First offer",
                },
                format="json",
            ).status_code
            == 201
        )
    with django_capture_on_commit_callbacks(execute=True):
        assert (
            api_client.post(
                f"/api/v1/manager/products/{product.id}/price-negotiation/",
                {
                    "proposed_unit_price": "150.00",
                    "message": "Second offer",
                },
                format="json",
            ).status_code
            == 201
        )

    authenticate(api_client, seller)
    listed = api_client.get("/api/v1/notifications/")
    offers = [
        row
        for row in listed.data["results"]
        if row["notification_type"] == "price_negotiation_offer"
    ]
    by_price = {row["proposed_unit_price"]: row for row in offers}
    assert by_price["100.00"]["price_negotiation_status"] == "superseded"
    assert by_price["100.00"]["price_accepted"] is None
    assert by_price["150.00"]["price_negotiation_status"] == "pending"
    assert by_price["150.00"]["price_accepted"] is None


@pytest.mark.integration
@pytest.mark.django_db
def test_manager_can_list_price_negotiation_history(
    api_client, seller, manager, product_factory
):
    product = product_factory(
        owner=seller,
        status=Product.Status.APPROVED,
        unit_price=Decimal("200.00"),
    )
    authenticate(api_client, manager)
    assert (
        api_client.post(
            f"/api/v1/manager/products/{product.id}/price-negotiation/",
            {
                "proposed_unit_price": "150.00",
                "message": "First offer",
            },
            format="json",
        ).status_code
        == 201
    )
    assert (
        api_client.post(
            f"/api/v1/manager/products/{product.id}/price-negotiation/",
            {
                "proposed_unit_price": "160.00",
                "message": "Second offer",
            },
            format="json",
        ).status_code
        == 201
    )
    response = api_client.get(
        f"/api/v1/products/{product.id}/price-negotiations/?page=1"
    )
    assert response.status_code == 200
    assert response.data["count"] == 2
    assert len(response.data["results"]) == 2
    assert response.data["results"][0]["proposed_unit_price"] == "160.00"
    assert response.data["results"][0]["status"] == "pending"
    assert response.data["results"][1]["status"] == "superseded"


@pytest.mark.integration
@pytest.mark.django_db
def test_non_owner_cannot_respond_to_price_offer(
    api_client, seller, second_seller, manager, product_factory
):
    product = product_factory(
        owner=seller,
        status=Product.Status.APPROVED,
        unit_price=Decimal("200.00"),
    )
    authenticate(api_client, manager)
    assert (
        api_client.post(
            f"/api/v1/manager/products/{product.id}/price-negotiation/",
            {
                "proposed_unit_price": "150.00",
                "message": "Offer",
            },
            format="json",
        ).status_code
        == 201
    )

    authenticate(api_client, second_seller)
    response = api_client.post(
        f"/api/v1/products/{product.id}/price-negotiation/respond/",
        {"accepted": True},
        format="json",
    )
    assert response.status_code == 404
    product.refresh_from_db()
    assert product.unit_price == Decimal("200.00")
