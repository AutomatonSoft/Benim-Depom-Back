import json
from decimal import Decimal

import pytest

from apps.ean.models import EanCode
from apps.moderation.models import ModerationDecision
from apps.notifications.models import Notification
from apps.products.models import Product


def authenticate(client, user):
    client.force_authenticate(user=user)
    return client


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_product_creation_requires_image_and_notifies_managers_without_reserving_eans(
    api_client, seller, manager, product_type, image_file
):
    authenticate(api_client, seller)

    payload = {
        "title": "Moderation chair",
        "product_type": product_type,
        "unit_price": "1000.00",
        "currency": "TRY",
        "warehouse_city": "INE",
        "variants": json.dumps(
            [
                {
                    "color_hex": "#5B91C8",
                    "materials": ["Wood"],
                    "width_cm": "50.00",
                    "height_cm": "90.00",
                    "length_cm": "55.00",
                    "quantity": 3,
                }
            ]
        ),
    }
    response = api_client.post("/api/v1/products/", payload, format="multipart")
    assert response.status_code == 400
    assert "images" in response.data

    payload["images"] = [image_file()]
    response = api_client.post("/api/v1/products/", payload, format="multipart")
    assert response.status_code == 201
    assert response.data["status"] == Product.Status.SUBMITTED
    product = Product.objects.get(pk=response.data["id"])
    assert product.ean_jv == "" and product.ean_xl == ""
    assert Notification.objects.filter(
        user=manager,
        product=product,
        notification_type=Notification.Type.PRODUCT_SUBMITTED_FOR_REVIEW,
    ).exists()


@pytest.mark.integration
@pytest.mark.django_db
def test_manager_can_edit_approved_product_and_images(
    api_client,
    seller,
    manager,
    product_factory,
    product_image_factory,
    image_file,
):
    product = product_factory(owner=seller, status=Product.Status.APPROVED)
    image = product_image_factory(product=product)
    authenticate(api_client, manager)

    response = api_client.patch(
        f"/api/v1/products/{product.id}/",
        {"title": "Manager updated chair", "unit_price": "150.00"},
        format="json",
    )
    assert response.status_code == 200
    assert response.data["title"] == "Manager updated chair"

    response = api_client.post(
        f"/api/v1/products/{product.id}/images/",
        {"image": image_file("manager-image.png"), "is_primary": False},
        format="multipart",
    )
    assert response.status_code == 201

    response = api_client.post(
        f"/api/v1/products/{product.id}/images/{image.id}/make-primary/"
    )
    assert response.status_code == 200


@pytest.mark.integration
@pytest.mark.django_db
def test_manager_can_override_listing_formula_for_one_product(
    api_client, seller, manager, product_factory
):
    from apps.products.models import ExchangeRate

    ExchangeRate.objects.create(
        as_of="2026-09-01",
        eur_to_usd="1.16",
        eur_to_try="50",
        source="test",
    )
    product = product_factory(owner=seller, status=Product.Status.APPROVED)
    other = product_factory(
        owner=seller, title="Untouched chair", status=Product.Status.APPROVED
    )
    authenticate(api_client, manager)
    before = api_client.get(f"/api/v1/products/{product.id}/").data["listing_price_eur"]
    other_before = api_client.get(f"/api/v1/products/{other.id}/").data[
        "listing_price_eur"
    ]

    response = api_client.patch(
        f"/api/v1/products/{product.id}/",
        {
            "pricing_overrides": {
                "margin": "0.5",
                "city_tariffs_eur_per_cbm": {"INE": "10"},
            }
        },
        format="json",
    )
    assert response.status_code == 200
    assert response.data["pricing_formula"]["uses_product_formula"] is True
    assert response.data["listing_price_eur"] != before
    product.refresh_from_db()
    other.refresh_from_db()
    assert product.pricing_overrides["margin"] == "0.5"
    assert other.pricing_overrides == {}
    assert (
        api_client.get(f"/api/v1/products/{other.id}/").data["listing_price_eur"]
        == other_before
    )

    response = api_client.patch(
        f"/api/v1/products/{product.id}/",
        {"listing_price_eur_override": "1149.00"},
        format="json",
    )
    assert response.status_code == 200
    assert Decimal(str(response.data["listing_price_eur"])) == Decimal("1149.00")

    authenticate(api_client, seller)
    rejected = product_factory(owner=seller, status=Product.Status.REJECTED)
    response = api_client.patch(
        f"/api/v1/products/{rejected.id}/",
        {"listing_price_eur_override": "99.00"},
        format="json",
    )
    assert response.status_code == 400


