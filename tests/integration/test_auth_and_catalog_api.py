from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.services import issue_email_verification_code
from apps.accounts.tasks import send_password_reset_code
from apps.catalog.otto_catalog import clear_otto_catalog_cache


def authenticate(client, user):
    client.force_authenticate(user=user)
    return client


@pytest.mark.integration
def test_password_reset_email_task_sends_code():
    send_password_reset_code.run(email="reset@example.com", code="123456")

    message = mail.outbox[-1]
    assert message.to == ["reset@example.com"]
    assert "123456" in message.body


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
        {"email": "new_seller@example.com", "password": password},
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
            "email": "second_manager@example.com",
            "password": password,
            "password_confirm": password,
        },
        format="json",
    )
    assert response.status_code == 201
    assert response.data["role"] == User.Role.MANAGER


@pytest.mark.integration
@pytest.mark.django_db
def test_duplicate_usernames_are_allowed_and_login_uses_email(api_client, password):
    User.objects.create_user(
        username="same_name",
        email="first-same@example.com",
        password=password,
    )
    second = User.objects.create_user(
        username="same_name",
        email="second-same@example.com",
        password=password,
    )

    login = api_client.post(
        "/api/v1/auth/login/",
        {"email": second.email, "password": password},
        format="json",
    )
    assert login.status_code == 200

    me = api_client.get(
        "/api/v1/auth/me/",
        HTTP_AUTHORIZATION=f"Bearer {login.data['access']}",
    )
    assert me.status_code == 200
    assert me.data["email"] == second.email
    assert me.data["username"] == "same_name"


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
        {"email": seller.email, "password": "wrong-password"},
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
def test_manager_lists_only_sellers_with_search_and_activity_filter(
    api_client,
    manager,
    seller,
    second_seller,
):
    seller.first_name = "Nikita"
    seller.email = "nikita@example.com"
    seller.save(update_fields=("first_name", "email"))
    second_seller.is_active = False
    second_seller.save(update_fields=("is_active",))

    authenticate(api_client, seller)
    response = api_client.get("/api/v1/manager/users/sellers/")
    assert response.status_code == 403

    authenticate(api_client, manager)
    response = api_client.get(
        "/api/v1/manager/users/sellers/",
        {"search": "nikita", "is_active": "true"},
    )
    assert response.status_code == 200
    assert response.data["count"] == 1
    assert response.data["results"][0]["id"] == seller.id
    assert response.data["results"][0]["role"] == User.Role.SELLER
    assert "product_count" in response.data["results"][0]
    assert "is_email_verified" not in response.data["results"][0]


@pytest.mark.integration
@pytest.mark.django_db
def test_manager_seller_list_hides_unverified_registrations(
    api_client,
    manager,
    seller,
    user_factory,
):
    unverified = user_factory(
        username="unverified_seller",
        is_email_verified=False,
        is_active=False,
    )

    authenticate(api_client, manager)
    response = api_client.get("/api/v1/manager/users/sellers/")
    assert response.status_code == 200
    ids = {item["id"] for item in response.data["results"]}
    assert seller.id in ids
    assert unverified.id not in ids


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_manager_can_delete_seller_and_seller_cannot(
    api_client, seller, manager, product_factory
):
    product_factory(owner=seller)
    authenticate(api_client, seller)
    assert (
        api_client.delete(f"/api/v1/manager/users/sellers/{seller.id}/").status_code
        == 403
    )

    authenticate(api_client, manager)
    listed = api_client.get("/api/v1/manager/users/sellers/")
    assert listed.status_code == 200
    row = next(item for item in listed.data["results"] if item["id"] == seller.id)
    assert row["product_count"] == 1

    response = api_client.delete(f"/api/v1/manager/users/sellers/{seller.id}/")
    assert response.status_code == 204
    assert not User.objects.filter(pk=seller.id).exists()


