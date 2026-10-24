from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.accounts.models import User
from apps.accounts.services import (
    issue_email_verification_code,
    manager_confirm_seller_email,
    verify_email_code,
)

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


@pytest.mark.parametrize("confirmation", ["code", "manager"])
def test_registration_notifies_only_after_first_email_confirmation(
    api_client,
    password,
    manager,
    confirmation,
    django_capture_on_commit_callbacks,
):
    with (
        patch("apps.accounts.services.enqueue_seller_registered_email") as enqueue,
        patch("apps.accounts.views.send_email_verification_code.delay"),
    ):
        with django_capture_on_commit_callbacks(execute=True):
            registration = api_client.post(
                "/api/v1/auth/register/",
                {
                    "username": "new_seller",
                    "first_name": "Alihan",
                    "last_name": "Bulkair",
                    "email": "new-seller@example.com",
                    "password": password,
                    "password_confirm": password,
                },
                format="json",
            )
        assert registration.status_code == 201, registration.data
        enqueue.assert_not_called()
        seller = User.objects.get(email="new-seller@example.com")
        code = issue_email_verification_code(user=seller)
        if confirmation == "manager":
            api_client.force_authenticate(manager)
            url = f"/api/v1/manager/users/sellers/{seller.pk}/confirm-email/"
            payload = {}
        else:
            url = "/api/v1/auth/email/verify/"
            payload = {"email": seller.email, "code": code}

        with django_capture_on_commit_callbacks(execute=True):
            response = api_client.post(url, payload, format="json")
            enqueue.assert_not_called()
        assert response.status_code == 200
        seller.refresh_from_db()
        assert seller.is_email_verified and seller.is_active
        enqueue.assert_called_once_with(
            seller_name=seller.username,
            seller_email=seller.email,
        )
        with django_capture_on_commit_callbacks(execute=True):
            repeated = api_client.post(url, payload, format="json")
        assert repeated.status_code == (200 if confirmation == "manager" else 400)
        assert enqueue.call_count == 1


@pytest.mark.parametrize("invalid", ["wrong", "expired"])
def test_invalid_code_does_not_notify(
    user_factory, invalid, django_capture_on_commit_callbacks
):
    seller = user_factory(is_email_verified=False, is_active=False)
    code = issue_email_verification_code(user=seller)
    if invalid == "expired":
        seller.email_verification_expires_at = timezone.now() - timedelta(seconds=1)
        seller.save(update_fields=("email_verification_expires_at",))
    else:
        code = "000000"
    with (
        patch("apps.accounts.services.enqueue_seller_registered_email") as enqueue,
        django_capture_on_commit_callbacks(execute=True),
        pytest.raises(ValidationError),
    ):
        verify_email_code(email=seller.email, code=code)
    enqueue.assert_not_called()


def test_manager_account_verification_does_not_notify(
    user_factory, django_capture_on_commit_callbacks
):
    user = user_factory(
        role=User.Role.MANAGER, is_email_verified=False, is_active=False
    )
    code = issue_email_verification_code(user=user)
    with (
        patch("apps.accounts.services.enqueue_seller_registered_email") as enqueue,
        django_capture_on_commit_callbacks(execute=True),
    ):
        verify_email_code(email=user.email, code=code)
    enqueue.assert_not_called()


def test_rolled_back_verification_does_not_notify(
    user_factory, django_capture_on_commit_callbacks
):
    seller = user_factory(is_email_verified=False, is_active=False)
    code = issue_email_verification_code(user=seller)
    with (
        patch("apps.accounts.services.enqueue_seller_registered_email") as enqueue,
        django_capture_on_commit_callbacks(execute=True),
    ):
        with pytest.raises(RuntimeError), transaction.atomic():
            verify_email_code(email=seller.email, code=code)
            raise RuntimeError("Rollback")
    enqueue.assert_not_called()
    seller.refresh_from_db()
    assert not seller.is_email_verified


def test_broker_failure_does_not_fail_verification(
    user_factory, settings, django_capture_on_commit_callbacks
):
    settings.PRODUCT_CREATE_EMAIL = "info@jvmoebel.de"
    seller = user_factory(is_email_verified=False, is_active=False)
    code = issue_email_verification_code(user=seller)
    with (
        patch(
            "apps.accounts.tasks.send_seller_registered_email.app.connection_for_write",
            side_effect=OSError("Broker unavailable"),
        ),
        django_capture_on_commit_callbacks(execute=True),
    ):
        verify_email_code(email=seller.email, code=code)
    seller.refresh_from_db()
    assert seller.is_email_verified and seller.is_active


@pytest.mark.parametrize("is_active", [True, False])
def test_manager_confirming_verified_seller_does_not_notify(
    user_factory,
    is_active,
    django_capture_on_commit_callbacks,
):
    seller = user_factory(is_email_verified=True, is_active=is_active)
    with (
        patch("apps.accounts.services.enqueue_seller_registered_email") as enqueue,
        django_capture_on_commit_callbacks(execute=True),
    ):
        manager_confirm_seller_email(seller=seller)
    enqueue.assert_not_called()