@pytest.mark.integration
@pytest.mark.django_db
def test_manager_approval_requires_two_pool_codes_then_assigns_one_per_account(
    api_client, seller, manager, product_factory, product_image_factory
):
    product = product_factory(owner=seller, status=Product.Status.SUBMITTED)
    product_image_factory(product=product)
    authenticate(api_client, manager)

    response = api_client.post(
        f"/api/v1/manager/products/{product.id}/approve/",
        {"comment": "ok"},
        format="json",
    )
    assert response.status_code == 400
    assert "No free EAN" in str(response.data.get("detail") or response.data)
    product.refresh_from_db()
    assert product.status == Product.Status.SUBMITTED

    EanCode.objects.create(
        code="4006381333931", account=EanCode.Account.JV, imported_by=manager
    )
    EanCode.objects.create(
        code="9501101530003", account=EanCode.Account.XL, imported_by=manager
    )
    response = api_client.post(
        f"/api/v1/manager/products/{product.id}/approve/",
        {"comment": "ok"},
        format="json",
    )
    assert response.status_code == 200
    assert response.data["status"] == Product.Status.APPROVED
    assert response.data["ean_jv"] == "4006381333931"
    assert response.data["ean_xl"] == "9501101530003"

    authenticate(api_client, seller)
    response = api_client.get(f"/api/v1/products/{product.id}/")
    assert response.status_code == 200
    assert "ean_jv" not in response.data
    assert "ean_xl" not in response.data


@pytest.mark.integration
@pytest.mark.django_db
def test_moderation_permissions_reject_validation_and_withdrawal(
    api_client, seller, manager, product_factory, product_image_factory
):
    product = product_factory(owner=seller, status=Product.Status.SUBMITTED)
    product_image_factory(product=product)

    authenticate(api_client, seller)
    assert (
        api_client.post(
            f"/api/v1/manager/products/{product.id}/reject/",
            {"comment": "x"},
            format="json",
        ).status_code
        == 403
    )

    authenticate(api_client, manager)
    assert (
        api_client.post(
            f"/api/v1/manager/products/{product.id}/reject/",
            {"comment": " "},
            format="json",
        ).status_code
        == 400
    )

    authenticate(api_client, seller)
    response = api_client.post(f"/api/v1/products/{product.id}/withdraw/")
    assert response.status_code == 204
    product.refresh_from_db()
    assert product.status == Product.Status.REJECTED
    assert (
        api_client.post(f"/api/v1/products/{product.id}/withdraw/").status_code == 400
    )


@pytest.mark.integration
@pytest.mark.django_db
def test_manager_ean_import_reports_duplicates_invalid_codes_and_summary(
    api_client, manager, seller
):
    authenticate(api_client, seller)
    assert (
        api_client.post(
            "/api/v1/manager/eans/import/",
            {"account": "jv", "codes": "4006381333931"},
            format="json",
        ).status_code
        == 403
    )

    authenticate(api_client, manager)
    response = api_client.post(
        "/api/v1/manager/eans/import/",
        {"account": "jv", "codes": "4006381333931\n4006381333931\nwrong"},
        format="json",
    )
    assert response.status_code == 201
    assert response.data == {
        "created_count": 1,
        "already_exists_count": 0,
        "duplicate_input_count": 1,
        "invalid_count": 1,
        "invalid_codes": ["wrong"],
    }
    response = api_client.get("/api/v1/manager/eans/summary/")
    assert response.status_code == 200
    assert response.data["requires_attention"] is True


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_manager_reject_and_manual_availability_request(
    api_client, seller, manager, product_factory
):
    submitted = product_factory(owner=seller, status=Product.Status.SUBMITTED)
    approved = product_factory(owner=seller, status=Product.Status.APPROVED)
    authenticate(api_client, manager)

    response = api_client.post(
        f"/api/v1/manager/products/{submitted.id}/reject/",
        {"comment": "Please add clear dimensions."},
        format="json",
    )
    assert response.status_code == 200
    submitted.refresh_from_db()
    assert submitted.status == Product.Status.REJECTED
    listing = api_client.get("/api/v1/manager/products/")
    assert listing.status_code == 200
    rejected_row = next(
        item for item in listing.data["results"] if item["id"] == submitted.id
    )
    assert rejected_row["last_moderation_decision"] == "rejected"

    response = api_client.post(
        f"/api/v1/manager/products/{approved.id}/availability-request/"
    )
    assert response.status_code == 200
    assert response.data["availability_reminder_sent_at"] is not None
    approved.refresh_from_db()
    assert approved.availability_reminder_sent_at is not None
    assert Notification.objects.filter(
        user=seller,
        product=approved,
        notification_type=Notification.Type.PRODUCT_AVAILABILITY_REMINDER,
    ).exists()
    reminder = Notification.objects.get(
        user=seller,
        product=approved,
        notification_type=Notification.Type.PRODUCT_AVAILABILITY_REMINDER,
    )
    assert approved.title in reminder.body

    response = api_client.get("/api/v1/manager/products/?status=approved")
    assert response.status_code == 200
    assert [item["id"] for item in response.data["results"]] == [approved.id]


