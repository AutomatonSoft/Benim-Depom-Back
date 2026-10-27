from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.apps import apps
from django.db import IntegrityError, connection, transaction
from rest_framework.exceptions import ValidationError

from apps.accounts.models import User
from apps.accounts.services import issue_email_verification_code

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


def registration(password, phone):
    return {
        "username": "seller",
        "first_name": "Alihan",
        "last_name": "Bulkair",
        "email": "shared@example.com",
        "phone": phone,
        "password": password,
        "password_confirm": password,
    }


def test_shared_email_registration_and_codes_are_bound_to_phone(api_client, password):
    users = []
    codes = []
    for phone in ["+905551111111", "+905552222222"]:
        result = api_client.post(
            "/api/v2/auth/register/", registration(password, phone), format="json"
        )
        assert result.status_code == 201, result.data
        users.append(User.objects.get(pk=result.data["user"]["id"]))
    with patch("apps.accounts.services.secrets.randbelow", side_effect=[1, 2]):
        codes = [issue_email_verification_code(user=user) for user in users]

    ambiguous = api_client.post(
        "/api/v2/auth/email/verify/",
        {"email": users[0].email, "code": codes[0]},
        format="json",
    )
    assert ambiguous.status_code == 400
    wrong_account = api_client.post(
        "/api/v2/auth/email/verify/",
        {"email": users[0].email, "phone": users[1].phone, "code": codes[0]},
        format="json",
    )
    assert wrong_account.status_code == 400
    for user, code in zip(users, codes):
        result = api_client.post(
            "/api/v2/auth/email/verify/",
            {"email": user.email, "phone": user.phone, "code": code},
            format="json",
        )
        assert result.status_code == 200, result.data
        assert result.data["user"]["id"] == user.pk
        user.refresh_from_db()
        assert user.is_email_verified and user.is_active


@pytest.mark.parametrize(
    "phone", [None, "", "15", "+0000000000", "abc", "+90555<script>"]
)
def test_registration_requires_valid_phone(api_client, password, phone):
    payload = registration(password, phone)
    if phone is None:
        payload.pop("phone")
    result = api_client.post("/api/v2/auth/register/", payload, format="json")
    assert result.status_code == 400
    assert "phone" in result.data
    assert not User.objects.exists()


@pytest.mark.parametrize(
    "phone", ["+90 (555) 111-1111", "00905551111111", "905551111111"]
)
def test_registration_rejects_duplicate_normalized_phone(
    api_client, user_factory, password, phone
):
    user_factory(phone="+905551111111")
    result = api_client.post(
        "/api/v2/auth/register/", registration(password, phone), format="json"
    )
    assert result.status_code == 400
    assert "phone" in result.data


def test_registration_phone_race_returns_validation_error(
    api_client, user_factory, password
):
    user_factory(phone="+905551111111")
    with patch(
        "apps.accounts.serializers._validate_unique_phone",
        side_effect=["+905551111111", ValidationError("Phone already used.")],
    ):
        result = api_client.post(
            "/api/v2/auth/register/",
            registration(password, "+905551111111"),
            format="json",
        )
    assert result.status_code == 400


def test_phone_login_and_staff_email_stay_separate(api_client, user_factory, password):
    seller = user_factory(email="shared@example.com", phone="+905551111111")
    other = user_factory(
        email=seller.email, phone="+905552222222", password="AnotherPassword123!"
    )
    manager = user_factory(email=seller.email, role=User.Role.MANAGER)
    for phone in [seller.phone, seller.phone.lstrip("+"), "0090 (555) 111-1111"]:
        login = api_client.post(
            "/api/v2/auth/login/", {"phone": phone, "password": password}, format="json"
        )
        assert login.status_code == 200
        me = api_client.get(
            "/api/v2/auth/me/", HTTP_AUTHORIZATION=f"Bearer {login.data['access']}"
        )
        assert me.data["id"] == seller.pk
    assert (
        api_client.post(
            "/api/v2/auth/login/",
            {"phone": other.phone, "password": password},
            format="json",
        ).status_code
        == 401
    )
    staff_login = api_client.post(
        "/api/v2/auth/login/",
        {"email": manager.email.upper(), "password": password},
        format="json",
    )
    assert staff_login.status_code == 200
    me = api_client.get(
        "/api/v2/auth/me/", HTTP_AUTHORIZATION=f"Bearer {staff_login.data['access']}"
    )
    assert me.data["id"] == manager.pk
    seller.is_active = False
    seller.save(update_fields=["is_active"])
    assert (
        api_client.post(
            "/api/v2/auth/login/",
            {"phone": seller.phone, "password": password},
            format="json",
        ).status_code
        == 401
    )
    assert (
        api_client.post(
            "/api/v2/auth/login/",
            {"email": other.email, "password": "AnotherPassword123!"},
            format="json",
        ).status_code
        == 401
    )


