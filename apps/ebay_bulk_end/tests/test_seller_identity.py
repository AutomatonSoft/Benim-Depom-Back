from types import SimpleNamespace
from unittest.mock import Mock, patch
from xml.etree import ElementTree as ET

from django.test import SimpleTestCase, override_settings
from requests.exceptions import ReadTimeout

from apps.ebay_bulk_end.auth import verified_seller_username
from apps.ebay_bulk_end.domain import item_summary
from apps.ebay_bulk_end.errors import BulkEndError, EBayTransportError
from apps.ebay_bulk_end.workflows import _verify_item, run_end_chunk


@override_settings(EBAY_ENVIRONMENT="sandbox")
class SellerIdentityTests(SimpleTestCase):
    @patch("apps.ebay_bulk_end.auth.build_marketplace_session")
    def test_identity_binds_immutable_id_to_trading_username(self, build_session):
        response = Mock(ok=True)
        response.json.return_value = {
            "userId": "qn-llr8lrkm",
            "username": "testuser_testuser_benim",
        }
        get = build_session.return_value.__enter__.return_value.get
        get.return_value = response
        self.assertEqual(
            verified_seller_username("private-token", "qn-llr8lrkm"),
            "testuser_testuser_benim",
        )
        self.assertEqual(
            get.call_args.args[0],
            "https://apiz.sandbox.ebay.com/commerce/identity/v1/user/",
        )

    @patch("apps.ebay_bulk_end.auth.build_marketplace_session")
    def test_missing_or_mismatched_identity_fails_closed(self, build_session):
        response = Mock(ok=True)
        build_session.return_value.__enter__.return_value.get.return_value = response
        for payload in (
            {"userId": "someone-else", "username": "testuser_testuser_benim"},
            {"userId": "qn-llr8lrkm"},
            {"userId": "qn-llr8lrkm", "username": " "},
            [],
        ):
            with self.subTest(payload=payload), self.assertRaises(BulkEndError):
                response.json.return_value = payload
                verified_seller_username("private-token", "qn-llr8lrkm")

    @patch("apps.ebay_bulk_end.auth.build_marketplace_session")
    def test_timeout_and_http_failure_do_not_leak_tokens(self, build_session):
        get = build_session.return_value.__enter__.return_value.get
        get.side_effect = ReadTimeout("private-token")
        with self.assertRaises(EBayTransportError) as caught:
            verified_seller_username("private-token", "qn-llr8lrkm")
        self.assertNotIn("private-token", str(caught.exception))
        get.side_effect = None
        get.return_value = Mock(ok=False, status_code=403)
        with self.assertRaises(BulkEndError):
            verified_seller_username("private-token", "qn-llr8lrkm")

    def test_preflight_accepts_verified_username_and_rejects_other_sellers(self):
        item = ET.fromstring(
            "<Item><ItemID>110591477867</ItemID><Title>Product</Title><SKU>4071489789959</SKU><SellingStatus><ListingStatus>Active</ListingStatus></SellingStatus><ListingType>FixedPriceItem</ListingType><Seller><UserID>testuser_testuser_benim</UserID></Seller></Item>"
        )
        selected = item_summary(item, {"4071489789959"})
        job = {"seller_id": "qn-llr8lrkm", "seller_username": "testuser_testuser_benim"}
        with patch("apps.ebay_bulk_end.workflows.get_item", return_value=item):
            self.assertEqual(_verify_item("token", selected, job)[0], "ready")
            item.find("Seller/UserID").text = "another-seller"
            self.assertEqual(
                _verify_item("token", selected, job)[0], "ownership_mismatch"
            )
            self.assertEqual(
                _verify_item("token", selected, {"seller_id": "qn-llr8lrkm"})[0],
                "ownership_mismatch",
            )

    def test_identity_failure_blocks_before_enditems(self):
        job = {
            "id": "test-job",
            "kind": "end",
            "status": "ending",
            "account": "depotum",
            "environment": "sandbox",
            "seller_id": "qn-llr8lrkm",
            "progress": {"current": 0, "total": 1},
            "selected": [{"item_id": "110591477867"}],
            "results": [{"item_id": "110591477867", "status": "queued"}],
        }
        with (
            patch("apps.ebay_bulk_end.workflows.load_job", return_value=job),
            patch("apps.ebay_bulk_end.workflows.save_job"),
            patch(
                "apps.ebay_bulk_end.workflows.acquire_account_lock", return_value=True
            ),
            patch("apps.ebay_bulk_end.workflows.release_account_lock"),
            patch(
                "apps.ebay_bulk_end.workflows.cancellation_requested",
                return_value=False,
            ),
            patch(
                "apps.ebay_bulk_end.workflows.access_context",
                return_value=(SimpleNamespace(ebay_user_id="qn-llr8lrkm"), "token"),
            ),
            patch(
                "apps.ebay_bulk_end.workflows.verified_seller_username",
                side_effect=BulkEndError("Identity rejected"),
            ),
            patch("apps.ebay_bulk_end.workflows.end_items") as end,
        ):
            run_end_chunk(job["id"])
        self.assertEqual(job["status"], "failed")
        end.assert_not_called()
