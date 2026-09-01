import json

import pytest

from apps.accounts.models import User
from apps.accounts.services import issue_email_verification_code
from apps.ean.models import EanCode
from apps.notifications.models import Notification
from apps.products.models import Product


def bearer(client, token):
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")


@pytest.mark.e2e
@pytest.mark.django_db(transaction=True)
def test_seller_to_manager_approval_and_deactivation_journey(
    api_client, image_file, password
):
    registration = {
        "username": "journey_seller",
        "email": "journey_seller@example.com",
        "password": password,
        "password_confirm": password,
        "preferred_language": "ru",
    }
    assert (
        api_client.post(
            "/api/v1/auth/register/",
            registration,
            format="json",
        ).status_code
        == 201
    )
    seller = User.objects.get(username="journey_seller")
    code = issue_email_verification_code(user=seller)
    verification = api_client.post(
        "/api/v1/auth/email/verify/",
        {"email": seller.email, "code": code},
        format="json",
    )
    assert verification.status_code == 200
    bearer(api_client, verification.data["access"])

    create = api_client.post(
        "/api/v1/products/",
        {
            "title": "Journey chair",
            "product_type": "Chair",
            "unit_price": "1000.00",
            "currency": "TRY",
            "otto_category_id": 26822,
            "otto_category_group_id": 3593,
            "variants": json.dumps(
                [
                    {
                        "color_hex": "#112233",
                        "materials": ["Wood"],
                        "width_cm": "50",
                        "height_cm": "90",
                        "length_cm": "55",
                        "quantity": 2,
                    }
                ]
            ),
            "images": [image_file()],
        },
        format="multipart",
    )
    assert create.status_code == 201
    product_id = create.data["id"]
    assert create.data["status"] == Product.Status.SUBMITTED

    manager = User.objects.create_user(
        username="journey_manager",
        email="journey_manager@example.com",
        password=password,
        role=User.Role.MANAGER,
    )
    api_client.credentials()
    manager_login = api_client.post(
        "/api/v1/auth/login/",
        {"email": manager.email, "password": password},
        format="json",
    )
    assert manager_login.status_code == 200
    bearer(api_client, manager_login.data["access"])
    assert (
        api_client.post(
            "/api/v1/manager/eans/import/",
            {"account": "jv", "codes": "4006381333931"},
            format="json",
        ).status_code
        == 201
    )
    assert (
        api_client.post(
            "/api/v1/manager/eans/import/",
            {"account": "xl", "codes": "9501101530003"},
            format="json",
        ).status_code
        == 201
    )
    approval = api_client.post(
        f"/api/v1/manager/products/{product_id}/approve/",
        {"comment": "Approved"},
        format="json",
    )
    assert approval.status_code == 200
    assert approval.data["status"] == Product.Status.APPROVED
    assert approval.data["ean_jv"] == "4006381333931"
    assert approval.data["ean_xl"] == "9501101530003"

    seller_login = api_client.post(
        "/api/v1/auth/login/",
        {"email": "journey_seller@example.com", "password": password},
        format="json",
    )
    bearer(api_client, seller_login.data["access"])
    deactivation = api_client.post(f"/api/v1/products/{product_id}/deactivate/")
    assert deactivation.status_code == 202
    product = Product.objects.get(pk=product_id)
    assert product.status == Product.Status.APPROVED
    assert product.deactivation_requested_at is not None
    assert Notification.objects.filter(
        user=manager,
        notification_type=Notification.Type.PRODUCT_DEACTIVATION_REQUESTED,
    ).exists()

    bearer(api_client, manager_login.data["access"])
    deactivation = api_client.post(f"/api/v1/manager/products/{product_id}/deactivate/")
    assert deactivation.status_code == 400
    product.refresh_from_db()
    assert product.status == Product.Status.APPROVED
    assert product.deactivation_requested_at is not None
    assert EanCode.objects.filter(product=product).count() == 2


@pytest.mark.e2e
@pytest.mark.django_db(transaction=True)
def test_seller_can_withdraw_before_manager_approval(api_client, image_file, password):
    user = User.objects.create_user(
        username="withdraw_seller",
        email="withdraw_seller@example.com",
        password=password,
    )
    login = api_client.post(
        "/api/v1/auth/login/",
        {"email": user.email, "password": password},
        format="json",
    )
    bearer(api_client, login.data["access"])
    create = api_client.post(
        "/api/v1/products/",
        {
            "title": "Wrong table",
            "product_type": "Table",
            "unit_price": "1000.00",
            "currency": "TRY",
            "otto_category_id": 26822,
            "otto_category_group_id": 3593,
            "variants": json.dumps(
                [
                    {
                        "color_hex": "#FFFFFF",
                        "materials": ["Metal"],
                        "width_cm": "1",
                        "height_cm": "1",
                        "length_cm": "1",
                        "quantity": 1,
                    }
                ]
            ),
            "images": [image_file()],
        },
        format="multipart",
    )
    product_id = create.data["id"]
    assert create.status_code == 201
    assert create.data["status"] == Product.Status.SUBMITTED
    assert (
        api_client.post(f"/api/v1/products/{product_id}/withdraw/").status_code == 204
    )
    assert api_client.get(f"/api/v1/products/{product_id}/").status_code == 404
