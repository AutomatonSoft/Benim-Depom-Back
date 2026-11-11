from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from django.http import Http404
from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.ebay_bulk_end.views import CancelView, ConfirmView, _owned_job


class ConfirmViewTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.job_id = uuid4()
        self.job = {
            "id": str(self.job_id),
            "manager_id": 7,
            "kind": "scan",
            "status": "preview",
            "account": "jv",
            "environment": "sandbox",
            "preview_expires_at": (timezone.now() + timedelta(minutes=5)).isoformat(),
            "matches": [
                {
                    "item_id": "123456789012",
                    "title": "Exact match",
                    "matches": [{"ean": "4006381333931", "type": "listing"}],
                    "risky": False,
                }
            ],
        }

    def post(self, payload, job=None):
        lock = Mock()
        lock.acquire.return_value = True
        redis = Mock()
        redis.lock.return_value = lock
        request = self.factory.post("/", payload, format="json")
        force_authenticate(
            request,
            user=SimpleNamespace(
                pk=7,
                is_authenticated=True,
                is_superuser=False,
                role="manager",
            ),
        )
        with (
            override_settings(EBAY_ENVIRONMENT="sandbox"),
            patch("apps.ebay_bulk_end.views.redis_client", return_value=redis),
            patch("apps.ebay_bulk_end.views._owned_job", return_value=job or self.job),
            patch(
                "apps.common.throttles.ManagerMutationRateThrottle.allow_request",
                return_value=True,
            ),
            patch("apps.ebay_bulk_end.views.confirm_job") as confirm,
        ):
            response = ConfirmView.as_view()(request, job_id=self.job_id)
        return response, confirm, lock

    def test_rejects_item_id_outside_the_immutable_preview(self):
        response, confirm, lock = self.post(
            {
                "item_ids": ["999999999999"],
                "typed_account": "JV",
                "confirm_unavailable_reason": True,
            }
        )
        self.assertEqual(response.status_code, 409)
        confirm.assert_not_called()
        lock.release.assert_called_once()

    def test_rejects_duplicate_item_ids(self):
        response, confirm, _ = self.post(
            {
                "item_ids": ["123456789012", "123456789012"],
                "typed_account": "JV",
                "confirm_unavailable_reason": True,
            }
        )
        self.assertEqual(response.status_code, 400)
        confirm.assert_not_called()

    def test_rejects_expired_preview(self):
        expired = {
            **self.job,
            "preview_expires_at": (timezone.now() - timedelta(seconds=1)).isoformat(),
        }
        response, confirm, _ = self.post(
            {
                "item_ids": ["123456789012"],
                "typed_account": "JV",
                "confirm_unavailable_reason": True,
            },
            expired,
        )
        self.assertEqual(response.status_code, 410)
        confirm.assert_not_called()

    def test_rejects_wrong_typed_account(self):
        response, confirm, _ = self.post(
            {
                "item_ids": ["123456789012"],
                "typed_account": "XL",
                "confirm_unavailable_reason": True,
            }
        )
        self.assertEqual(response.status_code, 400)
        confirm.assert_not_called()

    def test_variation_risk_requires_separate_opt_in(self):
        risky = {**self.job, "matches": [{**self.job["matches"][0], "risky": True}]}
        response, confirm, _ = self.post(
            {
                "item_ids": ["123456789012"],
                "typed_account": "JV",
                "confirm_unavailable_reason": True,
            },
            risky,
        )
        self.assertEqual(response.status_code, 400)
        confirm.assert_not_called()

    def test_accepts_only_previewed_ids_after_both_confirmations(self):
        response, confirm, _ = self.post(
            {
                "item_ids": ["123456789012"],
                "typed_account": "JV",
                "include_variations": False,
                "confirm_unavailable_reason": True,
            }
        )
        self.assertEqual(response.status_code, 202)
        confirm.assert_called_once()

    def test_accepts_depotum_as_the_typed_account(self):
        depotum_job = {**self.job, "account": "depotum"}
        response, confirm, _ = self.post(
            {
                "item_ids": ["123456789012"],
                "typed_account": "DEPOTUM",
                "confirm_unavailable_reason": True,
            },
            depotum_job,
        )

        self.assertEqual(response.status_code, 202)
        confirm.assert_called_once()

    def test_requires_confirmation_of_the_not_available_ending_reason(self):
        response, confirm, _ = self.post(
            {
                "item_ids": ["123456789012"],
                "typed_account": "JV",
                "confirm_unavailable_reason": False,
            }
        )
        self.assertEqual(response.status_code, 400)
        confirm.assert_not_called()

    def test_auction_risk_requires_separate_confirmation(self):
        auction = {
            **self.job,
            "matches": [{**self.job["matches"][0], "auction_risky": True}],
        }
        response, confirm, _ = self.post(
            {
                "item_ids": ["123456789012"],
                "typed_account": "JV",
                "include_auction_risk": False,
                "confirm_unavailable_reason": True,
            },
            auction,
        )
        self.assertEqual(response.status_code, 400)
        confirm.assert_not_called()


class JobOwnershipTests(SimpleTestCase):
    @patch("apps.ebay_bulk_end.views.load_job", return_value={"manager_id": 8})
    def test_job_is_not_visible_to_another_manager(self, load):
        with self.assertRaises(Http404):
            _owned_job(SimpleNamespace(user=SimpleNamespace(pk=7)), uuid4())
        load.assert_called_once()


class CancelViewTests(SimpleTestCase):
    def post(self, job):
        factory = APIRequestFactory()
        request = factory.post("/", {}, format="json")
        force_authenticate(
            request,
            user=SimpleNamespace(
                pk=7, is_authenticated=True, is_superuser=False, role="manager"
            ),
        )
        with (
            patch("apps.ebay_bulk_end.views._owned_job", return_value=job),
            patch("apps.ebay_bulk_end.views.request_cancellation") as cancel,
            patch(
                "apps.common.throttles.ManagerMutationRateThrottle.allow_request",
                return_value=True,
            ),
        ):
            response = CancelView.as_view()(request, job_id=uuid4())
        return response, cancel

    def test_requests_cancellation_for_active_end_job(self):
        response, cancel = self.post({"id": "job-1", "kind": "end", "status": "ending"})
        self.assertEqual(response.status_code, 202)
        cancel.assert_called_once()

    def test_does_not_cancel_a_finished_job(self):
        response, cancel = self.post(
            {"id": "job-1", "kind": "end", "status": "complete"}
        )
        self.assertEqual(response.status_code, 409)
        cancel.assert_not_called()