def test_profile_phone_unique_required_and_email_can_be_shared(
    api_client, seller, second_seller, password
):
    old_phone = seller.phone
    api_client.force_authenticate(seller)
    for phone in ["", second_seller.phone]:
        result = api_client.patch("/api/v2/auth/me/", {"phone": phone}, format="json")
        assert result.status_code == 400 and "phone" in result.data
    saved = api_client.patch(
        "/api/v2/auth/me/",
        {"email": second_seller.email, "phone": "0090 (555) 333-3333"},
        format="json",
    )
    assert saved.status_code == 200, saved.data
    assert saved.data["phone"] == "+905553333333"
    api_client.force_authenticate(None)
    assert (
        api_client.post(
            "/api/v2/auth/login/",
            {"phone": old_phone, "password": password},
            format="json",
        ).status_code
        == 401
    )
    assert (
        api_client.post(
            "/api/v2/auth/login/",
            {"phone": saved.data["phone"], "password": password},
            format="json",
        ).status_code
        == 200
    )


def test_reset_targets_phone_and_sends_code_to_shared_email(
    api_client, user_factory, password, django_capture_on_commit_callbacks
):
    first = user_factory(email="shared@example.com")
    second = user_factory(email=first.email)
    with patch("apps.accounts.views.send_password_reset_code.delay") as send:
        with django_capture_on_commit_callbacks(execute=True):
            ambiguous = api_client.post(
                "/api/v1/auth/password/reset/request/",
                {"email": first.email},
                format="json",
            )
        assert ambiguous.status_code == 202
        send.assert_not_called()
        with django_capture_on_commit_callbacks(execute=True):
            result = api_client.post(
                "/api/v2/auth/password/reset/request/",
                {"phone": second.phone, "email": second.email},
                format="json",
            )
        assert result.status_code == 202
        assert send.call_args.kwargs["email"] == first.email
        assert send.call_args.kwargs["username"] == second.username
        code = send.call_args.kwargs["code"]
    wrong = api_client.post(
        "/api/v2/auth/password/reset/verify/",
        {"phone": first.phone, "email": first.email, "code": code},
        format="json",
    )
    assert wrong.status_code == 400
    verified = api_client.post(
        "/api/v2/auth/password/reset/verify/",
        {"phone": second.phone, "email": second.email, "code": code},
        format="json",
    )
    assert verified.status_code == 200
    result = api_client.post(
        "/api/v2/auth/password/reset/complete/",
        {
            "reset_token": verified.data["reset_token"],
            "new_password": "ChangedPassword123!",
            "new_password_confirm": "ChangedPassword123!",
        },
        format="json",
    )
    assert result.status_code == 204
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.check_password(password)
    assert second.check_password("ChangedPassword123!")


def test_reset_requires_matching_phone_and_email(
    api_client, user_factory, django_capture_on_commit_callbacks
):
    first = user_factory(email="first@example.com")
    second = user_factory(email="second@example.com")
    with patch("apps.accounts.views.send_password_reset_code.delay") as send:
        with django_capture_on_commit_callbacks(execute=True):
            mismatch = api_client.post(
                "/api/v2/auth/password/reset/request/",
                {"phone": first.phone, "email": second.email},
                format="json",
            )
        assert mismatch.status_code == 400
        assert (
            mismatch.data["detail"]
            == "Phone number and email do not match an active account."
        )
        send.assert_not_called()
        first.refresh_from_db()
        assert not first.password_reset_code_hash

        with django_capture_on_commit_callbacks(execute=True):
            matched = api_client.post(
                "/api/v2/auth/password/reset/request/",
                {"phone": first.phone, "email": f" {first.email.upper()} "},
                format="json",
            )
        assert matched.status_code == 202
        send.assert_called_once()
        assert send.call_args.kwargs["username"] == first.username
        code = send.call_args.kwargs["code"]
    wrong_email = api_client.post(
        "/api/v2/auth/password/reset/verify/",
        {"phone": first.phone, "email": second.email, "code": code},
        format="json",
    )
    assert wrong_email.status_code == 400 and "reset_token" not in wrong_email.data
    verified = api_client.post(
        "/api/v2/auth/password/reset/verify/",
        {"phone": first.phone, "email": first.email, "code": code},
        format="json",
    )
    assert verified.status_code == 200 and verified.data["reset_token"]


@pytest.mark.parametrize("endpoint", ["request", "verify"])
@pytest.mark.parametrize("email", [None, "", "invalid-email"])
def test_v2_password_reset_requires_a_valid_email(api_client, endpoint, email):
    payload = {"phone": "+905551111111", "code": "123456"}
    if email is not None:
        payload["email"] = email
    result = api_client.post(
        f"/api/v2/auth/password/reset/{endpoint}/", payload, format="json"
    )
    assert result.status_code == 400 and "email" in result.data


