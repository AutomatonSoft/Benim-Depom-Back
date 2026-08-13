from unittest.mock import Mock

import pytest

from apps.accounts.models import User
from apps.catalog.models import Category
from rest_framework.exceptions import ValidationError


def authenticate(client, user):
    client.force_authenticate(user=user)
    return client


@pytest.mark.integration
@pytest.mark.django_db
def test_registration_login_profile_and_manager_creation(api_client, manager, password):
    registration = {
        "username": "new_seller",
        "password": password,
        "password_confirm": password,
        "preferred_language": "de",
    }
    response = api_client.post("/api/v1/auth/register/", registration, format="json")
    assert response.status_code == 201
    assert User.objects.get(username="new_seller").role == User.Role.SELLER

    response = api_client.post(
        "/api/v1/auth/login/",
        {"username": "new_seller", "password": password},
        format="json",
    )
    assert response.status_code == 200
    assert {"access", "refresh"} <= set(response.data)

    authenticate(api_client, manager)
    response = api_client.post(
        "/api/v1/manager/users/",
        {
            "username": "second_manager",
            "password": password,
            "password_confirm": password,
        },
        format="json",
    )
    assert response.status_code == 201
    assert response.data["role"] == User.Role.MANAGER


@pytest.mark.integration
@pytest.mark.django_db
def test_auth_rejects_password_mismatch_bad_login_and_seller_manager_creation(
    api_client, seller, password
):
    response = api_client.post(
        "/api/v1/auth/register/",
        {"username": "bad", "password": password, "password_confirm": "different"},
        format="json",
    )
    assert response.status_code == 400

    response = api_client.post(
        "/api/v1/auth/login/",
        {"username": seller.username, "password": "wrong-password"},
        format="json",
    )
    assert response.status_code == 401

    authenticate(api_client, seller)
    response = api_client.post(
        "/api/v1/manager/users/",
        {"username": "not_allowed", "password": password, "password_confirm": password},
        format="json",
    )
    assert response.status_code == 403


@pytest.mark.integration
@pytest.mark.django_db
def test_profile_refresh_and_logout_blacklist_refresh_token(api_client, seller, password):
    login = api_client.post(
        "/api/v1/auth/login/",
        {"username": seller.username, "password": password},
        format="json",
    )
    assert login.status_code == 200
    access, refresh = login.data["access"], login.data["refresh"]

    response = api_client.patch(
        "/api/v1/auth/me/",
        {"phone": "+905550000000", "preferred_language": "tr"},
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {access}",
    )
    assert response.status_code == 200
    assert response.data["preferred_language"] == "tr"

    refresh_response = api_client.post("/api/v1/auth/refresh/", {"refresh": refresh}, format="json")
    assert refresh_response.status_code == 200
    refresh = refresh_response.data.get("refresh", refresh)
    assert api_client.post(
        "/api/v1/auth/logout/", {"refresh": refresh}, format="json", HTTP_AUTHORIZATION=f"Bearer {access}"
    ).status_code == 204
    assert api_client.post("/api/v1/auth/refresh/", {"refresh": refresh}, format="json").status_code == 401


@pytest.mark.integration
@pytest.mark.django_db
def test_phone_verification_binds_only_verified_firebase_phone(api_client, seller, monkeypatch):
    authenticate(api_client, seller)
    monkeypatch.setattr(
        "apps.accounts.views.get_verified_phone_from_id_token",
        Mock(return_value="+77474412519"),
    )
    response = api_client.post(
        "/api/v1/auth/phone/verify/", {"id_token": "verified"}, format="json"
    )
    assert response.status_code == 200
    seller.refresh_from_db()
    assert seller.phone == "+77474412519"
    assert seller.is_phone_verified is True

    monkeypatch.setattr(
        "apps.accounts.views.get_verified_phone_from_id_token",
        Mock(side_effect=ValidationError({"id_token": "Invalid or expired Firebase token."})),
    )
    response = api_client.post("/api/v1/auth/phone/verify/", {"id_token": "bad"}, format="json")
    assert response.status_code == 400

    seller.phone = "+70000000000"
    seller.save(update_fields=["phone"])
    monkeypatch.setattr(
        "apps.accounts.views.get_verified_phone_from_id_token", Mock(return_value="+71111111111")
    )
    response = api_client.post("/api/v1/auth/phone/verify/", {"id_token": "other-phone"}, format="json")
    assert response.status_code == 400


@pytest.mark.integration
@pytest.mark.django_db
def test_catalog_public_visibility_and_manager_write_access(api_client, manager, seller):
    inactive = Category.objects.create(name="Hidden", is_active=False)
    visible = Category.objects.create(name="Visible", is_active=True)

    response = api_client.get("/api/v1/catalog/categories/")
    assert response.status_code == 200
    assert [item["id"] for item in response.data["results"]] == [visible.id]

    authenticate(api_client, seller)
    response = api_client.post("/api/v1/catalog/categories/", {"name": "Nope"}, format="json")
    assert response.status_code == 403

    authenticate(api_client, manager)
    response = api_client.post(
        "/api/v1/catalog/categories/", {"name": "Office", "sort_order": 3}, format="json"
    )
    assert response.status_code == 201
    response = api_client.patch(
        f"/api/v1/catalog/categories/{inactive.id}/", {"is_active": True}, format="json"
    )
    assert response.status_code == 200
