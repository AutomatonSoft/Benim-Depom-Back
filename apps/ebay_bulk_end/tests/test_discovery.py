import io
from datetime import timedelta
from tempfile import SpooledTemporaryFile
from types import SimpleNamespace
from unittest.mock import patch
from xml.etree import ElementTree as ET

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.ebay_bulk_end.domain import MAX_MATCHES, exact_listing_matches, item_summary
from apps.ebay_bulk_end.errors import BulkEndError
from apps.ebay_bulk_end.inventory import (
    IMPORT_BATCH_SIZE,
    REPORT_POLL_SECONDS,
    import_inventory_report,
    run_inventory_scan_chunk,
    snapshot_for_scan,
)
from apps.ebay_bulk_end.models import (
    EbayBulkEndJob,
    EbayInventoryListing,
    EbayInventorySnapshot,
)


def _item(item_id, sku, seller="seller-jv", extra=""):
    return ET.fromstring(
        f"<Item><ItemID>{item_id}</ItemID><Title>Test listing</Title>"
        f"<ListingStatus>Active</ListingStatus><Seller><UserID>{seller}</UserID></Seller>"
        f"<SKU>{sku}</SKU>{extra}</Item>"
    )


class ExactIdentifierTests(SimpleTestCase):
    def test_matches_exact_ean_item_specifics_but_not_a_partial_value(self):
        item = ET.fromstring(
            "<Item><ItemSpecifics><NameValueList><Name>EAN</Name><Value>01234565</Value></NameValueList>"
            "<NameValueList><Name>MPN</Name><Value>4006381333931</Value></NameValueList></ItemSpecifics></Item>"
        )
        self.assertEqual(exact_listing_matches(item, {"01234565", "4006381333931"}), {"01234565": "listing"})
        self.assertEqual(exact_listing_matches(item, {"0123456"}), {})

    def test_sku_only_variation_is_reported_with_its_real_match_type_and_label(self):
        item = _item(
            "123456789012",
            "",
            extra="<Variations><Variation><SKU>4006381333931</SKU>"
            "<VariationSpecifics><NameValueList><Name>Size</Name><Value>M</Value></NameValueList></VariationSpecifics>"
            "</Variation></Variations>",
        )

        summary = item_summary(item, {"4006381333931"})

        self.assertEqual(summary["matches"][0]["source"], "sku")
        self.assertEqual(summary["matches"][0]["match_type"], "exact_variation_sku")
        self.assertEqual(summary["matches"][0]["variation"], "Size: M")
        self.assertTrue(summary["risky"])

    def test_conflicting_populated_ean_marks_a_sku_match_unsafe(self):
        item = _item(
            "123456789012",
            "4006381333931",
            extra="<ProductListingDetails><EAN>5901234123457</EAN></ProductListingDetails>",
        )

        summary = item_summary(item, {"4006381333931"})

        self.assertTrue(summary["identifier_conflict"])


