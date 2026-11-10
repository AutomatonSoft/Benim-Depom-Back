from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from xml.etree import ElementTree as ET

from django.test import SimpleTestCase
from django.utils import timezone

from apps.ebay_bulk_end.workflows import _verify_item, run_end_chunk, run_scan_chunk


class EndWorkflowTests(SimpleTestCase):
    def test_scan_stops_if_the_connected_seller_changes_between_chunks(self):
        job = {
            "id": "scan-1",
            "kind": "scan",
            "status": "queued",
            "account": "jv",
            "environment": "sandbox",
            "seller_id": "seller-from-first-page",
            "matches": [],
            "eans": ["4006381333931"],
            "progress": {"current": 1, "total": 2},
        }
        with (
            patch("apps.ebay_bulk_end.workflows.load_job", return_value=job),
            patch("apps.ebay_bulk_end.workflows.save_job"),
            patch(
                "apps.ebay_bulk_end.workflows.access_context",
                return_value=(
                    SimpleNamespace(ebay_user_id="different-seller"),
                    "token",
                ),
            ),
            patch("apps.ebay_bulk_end.workflows.get_active_page") as get_active_page,
        ):
            run_scan_chunk(job["id"])

        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["seller_id"], "seller-from-first-page")
        self.assertIn("изменился", job["message"])
        get_active_page.assert_not_called()

    def test_blocks_auctions_with_bids_in_the_last_twelve_hours(self):
        item = ET.fromstring(
            "<Item><ItemID>123456789012</ItemID><ListingStatus>Active</ListingStatus>"
            "<Seller><UserID>seller-from-preview</UserID></Seller>"
            "<ProductListingDetails><EAN>4006381333931</EAN></ProductListingDetails>"
            "<ListingType>Chinese</ListingType><SellingStatus><BidCount>2</BidCount></SellingStatus>"
            f"<ListingDetails><EndTime>{(timezone.now() + timedelta(hours=3)).isoformat()}</EndTime></ListingDetails>"
            "</Item>"
        )
        selected = {
            "item_id": "123456789012",
            "title": "",
            "listing_type": "Chinese",
            "bid_count": 2,
            "variation_count": 0,
            "matches": [{"ean": "4006381333931", "type": "listing"}],
        }
        job = {
            "seller_id": "seller-from-preview",
            "include_variations": False,
            "include_auction_risk": True,
        }

        with patch("apps.ebay_bulk_end.workflows.get_item", return_value=item):
            status, message = _verify_item("token", selected, job)

        self.assertEqual(status, "blocked")
        self.assertIn("12 часов", message)

    def test_preflight_blocks_title_and_variation_changes_after_preview(self):
        selected = {
            "item_id": "123456789012",
            "title": "Preview title",
            "listing_type": "FixedPriceItem",
            "bid_count": 0,
            "variation_count": 0,
            "matches": [{"ean": "4006381333931", "type": "listing"}],
        }
        job = {
            "seller_id": "seller-from-preview",
            "include_variations": False,
            "include_auction_risk": False,
        }
        title_changed = ET.fromstring(
            "<Item><ItemID>123456789012</ItemID><ListingStatus>Active</ListingStatus>"
            "<Seller><UserID>seller-from-preview</UserID></Seller><Title>New title</Title>"
            "<ProductListingDetails><EAN>4006381333931</EAN></ProductListingDetails>"
            "<ListingType>FixedPriceItem</ListingType><SellingStatus><BidCount>0</BidCount></SellingStatus></Item>"
        )
        variation_changed = ET.fromstring(
            "<Item><ItemID>123456789012</ItemID><ListingStatus>Active</ListingStatus>"
            "<Seller><UserID>seller-from-preview</UserID></Seller><Title>Preview title</Title>"
            "<ProductListingDetails><EAN>4006381333931</EAN></ProductListingDetails>"
            "<ListingType>FixedPriceItem</ListingType><SellingStatus><BidCount>0</BidCount></SellingStatus>"
            "<Variations><Variation><SKU>new-variation</SKU></Variation></Variations></Item>"
        )

        with patch(
            "apps.ebay_bulk_end.workflows.get_item",
            side_effect=[title_changed, variation_changed],
        ):
            status, message = _verify_item("token", selected, job)
            self.assertEqual(status, "changed")
            self.assertIn("Название", message)
            status, message = _verify_item("token", selected, job)

        self.assertEqual(status, "changed")
        self.assertIn("Количество вариантов", message)

    def test_preflight_blocks_changed_matched_variation_specifics(self):
        selected = {
            "item_id": "123456789012",
            "title": "Preview title",
            "listing_type": "FixedPriceItem",
            "bid_count": 0,
            "variation_count": 1,
            "matches": [
                {
                    "ean": "4006381333931",
                    "type": "variation",
                    "variation": "Color: Black",
                }
            ],
        }
        job = {
            "seller_id": "seller-from-preview",
            "include_variations": True,
            "include_auction_risk": False,
        }
        item = ET.fromstring(
            "<Item><ItemID>123456789012</ItemID><ListingStatus>Active</ListingStatus>"
            "<Seller><UserID>seller-from-preview</UserID></Seller><Title>Preview title</Title>"
            "<ListingType>FixedPriceItem</ListingType><SellingStatus><BidCount>0</BidCount></SellingStatus>"
            "<Variations><Variation><VariationProductListingDetails><EAN>4006381333931</EAN></VariationProductListingDetails>"
            "<VariationSpecifics><NameValueList><Name>Color</Name><Value>Red</Value></NameValueList></VariationSpecifics>"
            "</Variation></Variations></Item>"
        )

        with patch("apps.ebay_bulk_end.workflows.get_item", return_value=item):
            status, message = _verify_item("token", selected, job)

        self.assertEqual(status, "changed")
        self.assertIn("EAN", message)

    def test_refuses_to_end_items_from_a_different_connected_seller(self):
        job = {
            "id": "job-1",
            "kind": "end",
            "status": "ending",
            "account": "jv",
            "environment": "sandbox",
            "seller_id": "seller-from-preview",
            "selected": [{"item_id": "123456789012"}],
            "results": [{"item_id": "123456789012", "status": "queued"}],
            "progress": {"current": 0, "total": 1},
        }

        with (
            patch("apps.ebay_bulk_end.workflows.load_job", return_value=job),
            patch("apps.ebay_bulk_end.workflows.save_job"),
            patch(
                "apps.ebay_bulk_end.workflows.acquire_account_lock", return_value=True
            ),
            patch("apps.ebay_bulk_end.workflows.release_account_lock"),
            patch(
                "apps.ebay_bulk_end.workflows.access_context",
                return_value=(
                    SimpleNamespace(ebay_user_id="different-seller"),
                    "token",
                ),
            ),
            patch("apps.ebay_bulk_end.workflows.end_items") as end_items,
        ):
            run_end_chunk("job-1")

        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["seller_id"], "seller-from-preview")
        self.assertIn("отличается", job["message"])
        end_items.assert_not_called()

    def _run_in_sandbox(self, count, cancel_after_batch=False):
        job = {
            "id": "job-2",
            "kind": "end",
            "status": "ending",
            "account": "jv",
            "environment": "sandbox",
            "seller_id": "seller-from-preview",
            "selected": [
                {
                    "item_id": str(123456789012 + i),
                    "matches": [{"ean": "4006381333931", "type": "listing"}],
                }
                for i in range(count)
            ],
            "results": [
                {
                    "item_id": str(123456789012 + i),
                    "status": "queued",
                    "eans": ["4006381333931"],
                }
                for i in range(count)
            ],
            "progress": {"current": 0, "total": count},
        }
        outcomes = [False, False, cancel_after_batch] + [False] * 100
        with (
            patch("apps.ebay_bulk_end.workflows.load_job", return_value=job),
            patch("apps.ebay_bulk_end.workflows.save_job"),
            patch(
                "apps.ebay_bulk_end.workflows.acquire_account_lock", return_value=True
            ),
            patch("apps.ebay_bulk_end.workflows.release_account_lock"),
            patch(
                "apps.ebay_bulk_end.workflows.access_context",
                return_value=(
                    SimpleNamespace(ebay_user_id="seller-from-preview"),
                    "token",
                ),
            ),
            patch(
                "apps.ebay_bulk_end.workflows.cancellation_requested",
                side_effect=outcomes + [True] * 5,
            ),
            patch(
                "apps.ebay_bulk_end.workflows._verify_item", return_value=("ready", "")
            ),
            patch(
                "apps.ebay_bulk_end.workflows.end_items",
                side_effect=lambda token, ids: {
                    item_id: {
                        "ack": "",
                        "message": "",
                        "classification": "",
                        "severity_code": "",
                        "error_code": "",
                        "end_time": "2026-11-10T12:00:00Z",
                    }
                    for item_id in ids
                },
            ) as end_items,
            patch("apps.ebay_bulk_end.tasks.end_ebay_job.apply_async"),
        ):
            for _ in range(20):
                if job["status"] != "ending":
                    break
                run_end_chunk(job["id"])
        return job, end_items

    def test_sends_ten_items_in_one_enditems_request(self):
        job, end_items = self._run_in_sandbox(10)
        self.assertEqual(end_items.call_count, 1)
        self.assertEqual(len(end_items.call_args.args[1]), 10)
        self.assertEqual(job["status"], "complete")

    def test_splits_eleven_items_into_two_requests(self):
        job, end_items = self._run_in_sandbox(11)
        self.assertEqual(end_items.call_count, 2)
        self.assertEqual(
            [len(call.args[1]) for call in end_items.call_args_list], [10, 1]
        )
        self.assertEqual(job["status"], "complete")

    def test_large_jobs_keep_each_enditems_request_within_the_ten_item_limit(self):
        job, end_items = self._run_in_sandbox(101)
        self.assertEqual(
            [len(call.args[1]) for call in end_items.call_args_list], [10] * 10 + [1]
        )
        self.assertEqual(job["status"], "complete")

    def test_cancellation_stops_after_the_current_batch(self):
        job, end_items = self._run_in_sandbox(11, cancel_after_batch=True)
        self.assertEqual(end_items.call_count, 1)
        self.assertEqual(job["status"], "cancelled")
        self.assertEqual(job["results"][-1]["status"], "cancelled")
