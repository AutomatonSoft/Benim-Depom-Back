from types import SimpleNamespace
from unittest.mock import patch
from xml.etree import ElementTree as ET

from django.test import SimpleTestCase

from apps.ebay_bulk_end.domain import exact_listing_matches, item_summary
from apps.ebay_bulk_end.errors import EBayRateLimitError
from apps.ebay_bulk_end.workflows import run_scan_chunk


def _item(item_id, ean_xml, seller="seller-jv"):
    return ET.fromstring(
        f"<Item><ItemID>{item_id}</ItemID><Title>Test listing</Title>"
        f"<ListingStatus>Active</ListingStatus><Seller><UserID>{seller}</UserID></Seller>"
        f"{ean_xml}</Item>"
    )


class DocumentedEanLocationsTests(SimpleTestCase):
    def test_matches_exact_ean_item_specifics_but_not_a_partial_value(self):
        item = _item(
            "123456789012",
            "<ItemSpecifics><NameValueList><Name>EAN</Name><Value>01234565</Value></NameValueList>"
            "<NameValueList><Name>MPN</Name><Value>4006381333931</Value></NameValueList></ItemSpecifics>",
        )

        self.assertEqual(
            exact_listing_matches(item, {"01234565", "4006381333931"}),
            {"01234565": "listing"},
        )
        self.assertEqual(exact_listing_matches(item, {"0123456"}), {})

    def test_matches_ean_in_variation_product_details_and_variation_specifics(self):
        item = _item(
            "123456789012",
            "<Variations><Variation><SKU>SIZE-M</SKU>"
            "<VariationProductListingDetails><EAN>01234565</EAN></VariationProductListingDetails>"
            "<VariationSpecifics><NameValueList><Name>Color</Name><Value>Black</Value></NameValueList>"
            "<NameValueList><Name>Size</Name><Value>M</Value></NameValueList>"
            "<NameValueList><Name>EAN</Name><Value>4006381333931</Value></NameValueList></VariationSpecifics>"
            "</Variation></Variations>",
        )

        summary = item_summary(item, {"01234565", "4006381333931"})

        self.assertEqual(
            summary["matches"],
            [
                {
                    "ean": "01234565",
                    "type": "variation",
                    "variation": "Color: Black, Size: M",
                },
                {
                    "ean": "4006381333931",
                    "type": "variation",
                    "variation": "Color: Black, Size: M",
                },
            ],
        )
        self.assertTrue(summary["risky"])

    def test_matches_item_details_then_verifies_the_active_listing_snapshot(self):
        job = {
            "id": "scan-1",
            "manager_id": 7,
            "account": "jv",
            "environment": "sandbox",
            "kind": "scan",
            "status": "queued",
            "eans": ["01234565", "4006381333931"],
            "matches": [],
            "progress": {"current": 0, "total": None},
        }
        listed_item = ET.fromstring("<Item><ItemID>123456789012</ItemID></Item>")
        details = _item(
            "123456789012",
            "<ProductListingDetails><EAN>01234565</EAN></ProductListingDetails>"
            "<ItemSpecifics><NameValueList><Name>EAN</Name><Value>4006381333931</Value></NameValueList></ItemSpecifics>",
        )

        with (
            patch("apps.ebay_bulk_end.workflows.load_job", return_value=job),
            patch("apps.ebay_bulk_end.workflows.save_job"),
            patch(
                "apps.ebay_bulk_end.workflows.access_context",
                return_value=(SimpleNamespace(ebay_user_id="seller-jv"), "token"),
            ),
            patch(
                "apps.ebay_bulk_end.workflows.get_active_page",
                side_effect=[
                    ([listed_item], 1, 1, {}),
                    ([listed_item], 1, 1, {}),
                ],
            ),
            patch(
                "apps.ebay_bulk_end.workflows.get_item", return_value=details
            ) as get_item,
        ):
            self.assertEqual(run_scan_chunk(job["id"]), 2)
            self.assertIsNone(run_scan_chunk(job["id"]))

        self.assertEqual(job["status"], "preview")
        get_item.assert_called_once()
        self.assertEqual(len(job["matches"]), 1)
        self.assertEqual(
            {row["ean"] for row in job["matches"][0]["matches"]},
            {"01234565", "4006381333931"},
        )
        self.assertEqual(job["unmatched_eans"], [])

    def test_empty_account_produces_a_complete_preview_with_unmatched_eans(self):
        job = {
            "id": "scan-empty",
            "manager_id": 7,
            "account": "xl",
            "environment": "sandbox",
            "kind": "scan",
            "status": "queued",
            "eans": ["01234565"],
            "matches": [],
            "progress": {"current": 0, "total": None},
        }

        with (
            patch("apps.ebay_bulk_end.workflows.load_job", return_value=job),
            patch("apps.ebay_bulk_end.workflows.save_job"),
            patch(
                "apps.ebay_bulk_end.workflows.access_context",
                return_value=(SimpleNamespace(ebay_user_id="seller-xl"), "token"),
            ),
            patch(
                "apps.ebay_bulk_end.workflows.get_active_page",
                side_effect=[([], 1, 0, {}), ([], 1, 0, {})],
            ),
        ):
            self.assertEqual(run_scan_chunk(job["id"]), 2)
            self.assertIsNone(run_scan_chunk(job["id"]))

        self.assertEqual(job["status"], "preview")
        self.assertEqual(job["unmatched_eans"], ["01234565"])

    def test_scan_marks_same_count_listing_changes_incomplete(self):
        job = {
            "id": "scan-changed-items",
            "manager_id": 7,
            "account": "jv",
            "environment": "sandbox",
            "kind": "scan",
            "status": "queued",
            "eans": ["01234565"],
            "matches": [],
            "progress": {"current": 0, "total": None},
        }
        original = ET.fromstring("<Item><ItemID>123456789012</ItemID></Item>")
        replacement = ET.fromstring("<Item><ItemID>123456789013</ItemID></Item>")
        details = _item(
            "123456789012",
            "<ProductListingDetails><EAN>01234565</EAN></ProductListingDetails>",
        )

        with (
            patch("apps.ebay_bulk_end.workflows.load_job", return_value=job),
            patch("apps.ebay_bulk_end.workflows.save_job"),
            patch(
                "apps.ebay_bulk_end.workflows.access_context",
                return_value=(SimpleNamespace(ebay_user_id="seller-jv"), "token"),
            ),
            patch(
                "apps.ebay_bulk_end.workflows.get_active_page",
                side_effect=[([original], 1, 1, {}), ([replacement], 1, 1, {})],
            ),
            patch("apps.ebay_bulk_end.workflows.get_item", return_value=details),
        ):
            self.assertEqual(run_scan_chunk(job["id"]), 2)
            self.assertIsNone(run_scan_chunk(job["id"]))

        self.assertEqual(job["status"], "incomplete")
        self.assertIn("изменился", job["message"])
        self.assertNotIn("preview_expires_at", job)

    def test_scan_stops_at_the_twenty_five_thousand_listing_cap(self):
        job = {
            "id": "scan-cap",
            "manager_id": 7,
            "account": "jv",
            "environment": "sandbox",
            "kind": "scan",
            "status": "queued",
            "eans": ["01234565"],
            "matches": [],
            "progress": {"current": 0, "total": None},
        }

        with (
            patch("apps.ebay_bulk_end.workflows.load_job", return_value=job),
            patch("apps.ebay_bulk_end.workflows.save_job"),
            patch(
                "apps.ebay_bulk_end.workflows.access_context",
                return_value=(SimpleNamespace(ebay_user_id="seller-jv"), "token"),
            ),
            patch(
                "apps.ebay_bulk_end.workflows.get_active_page",
                return_value=([], 1250, 25000, {}),
            ) as get_page,
        ):
            self.assertIsNone(run_scan_chunk(job["id"]))

        self.assertEqual(job["status"], "incomplete")
        self.assertNotIn("preview_expires_at", job)
        get_page.assert_called_once()

    def test_scan_stops_above_the_safe_get_item_lookup_budget(self):
        job = {
            "id": "scan-lookup-budget",
            "manager_id": 7,
            "account": "jv",
            "environment": "sandbox",
            "kind": "scan",
            "status": "queued",
            "eans": ["01234565"],
            "matches": [],
            "progress": {"current": 0, "total": None},
        }

        with (
            patch("apps.ebay_bulk_end.workflows.load_job", return_value=job),
            patch("apps.ebay_bulk_end.workflows.save_job"),
            patch(
                "apps.ebay_bulk_end.workflows.access_context",
                return_value=(SimpleNamespace(ebay_user_id="seller-jv"), "token"),
            ),
            patch(
                "apps.ebay_bulk_end.workflows.get_active_page",
                return_value=([], 101, 2001, {}),
            ),
            patch("apps.ebay_bulk_end.workflows.get_item") as get_item,
        ):
            self.assertIsNone(run_scan_chunk(job["id"]))

        self.assertEqual(job["status"], "incomplete")
        self.assertIn("2000", job["message"])
        self.assertNotIn("preview_expires_at", job)
        get_item.assert_not_called()

    def test_scan_marks_pagination_drift_incomplete(self):
        job = {
            "id": "scan-drift",
            "manager_id": 7,
            "account": "jv",
            "environment": "sandbox",
            "kind": "scan",
            "status": "queued",
            "eans": ["01234565"],
            "matches": [],
            "progress": {"current": 0, "total": None},
        }
        item = _item(
            "123456789012",
            "<ProductListingDetails><EAN>01234565</EAN></ProductListingDetails>",
        )

        with (
            patch("apps.ebay_bulk_end.workflows.load_job", return_value=job),
            patch("apps.ebay_bulk_end.workflows.save_job"),
            patch(
                "apps.ebay_bulk_end.workflows.access_context",
                return_value=(SimpleNamespace(ebay_user_id="seller-jv"), "token"),
            ),
            patch(
                "apps.ebay_bulk_end.workflows.get_active_page",
                side_effect=[([item], 2, 201, {}), ([item], 2, 202, {})],
            ) as get_page,
            patch("apps.ebay_bulk_end.workflows.get_item", return_value=item),
        ):
            self.assertEqual(run_scan_chunk(job["id"]), 2)
            self.assertIsNone(run_scan_chunk(job["id"]))

        self.assertEqual(job["status"], "incomplete")
        self.assertNotIn("preview_expires_at", job)
        self.assertEqual(get_page.call_count, 2)

    def test_scan_marks_empty_page_with_reported_listings_incomplete(self):
        job = {
            "id": "scan-empty-page",
            "manager_id": 7,
            "account": "jv",
            "environment": "sandbox",
            "kind": "scan",
            "status": "queued",
            "eans": ["01234565"],
            "matches": [],
            "progress": {"current": 0, "total": None},
        }

        with (
            patch("apps.ebay_bulk_end.workflows.load_job", return_value=job),
            patch("apps.ebay_bulk_end.workflows.save_job"),
            patch(
                "apps.ebay_bulk_end.workflows.access_context",
                return_value=(SimpleNamespace(ebay_user_id="seller-jv"), "token"),
            ),
            patch(
                "apps.ebay_bulk_end.workflows.get_active_page",
                return_value=([], 1, 1, {}),
            ),
        ):
            run_scan_chunk(job["id"])

        self.assertEqual(job["status"], "incomplete")
        self.assertNotIn("preview_expires_at", job)

    def test_malformed_listing_ownership_makes_scan_incomplete(self):
        job = {
            "id": "scan-malformed",
            "manager_id": 7,
            "account": "jv",
            "environment": "sandbox",
            "kind": "scan",
            "status": "queued",
            "eans": ["01234565"],
            "matches": [],
            "progress": {"current": 0, "total": None},
        }
        item = _item(
            "123456789012",
            "<ProductListingDetails><EAN>01234565</EAN></ProductListingDetails>",
            seller="",
        )

        with (
            patch("apps.ebay_bulk_end.workflows.load_job", return_value=job),
            patch("apps.ebay_bulk_end.workflows.save_job"),
            patch(
                "apps.ebay_bulk_end.workflows.access_context",
                return_value=(SimpleNamespace(ebay_user_id="seller-jv"), "token"),
            ),
            patch(
                "apps.ebay_bulk_end.workflows.get_active_page",
                return_value=([item], 1, 1, {}),
            ),
            patch("apps.ebay_bulk_end.workflows.get_item", return_value=item),
        ):
            run_scan_chunk(job["id"])

        self.assertEqual(job["status"], "incomplete")
        self.assertNotIn("preview_expires_at", job)

    def test_rate_limit_retries_stop_after_the_bounded_attempts(self):
        job = {
            "id": "scan-rate-limit",
            "manager_id": 7,
            "account": "jv",
            "environment": "sandbox",
            "kind": "scan",
            "status": "queued",
            "eans": ["01234565"],
            "matches": [],
            "progress": {"current": 0, "total": None},
        }

        with (
            patch("apps.ebay_bulk_end.workflows.load_job", return_value=job),
            patch("apps.ebay_bulk_end.workflows.save_job"),
            patch(
                "apps.ebay_bulk_end.workflows.access_context",
                return_value=(SimpleNamespace(ebay_user_id="seller-jv"), "token"),
            ),
            patch(
                "apps.ebay_bulk_end.workflows.get_active_page",
                side_effect=EBayRateLimitError(2),
            ) as get_active_page,
        ):
            retry_delays = [run_scan_chunk(job["id"]) for _ in range(4)]

        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["rate_limit_retries"], 4)
        self.assertEqual(get_active_page.call_count, 4)
        self.assertEqual(retry_delays, [2, 2, 2, None])

    def test_get_item_rate_limit_retries_stop_after_the_bounded_attempts(self):
        job = {
            "id": "scan-item-rate-limit",
            "manager_id": 7,
            "account": "jv",
            "environment": "sandbox",
            "kind": "scan",
            "status": "queued",
            "eans": ["01234565"],
            "matches": [],
            "progress": {"current": 0, "total": None},
        }
        listed_item = ET.fromstring("<Item><ItemID>123456789012</ItemID></Item>")

        with (
            patch("apps.ebay_bulk_end.workflows.load_job", return_value=job),
            patch("apps.ebay_bulk_end.workflows.save_job"),
            patch(
                "apps.ebay_bulk_end.workflows.access_context",
                return_value=(SimpleNamespace(ebay_user_id="seller-jv"), "token"),
            ),
            patch(
                "apps.ebay_bulk_end.workflows.get_active_page",
                return_value=([listed_item], 1, 1, {}),
            ),
            patch(
                "apps.ebay_bulk_end.workflows.get_item",
                side_effect=EBayRateLimitError(2),
            ) as get_item,
        ):
            retry_delays = [run_scan_chunk(job["id"]) for _ in range(4)]

        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["rate_limit_retries"], 4)
        self.assertEqual(get_item.call_count, 4)
        self.assertEqual(retry_delays, [2, 2, 2, None])