@pytest.mark.integration
@pytest.mark.django_db
def test_manager_dashboard_returns_live_counts(
    api_client, seller, manager, user_factory, product_factory
):
    from datetime import timedelta

    from django.utils import timezone

    from apps.ean.models import EanCode
    from apps.orchestrator.models import MarketplacePublication

    waiting = product_factory(owner=seller, status=Product.Status.SUBMITTED)
    approved = product_factory(
        owner=seller,
        status=Product.Status.APPROVED,
        ean_jv="4012345678901",
        ean_xl="4012345678902",
    )
    MarketplacePublication.objects.create(
        product=approved,
        marketplace=MarketplacePublication.Marketplace.OTTO,
        account=MarketplacePublication.Account.JV,
        ean=approved.ean_jv,
        status=MarketplacePublication.Status.ACTIVE,
        published_at=timezone.now(),
    )
    MarketplacePublication.objects.create(
        product=approved,
        marketplace=MarketplacePublication.Marketplace.OTTO,
        account=MarketplacePublication.Account.XL,
        ean=approved.ean_xl,
        status=MarketplacePublication.Status.ACTIVE,
        published_at=timezone.now(),
    )
    MarketplacePublication.objects.create(
        product=approved,
        marketplace=MarketplacePublication.Marketplace.HOOD,
        account=MarketplacePublication.Account.JV,
        ean=approved.ean_jv,
        status=MarketplacePublication.Status.ACTIVE,
        published_at=timezone.now(),
    )
    user_factory(
        username="old_seller",
        date_joined=timezone.now() - timedelta(days=40),
    )
    user_factory(username="inactive_seller", is_active=False)
    EanCode.objects.create(
        code="2000000000001",
        account=EanCode.Account.JV,
        state=EanCode.State.AVAILABLE,
    )
    EanCode.objects.create(
        code="2000000000002",
        account=EanCode.Account.XL,
        state=EanCode.State.AVAILABLE,
    )

    authenticate(api_client, manager)
    response = api_client.get("/api/v1/manager/dashboard/")
    assert response.status_code == 200
    assert response.data["awaiting_review"] == 1
    assert response.data["awaiting_review_today"] == 1
    assert response.data["published_today"] == 3
    assert response.data["published_today_marketplaces"] == 2
    assert response.data["active_sellers"] == 2
    assert response.data["sellers_joined_this_month"] == 1
    assert response.data["active_listings"] == [
        {"marketplace": "otto", "account": "jv", "count": 1},
        {"marketplace": "otto", "account": "xl", "count": 1},
        {"marketplace": "hood", "account": "jv", "count": 1},
        {"marketplace": "hood", "account": "xl", "count": 0},
        {"marketplace": "kaufland", "account": "jv", "count": 0},
        {"marketplace": "kaufland", "account": "xl", "count": 0},
    ]
    assert response.data["free_eans"] == {"jv": 1, "xl": 1, "total": 2}
    assert [item["id"] for item in response.data["queue"]] == [waiting.id]

    authenticate(api_client, seller)
    assert api_client.get("/api/v1/manager/dashboard/").status_code == 403


@pytest.mark.integration
@pytest.mark.django_db
def test_moderation_history_returns_five_items_per_page(
    api_client, seller, manager, product_factory
):
    product = product_factory(owner=seller, status=Product.Status.SUBMITTED)
    for index in range(6):
        ModerationDecision.objects.create(
            product=product,
            manager=manager,
            decision=ModerationDecision.Decision.REJECTED,
            comment=f"Review {index}",
        )

    authenticate(api_client, manager)
    first_page = api_client.get(
        f"/api/v1/products/{product.id}/moderation-history/?page=1"
    )
    second_page = api_client.get(
        f"/api/v1/products/{product.id}/moderation-history/?page=2"
    )

    assert first_page.status_code == 200
    assert first_page.data["count"] == 6
    assert len(first_page.data["results"]) == 5
    assert second_page.status_code == 200
    assert len(second_page.data["results"]) == 1


@pytest.mark.integration
@pytest.mark.django_db
def test_manager_status_change_respects_listings_and_rejected(
    api_client, seller, manager, product_factory
):
    from apps.orchestrator.models import MarketplacePublication

    rejected = product_factory(owner=seller, status=Product.Status.REJECTED)
    approved = product_factory(
        owner=seller,
        status=Product.Status.APPROVED,
        ean_jv="4012345678901",
        ean_xl="4012345678902",
    )
    authenticate(api_client, manager)

    blocked = api_client.post(
        f"/api/v1/manager/products/{rejected.id}/status/",
        {"status": "submitted"},
        format="json",
    )
    assert blocked.status_code == 400

    returned = api_client.post(
        f"/api/v1/manager/products/{approved.id}/status/",
        {"status": "submitted"},
        format="json",
    )
    assert returned.status_code == 200
    assert returned.data["status"] == "submitted"
    assert returned.data["ean_jv"] == "4012345678901"

    live = product_factory(
        owner=seller,
        status=Product.Status.APPROVED,
        ean_jv="4012345678911",
        ean_xl="4012345678912",
    )
    MarketplacePublication.objects.create(
        product=live,
        marketplace=MarketplacePublication.Marketplace.OTTO,
        account=MarketplacePublication.Account.JV,
        ean=live.ean_jv,
        status=MarketplacePublication.Status.ACTIVE,
    )
    live_blocked = api_client.post(
        f"/api/v1/manager/products/{live.id}/status/",
        {"status": "rejected", "comment": "stop"},
        format="json",
    )
    assert live_blocked.status_code == 400
    assert "Deactivate marketplace listings" in str(live_blocked.data["detail"])