class LargeInventoryLookupTests(TestCase):
    def _snapshot(self):
        return EbayInventorySnapshot.objects.create(
            account="jv",
            environment="sandbox",
            ebay_seller_id="seller-jv",
            marketplace_id="EBAY_DE",
            status="complete",
            complete=True,
            completed_at=timezone.now(),
            expires_at=timezone.now() + timedelta(hours=1),
        )

    def test_import_marks_only_a_fully_parsed_matching_marketplace_report_complete(self):
        snapshot = self._snapshot()
        report = io.BytesIO(
            b"SKU,ItemID,Channel\n001234565,10001,EBAY_DE\n001234565,10002,EBAY_DE\n"
        )

        count = import_inventory_report(snapshot, report)

        snapshot.refresh_from_db()
        self.assertEqual(count, 2)
        self.assertTrue(snapshot.complete)
        self.assertEqual(snapshot.listings.count(), 2)
        self.assertEqual(set(snapshot.listings.values_list("sku", flat=True)), {"001234565"})

    def test_wrong_marketplace_report_is_incomplete_and_discards_partial_rows(self):
        snapshot = self._snapshot()
        report = io.BytesIO(b"SKU,ItemID,Channel\n001234565,10001,EBAY_GB\n")

        with self.assertRaises(BulkEndError):
            import_inventory_report(snapshot, report)

        snapshot.refresh_from_db()
        self.assertFalse(snapshot.complete)
        self.assertEqual(snapshot.status, "incomplete")
        self.assertEqual(snapshot.listings.count(), 0)

    def test_imports_100k_500k_and_over_a_million_rows_in_bounded_batches(self):
        batch_sizes = []
        with patch.object(
            EbayInventoryListing.objects,
            "bulk_create",
            side_effect=lambda rows, **kwargs: batch_sizes.append(len(rows)),
        ):
            for count in (100_000, 500_000, 1_000_001):
                with self.subTest(count=count), SpooledTemporaryFile(max_size=1024 * 1024) as report:
                    batch_sizes.clear()
                    report.write(b"SKU,ItemID,Channel\n")
                    for start in range(0, count, IMPORT_BATCH_SIZE):
                        end = min(start + IMPORT_BATCH_SIZE, count)
                        report.write(
                            "".join(
                                f"{number:013d},{number + 1},EBAY_DE\n"
                                for number in range(start, end)
                            ).encode("ascii")
                        )
                    report.seek(0)
                    snapshot = self._snapshot()

                    self.assertEqual(import_inventory_report(snapshot, report), count)

                    snapshot.refresh_from_db()
                    self.assertTrue(snapshot.complete)
                    self.assertEqual(snapshot.record_count, count)
                    self.assertEqual(
                        len(batch_sizes),
                        (count + IMPORT_BATCH_SIZE - 1) // IMPORT_BATCH_SIZE,
                    )
                    self.assertLessEqual(max(batch_sizes), IMPORT_BATCH_SIZE)

    def test_100k_inventory_looks_up_only_matching_skus_and_reports_unmatched_eans(self):
        snapshot = EbayInventorySnapshot.objects.create(
            account="jv",
            environment="sandbox",
            ebay_seller_id="seller-jv",
            marketplace_id="EBAY_DE",
            status="complete",
            complete=True,
            record_count=100_000,
            completed_at=timezone.now(),
            expires_at=timezone.now() + timedelta(hours=1),
        )
        requested = [f"EAN-{number:03d}" for number in range(100)]
        batch = []
        for index in range(100_000):
            if index < 80:
                sku = requested[index]
            elif index == 80:
                sku = requested[0]
            else:
                sku = f"SKU-{index}"
            batch.append(
                EbayInventoryListing(
                    snapshot=snapshot,
                    item_id=str(100_000_000_000 + index),
                    sku=sku,
                    marketplace_id="EBAY_DE",
                )
            )
            if len(batch) == 1000:
                EbayInventoryListing.objects.bulk_create(batch, batch_size=1000)
                batch.clear()
        if batch:
            EbayInventoryListing.objects.bulk_create(batch, batch_size=1000)

        candidates = {
            str(100_000_000_000 + index): requested[index]
            for index in range(80)
        }
        candidates[str(100_000_000_080)] = requested[0]
        job = {
            "id": "scan-large",
            "manager_id": 7,
            "account": "jv",
            "environment": "sandbox",
            "kind": "scan",
            "status": "queued",
            "seller_id": "seller-jv",
            "eans": requested,
            "matches": [],
            "discovery_details": {},
            "inventory_snapshot_id": str(snapshot.pk),
        }

        with (
            patch(
                "apps.ebay_bulk_end.inventory.access_context",
                return_value=(SimpleNamespace(ebay_user_id="seller-jv"), "token"),
            ),
            patch(
                "apps.ebay_bulk_end.inventory.get_item",
                side_effect=lambda token, item_id: _item(item_id, candidates[item_id]),
            ) as get_item,
            patch("apps.ebay_bulk_end.inventory.save_job"),
        ):
            while run_inventory_scan_chunk(job) is not None:
                pass

        self.assertEqual(get_item.call_count, 81)
        self.assertEqual(job["status"], "preview")
        self.assertEqual(len(job["matches"]), 81)
        self.assertEqual(set(job["unmatched_eans"]), set(requested[80:]))
        self.assertEqual(job["unmatched_status"], "not_found_in_sku_index")
        self.assertFalse(job["discovery_details"]["ean_search_complete"])

    def test_stale_report_candidate_is_not_misreported_as_absent_from_the_index(self):
        snapshot = self._snapshot()
        EbayInventoryListing.objects.create(
            snapshot=snapshot,
            item_id="100000000001",
            sku="4006381333931",
            marketplace_id="EBAY_DE",
        )
        ean = "4006381333931"
        job = {
            "id": "scan-stale",
            "manager_id": 7,
            "account": "jv",
            "environment": "sandbox",
            "kind": "scan",
            "status": "queued",
            "seller_id": "seller-jv",
            "eans": [ean],
            "matches": [],
            "discovery_details": {},
            "inventory_snapshot_id": str(snapshot.pk),
        }

        with (
            patch(
                "apps.ebay_bulk_end.inventory.access_context",
                return_value=(SimpleNamespace(ebay_user_id="seller-jv"), "token"),
            ),
            patch("apps.ebay_bulk_end.inventory.get_item", return_value=None),
            patch("apps.ebay_bulk_end.inventory.save_job"),
        ):
            self.assertIsNone(run_inventory_scan_chunk(job))

        self.assertEqual(job["status"], "preview")
        self.assertEqual(job["unmatched_status_by_ean"][ean], "verification_failed")

    def test_candidate_safety_limit_is_counted_in_sql_before_rows_are_loaded(self):
        snapshot = self._snapshot()
        ean = "4006381333931"
        for start in range(0, MAX_MATCHES + 1, 1000):
            end = min(start + 1000, MAX_MATCHES + 1)
            EbayInventoryListing.objects.bulk_create(
                [
                    EbayInventoryListing(
                        snapshot=snapshot,
                        item_id=str(100_000_000_000 + number),
                        sku=ean,
                        marketplace_id="EBAY_DE",
                    )
                    for number in range(start, end)
                ],
                batch_size=1000,
            )
        job = {
            "id": "scan-many-candidates",
            "manager_id": 7,
            "account": "jv",
            "environment": "sandbox",
            "kind": "scan",
            "status": "queued",
            "seller_id": "seller-jv",
            "eans": [ean],
            "matches": [],
            "discovery_details": {},
            "inventory_snapshot_id": str(snapshot.pk),
        }

        with (
            patch(
                "apps.ebay_bulk_end.inventory.access_context",
                return_value=(SimpleNamespace(ebay_user_id="seller-jv"), "token"),
            ),
            patch("apps.ebay_bulk_end.inventory.get_item") as get_item,
            patch("apps.ebay_bulk_end.inventory._candidate_rows") as candidate_rows,
            patch("apps.ebay_bulk_end.inventory.save_job"),
        ):
            self.assertIsNone(run_inventory_scan_chunk(job))

        self.assertEqual(job["status"], "incomplete")
        self.assertEqual(job["discovery_details"]["candidate_item_count"], MAX_MATCHES + 1)
        get_item.assert_not_called()
        candidate_rows.assert_not_called()

    def test_slow_large_report_waits_longer_with_bounded_poll_backoff(self):
        snapshot = self._snapshot()
        snapshot.complete = False
        snapshot.status = "in_progress"
        snapshot.task_id = "slow-report"
        snapshot.save(update_fields=("complete", "status", "task_id"))
        EbayInventorySnapshot.objects.filter(pk=snapshot.pk).update(
            created_at=timezone.now() - timedelta(hours=1)
        )
        job = {
            "id": "scan-slow-report",
            "account": "jv",
            "environment": "sandbox",
            "seller_id": "seller-jv",
            "kind": "scan",
            "status": "scanning",
            "eans": ["4006381333931"],
            "matches": [],
            "discovery_details": {},
            "inventory_snapshot_id": str(snapshot.pk),
        }

        with (
            patch(
                "apps.ebay_bulk_end.inventory.access_context",
                return_value=(SimpleNamespace(ebay_user_id="seller-jv"), "token"),
            ),
            patch(
                "apps.ebay_bulk_end.inventory.get_inventory_task",
                return_value={"status": "IN_PROCESS"},
            ),
            patch("apps.ebay_bulk_end.inventory.save_job"),
        ):
            delays = [run_inventory_scan_chunk(job) for _ in range(6)]

        self.assertEqual(delays, [REPORT_POLL_SECONDS, 120, 240, 300, 300, 300])
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.status, "in_progress")
        self.assertEqual(job["status"], "scanning")

    def test_expired_snapshot_and_listings_are_deleted_before_next_report(self):
        expired = self._snapshot()
        expired.expires_at = timezone.now() - timedelta(minutes=1)
        expired.save(update_fields=("expires_at",))
        EbayInventoryListing.objects.create(
            snapshot=expired,
            item_id="100000000001",
            sku="4006381333931",
            marketplace_id="EBAY_DE",
        )

        snapshot, created = snapshot_for_scan(
            account="jv",
            environment="sandbox",
            seller_id="seller-jv",
        )

        self.assertTrue(created)
        self.assertFalse(EbayInventorySnapshot.objects.filter(pk=expired.pk).exists())
        self.assertEqual(snapshot.listings.count(), 0)

    def test_expired_snapshot_is_kept_while_a_scan_still_uses_it(self):
        expired = self._snapshot()
        expired.expires_at = timezone.now() - timedelta(minutes=1)
        expired.save(update_fields=("expires_at",))
        manager = User.objects.create_user(
            username="snapshot-manager",
            email="snapshot-manager@example.test",
            phone="+15550000004",
            password="test-password",
            role=User.Role.MANAGER,
        )
        EbayBulkEndJob.objects.create(
            created_by=manager,
            account="jv",
            environment="sandbox",
            ebay_seller_id="seller-jv",
            kind="scan",
            status="scanning",
            inventory_snapshot=expired,
        )

        snapshot_for_scan(
            account="jv",
            environment="sandbox",
            seller_id="seller-jv",
        )

        self.assertTrue(EbayInventorySnapshot.objects.filter(pk=expired.pk).exists())