def test_reset_cooldown_does_not_claim_a_new_code_was_sent(
    api_client, seller, django_capture_on_commit_callbacks
):
    payload = {"phone": seller.phone, "email": seller.email}
    with patch("apps.accounts.views.send_password_reset_code.delay") as send:
        with django_capture_on_commit_callbacks(execute=True):
            first = api_client.post(
                "/api/v2/auth/password/reset/request/", payload, format="json"
            )
        assert first.status_code == 202
        seller.refresh_from_db()
        code_hash = seller.password_reset_code_hash
        with django_capture_on_commit_callbacks(execute=True):
            repeated = api_client.post(
                "/api/v2/auth/password/reset/request/", payload, format="json"
            )
        assert repeated.status_code == 429 and int(repeated["Retry-After"]) > 0
        send.assert_called_once()
        seller.refresh_from_db()
        assert seller.password_reset_code_hash == code_hash
        legacy = api_client.post(
            "/api/v1/auth/password/reset/request/",
            {"email": seller.email},
            format="json",
        )
        assert legacy.status_code == 202


@pytest.mark.parametrize("case", ["wrong_phone", "wrong_email", "inactive"])
def test_reset_rejects_an_unmatched_or_inactive_account(api_client, seller, case):
    payload = {"phone": seller.phone, "email": seller.email}
    if case == "wrong_phone":
        payload["phone"] = "+905559999999"
    elif case == "wrong_email":
        payload["email"] = "unknown@example.com"
    else:
        seller.is_active = False
        seller.save(update_fields=["is_active"])
    with patch("apps.accounts.views.send_password_reset_code.delay") as send:
        result = api_client.post(
            "/api/v2/auth/password/reset/request/", payload, format="json"
        )
        assert result.status_code == 400
        send.assert_not_called()


def test_phone_has_database_uniqueness(user_factory):
    user = user_factory()
    with pytest.raises(IntegrityError), transaction.atomic():
        user_factory(phone=user.phone)


def test_django_administrator_keeps_email_login(api_client, user_factory, password):
    from apps.accounts.admin import StaffAuthenticationForm

    user = user_factory(is_staff=True, is_superuser=True, phone=None)
    assert StaffAuthenticationForm().fields["username"].widget.attrs["maxlength"] == 254
    result = api_client.post(
        "/api/v2/auth/login/",
        {"email": user.email, "password": password},
        format="json",
    )
    assert result.status_code == 200


def test_resend_code_selects_only_requested_phone(
    api_client, user_factory, django_capture_on_commit_callbacks
):
    first = user_factory(
        email="shared@example.com", is_active=False, is_email_verified=False
    )
    second = user_factory(email=first.email, is_active=False, is_email_verified=False)
    with patch("apps.accounts.views.send_email_verification_code.delay") as send:
        with django_capture_on_commit_callbacks(execute=True):
            result = api_client.post(
                "/api/v2/auth/email/resend-verification/",
                {"email": first.email, "phone": second.phone},
                format="json",
            )
        assert result.status_code == 202
        send.assert_called_once()
    first.refresh_from_db()
    second.refresh_from_db()
    assert not first.email_verification_code_hash
    assert second.email_verification_code_hash


def test_sales_filter_does_not_choose_arbitrary_seller(
    api_client, manager, user_factory
):
    first = user_factory(email="shared@example.com")
    user_factory(email=first.email)
    api_client.force_authenticate(manager)
    result = api_client.get(
        "/api/v2/manager/sales-stats/", {"seller_email": first.email}
    )
    assert result.status_code == 400
    assert "seller_email" in result.data


def test_migration_normalizes_legacy_phones_and_keeps_missing_phones(
    user_factory, api_client, password
):
    user = user_factory(phone="0090 (555) 111-1111")
    digits = user_factory(phone="77474412519")
    blank = user_factory(phone="")
    migration = import_module("apps.accounts.migrations.0009_phone_login")
    migration.normalize_existing_phones(apps, SimpleNamespace(connection=connection))
    user.refresh_from_db()
    blank.refresh_from_db()
    assert user.phone == "+905551111111"
    assert blank.phone is None
    digits.refresh_from_db()
    assert digits.phone == "+77474412519"
    for phone in ["77474412519", "+77474412519"]:
        login = api_client.post(
            "/api/v2/auth/login/", {"phone": phone, "password": password}, format="json"
        )
        assert login.status_code == 200


def test_migration_refuses_duplicate_legacy_phones_without_changing_them(user_factory):
    first = user_factory(phone="00905551111111")
    second = user_factory(phone="+905551111111")
    migration = import_module("apps.accounts.migrations.0009_phone_login")
    with pytest.raises(RuntimeError, match="share a phone"):
        migration.normalize_existing_phones(
            apps, SimpleNamespace(connection=connection)
        )
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.phone == "00905551111111" and second.phone == "+905551111111"
