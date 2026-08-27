from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.services import issue_email_verification_code
from apps.catalog.otto_catalog import clear_otto_catalog_cache


def authenticate(client, user):
    client.force_authenticate(user=user)
    return client


@pytest.mark.integration
@pytest.mark.django_db
def test_registration_email_verification_login_and_manager_creation(
    api_client,
    manager,
    password,
):
    registration = {
        "username": "new_seller",
        "email": "new_seller@example.com",
        "password": password,
        "password_confirm": password,
        "preferred_language": "de",
    }
    response = api_client.post("/api/v1/auth/register/", registration, format="json")
    assert response.status_code == 201
    seller = User.objects.get(username="new_seller")
    assert seller.role == User.Role.SELLER
    assert seller.is_active is False
    assert seller.is_email_verified is False
    assert response.data["email_verification_required"] is True

    response = api_client.post(
        "/api/v1/auth/login/",
        {"username": "new_seller", "password": password},
        format="json",
    )
    assert response.status_code == 401

    code = issue_email_verification_code(user=seller)
    response = api_client.post(
        "/api/v1/auth/email/verify/",
        {"email": seller.email, "code": code},
        format="json",
    )
    assert response.status_code == 200
    assert {"access", "refresh"} <= set(response.data)
    seller.refresh_from_db()
    assert seller.is_active is True
    assert seller.is_email_verified is True

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
        {
            "username": "bad",
            "email": "bad@example.com",
            "password": password,
            "password_confirm": "different",
        },
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
def test_profile_refresh_and_logout_blacklist_refresh_token(
    api_client, seller, password
):
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

    refresh_response = api_client.post(
        "/api/v1/auth/refresh/", {"refresh": refresh}, format="json"
    )
    assert refresh_response.status_code == 200
    refresh = refresh_response.data.get("refresh", refresh)
    assert (
        api_client.post(
            "/api/v1/auth/logout/",
            {"refresh": refresh},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {access}",
        ).status_code
        == 204
    )
    assert (
        api_client.post(
            "/api/v1/auth/refresh/", {"refresh": refresh}, format="json"
        ).status_code
        == 401
    )


@pytest.mark.integration
@pytest.mark.django_db
def test_email_verification_rejects_wrong_code_and_hides_unknown_resend(
    api_client,
    seller,
):
    seller.email = "verification@example.com"
    seller.is_active = False
    seller.save(update_fields=("email", "is_active"))
    code = issue_email_verification_code(user=seller)
    wrong_code = "000000" if code != "000000" else "999999"

    response = api_client.post(
        "/api/v1/auth/email/verify/",
        {"email": seller.email, "code": wrong_code},
        format="json",
    )
    assert response.status_code == 400
    seller.refresh_from_db()
    assert seller.email_verification_attempts == 1

    response = api_client.post(
        "/api/v1/auth/email/resend-verification/",
        {"email": "unknown@example.com"},
        format="json",
    )
    assert response.status_code == 202


@pytest.mark.integration
@pytest.mark.django_db
def test_email_verification_rejects_missing_duplicate_expired_and_exhausted_codes(
    api_client,
    seller,
    password,
):
    seller.email = "existing@example.com"
    seller.save(update_fields=("email",))

    missing_email = api_client.post(
        "/api/v1/auth/register/",
        {
            "username": "without_email",
            "password": password,
            "password_confirm": password,
        },
        format="json",
    )
    assert missing_email.status_code == 400
    assert "email" in missing_email.data

    duplicate_email = api_client.post(
        "/api/v1/auth/register/",
        {
            "username": "duplicate_email",
            "email": "EXISTING@example.com",
            "password": password,
            "password_confirm": password,
        },
        format="json",
    )
    assert duplicate_email.status_code == 400
    assert "email" in duplicate_email.data

    seller.is_active = False
    seller.save(update_fields=("is_active",))
    valid_code = issue_email_verification_code(user=seller)
    seller.email_verification_expires_at = timezone.now() - timedelta(seconds=1)
    seller.save(update_fields=("email_verification_expires_at",))

    expired = api_client.post(
        "/api/v1/auth/email/verify/",
        {"email": seller.email, "code": valid_code},
        format="json",
    )
    assert expired.status_code == 400

    valid_code = issue_email_verification_code(user=seller)
    wrong_code = "000000" if valid_code != "000000" else "999999"
    for _ in range(5):
        assert (
            api_client.post(
                "/api/v1/auth/email/verify/",
                {"email": seller.email, "code": wrong_code},
                format="json",
            ).status_code
            == 400
        )

    exhausted = api_client.post(
        "/api/v1/auth/email/verify/",
        {"email": seller.email, "code": valid_code},
        format="json",
    )
    assert exhausted.status_code == 400
    seller.refresh_from_db()
    assert seller.is_active is False
    assert seller.is_email_verified is False


@pytest.mark.integration
@pytest.mark.django_db
def test_email_resend_cooldown_and_profile_cannot_verify_email(api_client, seller):
    seller.email = "resend@example.com"
    seller.is_active = False
    seller.save(update_fields=("email", "is_active"))
    issue_email_verification_code(user=seller)

    cooldown = api_client.post(
        "/api/v1/auth/email/resend-verification/",
        {"email": seller.email},
        format="json",
    )
    assert cooldown.status_code == 400

    seller.is_active = True
    seller.save(update_fields=("is_active",))
    api_client.force_authenticate(seller)
    response = api_client.patch(
        "/api/v1/auth/me/",
        {"is_email_verified": True},
        format="json",
    )
    assert response.status_code == 200
    seller.refresh_from_db()
    assert seller.is_email_verified is False


@pytest.mark.integration
def test_otto_catalog_endpoints_return_localized_overlays(api_client):
    clear_otto_catalog_cache()

    turkish_groups = api_client.get(
        "/api/v1/catalog/otto/category-groups/tr/",
        {"search": "Sandalyeler"},
    )
    assert turkish_groups.status_code == 200, turkish_groups.data
    assert any(
        group["category_group_id"] == 3593 and group["category_group"] == "Sandalyeler"
        for group in turkish_groups.data["results"]
    )

    english_groups = api_client.get(
        "/api/v1/catalog/otto/category-groups/en/",
        {"search": "Chairs"},
    )
    assert english_groups.status_code == 200, english_groups.data
    assert any(
        group["category_group_id"] == 3593 and group["category_group"] == "Chairs"
        for group in english_groups.data["results"]
    )

    unsupported_language_groups = api_client.get(
        "/api/v1/catalog/otto/category-groups/invalid/",
        {"search": "Chairs"},
    )
    assert unsupported_language_groups.status_code == 200, (
        unsupported_language_groups.data
    )
    assert any(
        group["category_group_id"] == 3593 and group["category_group"] == "Chairs"
        for group in unsupported_language_groups.data["results"]
    )

    english_categories = api_client.get(
        "/api/v1/catalog/otto/category-groups/3593/categories/en/",
        {"limit": 200},
    )
    assert english_categories.status_code == 200, english_categories.data
    assert any(
        category["category_id"] == 26822
        and category["category_group"] == "Chairs"
        and category["name"] == "Dining chair"
        for category in english_categories.data["results"]
    )

    english_attributes = api_client.get(
        "/api/v1/catalog/otto/category-groups/3593/attributes/en/",
    )
    assert english_attributes.status_code == 200, english_attributes.data
    assert any(
        attribute["attribute_id"] == 177052
        and attribute["name"] == "Cover abrasion resistance"
        and attribute["attribute_group"] == "Material"
        for attribute in english_attributes.data
    )
