from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.ebay_bulk_end.domain import MAX_EANS
from apps.ebay_bulk_end.views import ConnectionsView, ScanView


class ManagerOnlyApiTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    def test_connections_require_authentication(self):
        response = ConnectionsView.as_view()(self.factory.get("/connections/"))
        self.assertNotEqual(response.status_code, 200)

    def test_seller_cannot_read_connections_or_create_a_scan(self):
        seller = SimpleNamespace(
            pk=9,
            is_authenticated=True,
            is_superuser=False,
            role="seller",
        )
        get_request = self.factory.get("/connections/")
        post_request = self.factory.post(
            "/scans/",
            {"account": "jv", "eans": ["01234565"]},
            format="json",
        )
        force_authenticate(get_request, user=seller)
        force_authenticate(post_request, user=seller)

        with patch("apps.ebay_bulk_end.views.connection_status") as connection_status:
            get_response = ConnectionsView.as_view()(get_request)
            post_response = ScanView.as_view()(post_request)

        self.assertEqual(get_response.status_code, 403)
        self.assertEqual(post_response.status_code, 403)
        connection_status.assert_not_called()

    def test_manager_can_read_connection_state(self):
        manager = SimpleNamespace(
            pk=7,
            is_authenticated=True,
            is_superuser=False,
            role="manager",
        )
        request = self.factory.get("/connections/")
        force_authenticate(request, user=manager)

        with patch(
            "apps.ebay_bulk_end.views.connection_status",
            return_value={"environment": "sandbox", "accounts": []},
        ):
            response = ConnectionsView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["accounts"], [])
        self.assertEqual(response.data["max_eans_per_scan"], MAX_EANS)

    def test_scan_stops_when_selected_account_has_no_oauth_connection(self):
        manager = SimpleNamespace(
            pk=7,
            is_authenticated=True,
            is_superuser=False,
            role="manager",
        )
        request = self.factory.post(
            "/scans/",
            {"account": "jv", "eans": ["01234565"]},
            format="json",
        )
        force_authenticate(request, user=manager)

        with (
            override_settings(EBAY_ENVIRONMENT="sandbox"),
            patch(
                "apps.common.throttles.ManagerMutationRateThrottle.allow_request",
                return_value=True,
            ),
            patch("apps.ebay_bulk_end.views.connection_for", return_value=None),
            patch("apps.ebay_bulk_end.views.start_scan") as start_scan,
        ):
            response = ScanView.as_view()(request)

        self.assertEqual(response.status_code, 409)
        self.assertIn("Подключите", response.data["detail"])
        start_scan.assert_not_called()
