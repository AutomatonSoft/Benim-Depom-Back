import pytest

from apps.ean.models import EanCode
from apps.notifications.models import Notification
from apps.products.models import Product


def authenticate(client, user):
    client.force_authenticate(user=user)
    return client


@pytest.mark.integration
@pytest.mark.django_db
def test_submit_requires_image_and_does_not_reserve_eans(api_client, seller, product_factory, product_image_factory):
    product = product_factory(owner=seller)
    authenticate(api_client, seller)

    response = api_client.post(f"/api/v1/products/{product.id}/submit/")
    assert response.status_code == 400
    assert "images" in response.data

    product_image_factory(product=product)
    response = api_client.post(f"/api/v1/products/{product.id}/submit/")
    assert response.status_code == 200
    product.refresh_from_db()
    assert product.status == Product.Status.SUBMITTED
    assert product.ean_codes.count() == 0


@pytest.mark.integration
@pytest.mark.django_db
def test_manager_approval_requires_two_pool_codes_then_assigns_one_per_account(
    api_client, seller, manager, product_factory, product_image_factory
):
    product = product_factory(owner=seller, status=Product.Status.SUBMITTED)
    product_image_factory(product=product)
    authenticate(api_client, manager)

    response = api_client.post(f"/api/v1/manager/products/{product.id}/approve/", {"comment": "ok"}, format="json")
    assert response.status_code == 400
    product.refresh_from_db()
    assert product.status == Product.Status.SUBMITTED

    EanCode.objects.create(code="4006381333931", account=EanCode.Account.JV, imported_by=manager)
    EanCode.objects.create(code="9501101530003", account=EanCode.Account.XL, imported_by=manager)
    response = api_client.post(f"/api/v1/manager/products/{product.id}/approve/", {"comment": "ok"}, format="json")
    assert response.status_code == 200
    assert response.data["status"] == Product.Status.APPROVED
    assert {code["account"] for code in response.data["ean_codes"]} == {"jv", "xl"}

    authenticate(api_client, seller)
    response = api_client.get(f"/api/v1/products/{product.id}/")
    assert response.status_code == 200
    assert "ean_codes" not in response.data


@pytest.mark.integration
@pytest.mark.django_db
def test_moderation_permissions_reject_validation_and_withdrawal(api_client, seller, manager, product_factory, product_image_factory):
    product = product_factory(owner=seller, status=Product.Status.SUBMITTED)
    product_image_factory(product=product)

    authenticate(api_client, seller)
    assert api_client.post(f"/api/v1/manager/products/{product.id}/reject/", {"comment": "x"}, format="json").status_code == 403

    authenticate(api_client, manager)
    assert api_client.post(f"/api/v1/manager/products/{product.id}/reject/", {"comment": " "}, format="json").status_code == 400

    authenticate(api_client, seller)
    response = api_client.post(f"/api/v1/products/{product.id}/withdraw/")
    assert response.status_code == 204
    product.refresh_from_db()
    assert product.status == Product.Status.ARCHIVED
    assert api_client.post(f"/api/v1/products/{product.id}/withdraw/").status_code == 400


@pytest.mark.integration
@pytest.mark.django_db
def test_manager_ean_import_reports_duplicates_invalid_codes_and_summary(api_client, manager, seller):
    authenticate(api_client, seller)
    assert api_client.post(
        "/api/v1/manager/eans/import/", {"account": "jv", "codes": "4006381333931"}, format="json"
    ).status_code == 403

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
def test_manager_reject_and_manual_availability_request(api_client, seller, manager, product_factory):
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

    response = api_client.post(
        f"/api/v1/manager/products/{approved.id}/availability-request/"
    )
    assert response.status_code == 200
    approved.refresh_from_db()
    assert approved.availability_reminder_sent_at is not None
    assert Notification.objects.filter(
        user=seller,
        product=approved,
        notification_type=Notification.Type.PRODUCT_AVAILABILITY_REMINDER,
    ).exists()

    response = api_client.get("/api/v1/manager/products/?status=approved")
    assert response.status_code == 200
    assert [item["id"] for item in response.data["results"]] == [approved.id]
