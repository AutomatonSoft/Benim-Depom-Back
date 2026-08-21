from types import SimpleNamespace

import pytest
from django.core.cache import cache
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.common.throttles import (
    AiGenerationRateThrottle,
    ManagerMutationRateThrottle,
    ManagerMutationThrottleMixin,
)


class ManagerMutationTestView(ManagerMutationThrottleMixin, APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"ok": True})

    def post(self, request):
        return Response({"ok": True})


class AiGenerationTestView(ManagerMutationThrottleMixin, APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [AiGenerationRateThrottle]

    def post(self, request):
        return Response({"ok": True})


@pytest.fixture(autouse=True)
def configure_test_throttle_rates(monkeypatch):
    cache.clear()
    monkeypatch.setattr(ManagerMutationRateThrottle, "rate", "2/min", raising=False)
    monkeypatch.setattr(AiGenerationRateThrottle, "rate", "1/min", raising=False)
    yield
    cache.clear()


def make_user(*, user_id: int, role: str):
    return SimpleNamespace(
        pk=user_id,
        is_authenticated=True,
        is_superuser=False,
        is_staff=False,
        role=role,
    )


def perform_request(*, view, method: str, user):
    request = getattr(APIRequestFactory(), method.lower())("/test/")
    force_authenticate(request, user=user)
    return view(request)


@pytest.mark.unit
def test_manager_write_is_limited_once_not_twice():
    view = ManagerMutationTestView.as_view()
    manager = make_user(user_id=1001, role=User.Role.MANAGER)

    assert perform_request(view=view, method="POST", user=manager).status_code == 200
    assert perform_request(view=view, method="POST", user=manager).status_code == 200
    assert perform_request(view=view, method="POST", user=manager).status_code == 429


@pytest.mark.unit
def test_seller_writes_and_manager_reads_are_not_manager_throttled():
    view = ManagerMutationTestView.as_view()
    seller = make_user(user_id=1002, role=User.Role.SELLER)
    manager = make_user(user_id=1003, role=User.Role.MANAGER)

    for _ in range(4):
        assert perform_request(view=view, method="POST", user=seller).status_code == 200
        assert perform_request(view=view, method="GET", user=manager).status_code == 200


@pytest.mark.unit
def test_ai_endpoint_has_its_own_stricter_limit():
    view = AiGenerationTestView.as_view()
    manager = make_user(user_id=1004, role=User.Role.MANAGER)

    assert perform_request(view=view, method="POST", user=manager).status_code == 200
    assert perform_request(view=view, method="POST", user=manager).status_code == 429


@pytest.mark.unit
def test_manager_limit_allows_request_after_window_expired(monkeypatch):
    current_time = [1_000.0]
    monkeypatch.setattr(
        ManagerMutationRateThrottle,
        "timer",
        staticmethod(lambda: current_time[0]),
    )

    view = ManagerMutationTestView.as_view()
    manager = make_user(user_id=1005, role=User.Role.MANAGER)

    assert perform_request(view=view, method="POST", user=manager).status_code == 200
    assert perform_request(view=view, method="POST", user=manager).status_code == 200
    assert perform_request(view=view, method="POST", user=manager).status_code == 429

    current_time[0] += 61

    assert perform_request(view=view, method="POST", user=manager).status_code == 200
