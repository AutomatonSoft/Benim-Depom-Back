from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from apps.accounts.models import User
from apps.common.permissions import IsManager, IsSeller, is_manager
from apps.notifications.firebase import get_firebase_app


@pytest.mark.unit
@pytest.mark.parametrize(
    "authenticated, role, is_staff, expected",
    [
        (False, User.Role.MANAGER, False, False),
        (True, User.Role.SELLER, False, False),
        (True, User.Role.MANAGER, False, True),
        (True, User.Role.ADMIN, False, True),
        (True, User.Role.SELLER, True, True),
    ],
)
def test_role_permissions(authenticated, role, is_staff, expected):
    user = SimpleNamespace(is_authenticated=authenticated, role=role, is_staff=is_staff)
    request = SimpleNamespace(user=user)

    assert is_manager(user) is expected
    assert IsManager().has_permission(request, None) is expected
    assert IsSeller().has_permission(request, None) is (authenticated and role == User.Role.SELLER)


@pytest.mark.unit
@override_settings(FIREBASE_ENABLED=False)
def test_firebase_app_returns_none_when_disabled():
    get_firebase_app.cache_clear()
    assert get_firebase_app() is None
    get_firebase_app.cache_clear()


@pytest.mark.unit
@override_settings(FIREBASE_ENABLED=True, FIREBASE_SERVICE_ACCOUNT_FILE="missing.json")
def test_firebase_app_rejects_missing_service_account_file():
    get_firebase_app.cache_clear()
    with pytest.raises(ImproperlyConfigured, match="not found"):
        get_firebase_app()
    get_firebase_app.cache_clear()