@pytest.mark.integration
@pytest.mark.django_db
def test_profile_refresh_and_logout_blacklist_refresh_token(
    api_client, seller, password
):
    login = api_client.post(
        "/api/v1/auth/login/",
        {"email": seller.email, "password": password},
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
def test_authenticated_user_can_change_password_and_revokes_refresh_tokens(
    api_client,
    seller,
    password,
):
    login = api_client.post(
        "/api/v1/auth/login/",
        {"email": seller.email, "password": password},
        format="json",
    )
    assert login.status_code == 200
    access, refresh = login.data["access"], login.data["refresh"]

    wrong_password = api_client.post(
        "/api/v1/auth/password/change/",
        {
            "current_password": "wrong-password",
            "new_password": "DifferentPassword123!",
            "new_password_confirm": "DifferentPassword123!",
        },
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {access}",
    )
    assert wrong_password.status_code == 400

    changed = api_client.post(
        "/api/v1/auth/password/change/",
        {
            "current_password": password,
            "new_password": "DifferentPassword123!",
            "new_password_confirm": "DifferentPassword123!",
        },
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {access}",
    )
    assert changed.status_code == 204

    assert (
        api_client.post(
            "/api/v1/auth/refresh/",
            {"refresh": refresh},
            format="json",
        ).status_code
        == 401
    )
    assert (
        api_client.post(
            "/api/v1/auth/login/",
            {"email": seller.email, "password": password},
            format="json",
        ).status_code
        == 401
    )
    assert (
        api_client.post(
            "/api/v1/auth/login/",
            {
                "email": seller.email,
                "password": "DifferentPassword123!",
            },
            format="json",
        ).status_code
        == 200
    )


@pytest.mark.integration
@pytest.mark.django_db
def test_password_reset_changes_password_and_revokes_refresh_tokens(
    api_client,
    seller,
    password,
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    seller.email = "reset@example.com"
    seller.save(update_fields=("email",))
    sent = []
    monkeypatch.setattr(
        "apps.accounts.views.send_password_reset_code.delay",
        lambda **kwargs: sent.append(kwargs),
    )

    login = api_client.post(
        "/api/v1/auth/login/",
        {"email": seller.email, "password": password},
        format="json",
    )
    refresh = login.data["refresh"]

    unknown = api_client.post(
        "/api/v1/auth/password/reset/request/",
        {"email": "unknown@example.com"},
        format="json",
    )
    assert unknown.status_code == 202
    assert sent == []

    with django_capture_on_commit_callbacks(execute=True):
        requested = api_client.post(
            "/api/v1/auth/password/reset/request/",
            {"email": seller.email},
            format="json",
        )
    assert requested.status_code == 202
    assert len(sent) == 1

    wrong_code = "000000" if sent[0]["code"] != "000000" else "999999"
    wrong = api_client.post(
        "/api/v1/auth/password/reset/verify/",
        {"email": seller.email, "code": wrong_code},
        format="json",
    )
    assert wrong.status_code == 400

    seller.refresh_from_db()
    assert seller.password_reset_attempts == 1
    verified = api_client.post(
        "/api/v1/auth/password/reset/verify/",
        {"email": seller.email, "code": sent[0]["code"]},
        format="json",
    )
    assert verified.status_code == 200

    weak_password = api_client.post(
        "/api/v1/auth/password/reset/complete/",
        {
            "reset_token": verified.data["reset_token"],
            "new_password": "password",
            "new_password_confirm": "password",
        },
        format="json",
    )
    assert weak_password.status_code == 400
    assert "new_password" in weak_password.data

    completed = api_client.post(
        "/api/v1/auth/password/reset/complete/",
        {
            "reset_token": verified.data["reset_token"],
            "new_password": "DifferentPassword123!",
            "new_password_confirm": "DifferentPassword123!",
        },
        format="json",
    )
    assert completed.status_code == 204
    assert (
        api_client.post(
            "/api/v1/auth/refresh/",
            {"refresh": refresh},
            format="json",
        ).status_code
        == 401
    )
    assert (
        api_client.post(
            "/api/v1/auth/login/",
            {"email": seller.email, "password": password},
            format="json",
        ).status_code
        == 401
    )
    assert (
        api_client.post(
            "/api/v1/auth/login/",
            {"email": seller.email, "password": "DifferentPassword123!"},
            format="json",
        ).status_code
        == 200
    )


@pytest.mark.integration
@pytest.mark.django_db
def test_email_verification_rejects_wrong_code_and_hides_unknown_resend(
    api_client,
    seller,
):
    seller.email = "verification@example.com"
    seller.is_active = False
    seller.is_email_verified = False
    seller.save(update_fields=("email", "is_active", "is_email_verified"))
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
    seller.is_email_verified = False
    seller.save(update_fields=("is_active", "is_email_verified"))
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
    seller.is_email_verified = False
    seller.save(update_fields=("email", "is_active", "is_email_verified"))
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
    assert "is_email_verified" not in response.data


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

    russian_groups = api_client.get("/api/v1/catalog/otto/category-groups/ru/")
    if russian_groups.status_code == 200:
        assert russian_groups.data.get("results")
    else:
        assert russian_groups.status_code == 404
