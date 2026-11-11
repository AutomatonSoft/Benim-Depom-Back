import io
from datetime import timedelta
from pathlib import Path
from tempfile import SpooledTemporaryFile, TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
from xml.etree import ElementTree as ET

from django.db import DatabaseError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.ebay_bulk_end.domain import MAX_MATCHES, exact_listing_matches, item_summary
from apps.ebay_bulk_end.errors import BulkEndError, EBayTransportError
from apps.ebay_bulk_end.feed import iter_inventory_chunk
from apps.ebay_bulk_end.inventory import (
    IMPORT_BATCH_SIZE,
    IMPORT_ROWS_PER_TASK,
    REPORT_DOWNLOADS_PER_24_HOURS,
    REPORT_POLL_SECONDS,
    _claim_snapshot,
    _fail_snapshot,
    _heartbeat_snapshot,
    _reserve_report_download,
    advance_inventory_snapshot_step,
    run_inventory_scan_chunk,
    snapshot_for_scan,
)
from apps.ebay_bulk_end.maintenance import cleanup_snapshot_batch
from apps.ebay_bulk_end.models import (
    EbayBulkEndEvent,
    EbayBulkEndJob,
    EbayInventoryListing,
    EbayInventorySnapshot,
)
from apps.marketplace.ebay.models import EbayConnection


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
        self.assertEqual(
            exact_listing_matches(item, {"01234565", "4006381333931"}),
            {"01234565": "listing"},
        )
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

    def test_chunked_parser_reads_report_without_marking_a_snapshot_complete(self):
        report = io.BytesIO(
            b"SKU,ItemID,Channel\n001234565,10001,EBAY_DE\n001234565,10002,EBAY_DE\n"
        )

        rows, _cookie, eof = iter_inventory_chunk(report, "", IMPORT_ROWS_PER_TASK)

        self.assertEqual([row["item_id"] for row in rows], ["10001", "10002"])
        self.assertTrue(eof)

    def test_invalid_report_row_is_rejected_before_a_chunk_is_returned(self):
        report = io.BytesIO(b"SKU,ItemID,Channel\n001234565,not-an-item,EBAY_DE\n")

        with self.assertRaises(BulkEndError):
            iter_inventory_chunk(report, "", IMPORT_ROWS_PER_TASK)

    def test_parses_100k_500k_and_over_a_million_rows_in_bounded_chunks(self):
        for count in (100_000, 500_000, 1_000_001):
            with (
                self.subTest(count=count),
                SpooledTemporaryFile(max_size=1024 * 1024) as report,
            ):
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
                cookie = ""
                rows_read = 0
                max_chunk = 0
                eof = False
                while not eof:
                    report.seek(0)
                    rows, cookie, eof = iter_inventory_chunk(
                        report, cookie, IMPORT_ROWS_PER_TASK
                    )
                    max_chunk = max(max_chunk, len(rows))
                    rows_read += len(rows)

                self.assertEqual(rows_read, count)
                self.assertLessEqual(max_chunk, IMPORT_ROWS_PER_TASK)

    def _importing_snapshot(self, report_path):
        content = (
            b"SKU,ItemID,Channel\n001234565,10001,EBAY_DE\n001234566,10002,EBAY_DE\n"
        )
        report_path.write_bytes(content)
        stat = report_path.stat()
        return EbayInventorySnapshot.objects.create(
            account="jv",
            environment="sandbox",
            ebay_seller_id="seller-jv",
            marketplace_id="EBAY_DE",
            status="importing",
            report_path="ebay-bulk-end/test.csv",
            report_size=len(content),
            report_mtime_ns=stat.st_mtime_ns,
        )

    def test_chunk_import_checkpoints_before_snapshot_becomes_complete(self):
        with TemporaryDirectory() as directory:
            report_path = Path(directory) / "report.csv"
            snapshot = self._importing_snapshot(report_path)
            connection = SimpleNamespace(ebay_user_id="seller-jv")
            with (
                patch("apps.ebay_bulk_end.inventory.record_event"),
                patch(
                    "apps.ebay_bulk_end.inventory.access_context",
                    return_value=(connection, "token"),
                ),
                patch(
                    "apps.ebay_bulk_end.inventory.resolve_report_path",
                    return_value=report_path,
                ),
                patch("apps.ebay_bulk_end.inventory.delete_report_path"),
            ):
                self.assertEqual(
                    advance_inventory_snapshot_step(snapshot.pk, uuid4()), 1
                )
                snapshot.refresh_from_db()
                self.assertEqual(snapshot.status, "verifying")
                self.assertEqual(snapshot.checkpoint_row, 2)
                self.assertEqual(snapshot.listings.count(), 2)
                self.assertFalse(snapshot.complete)

                self.assertIsNone(advance_inventory_snapshot_step(snapshot.pk, uuid4()))

            snapshot.refresh_from_db()
            self.assertEqual(snapshot.status, "complete")
            self.assertTrue(snapshot.complete)
            self.assertEqual(snapshot.record_count, 2)

    def test_failed_database_chunk_keeps_checkpoint_and_rows_atomic_for_retry(self):
        with TemporaryDirectory() as directory:
            report_path = Path(directory) / "report.csv"
            snapshot = self._importing_snapshot(report_path)
            connection = SimpleNamespace(ebay_user_id="seller-jv")
            patchers = (
                patch("apps.ebay_bulk_end.inventory.record_event"),
                patch(
                    "apps.ebay_bulk_end.inventory.access_context",
                    return_value=(connection, "token"),
                ),
                patch(
                    "apps.ebay_bulk_end.inventory.resolve_report_path",
                    return_value=report_path,
                ),
            )
            with patchers[0], patchers[1], patchers[2]:
                with patch.object(
                    EbayInventoryListing.objects,
                    "bulk_create",
                    side_effect=DatabaseError("db lost"),
                ):
                    with self.assertRaises(DatabaseError):
                        advance_inventory_snapshot_step(snapshot.pk, uuid4())
                snapshot.refresh_from_db()
                self.assertEqual(snapshot.checkpoint_row, 0)
                self.assertEqual(snapshot.listings.count(), 0)

                self.assertEqual(
                    advance_inventory_snapshot_step(snapshot.pk, uuid4()), 1
                )

            snapshot.refresh_from_db()
            self.assertEqual(snapshot.checkpoint_row, 2)
            self.assertEqual(snapshot.listings.count(), 2)

    def test_expired_owner_can_be_reclaimed_and_stale_owner_is_fenced(self):
        stale_token = uuid4()
        snapshot = EbayInventorySnapshot.objects.create(
            account="jv",
            environment="sandbox",
            ebay_seller_id="seller-jv",
            marketplace_id="EBAY_DE",
            status="importing",
            owner_token=stale_token,
            ownership_generation=3,
            owner_expires_at=timezone.now() - timedelta(seconds=1),
        )
        _snapshot, token, generation, _previous_owner = _claim_snapshot(
            snapshot.pk, uuid4()
        )
        snapshot.refresh_from_db()
        self.assertEqual(generation, 4)
        self.assertEqual(snapshot.ownership_generation, 4)
        _snapshot, competing_token, _competing_generation, expiry = _claim_snapshot(
            snapshot.pk, uuid4()
        )
        self.assertIsNone(competing_token)
        self.assertEqual(expiry, snapshot.owner_expires_at)
        self.assertFalse(_heartbeat_snapshot(snapshot.pk, stale_token, 3))
        self.assertTrue(_heartbeat_snapshot(snapshot.pk, token, generation))
        snapshot.owner_expires_at = timezone.now() - timedelta(seconds=1)
        snapshot.save(update_fields=("owner_expires_at",))
        self.assertFalse(_fail_snapshot(snapshot.pk, token, generation, "stale worker"))
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.status, "importing")

    def test_uncertain_creation_is_logged_and_saved_without_repeating_post(self):
        snapshot = EbayInventorySnapshot.objects.create(
            account="jv",
            environment="sandbox",
            ebay_seller_id="seller-jv",
            marketplace_id="EBAY_DE",
            status="requested",
        )
        job_id = uuid4()
        error = EBayTransportError(
            "eBay did not confirm the report.",
            diagnostics={
                "operation": "create_inventory_task",
                "http_status": 500,
                "reason": "http_error",
                "ebay_error_ids": [160001],
            },
        )
        with (
            patch("apps.ebay_bulk_end.inventory.record_event") as audit,
            patch(
                "apps.ebay_bulk_end.inventory.access_context",
                return_value=(
                    SimpleNamespace(ebay_user_id="seller-jv"),
                    "secret-token",
                ),
            ),
            patch(
                "apps.ebay_bulk_end.inventory.create_inventory_task", side_effect=error
            ) as create,
            self.assertLogs("apps.ebay_bulk_end.inventory", level="WARNING") as logs,
        ):
            self.assertEqual(
                advance_inventory_snapshot_step(snapshot.pk, job_id),
                REPORT_POLL_SECONDS,
            )
        create.assert_called_once()
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.status, "requesting")
        self.assertEqual(snapshot.error_message, str(error))
        self.assertFalse(snapshot.task_id)
        event = next(
            call.args
            for call in audit.call_args_list
            if call.args[1] == "snapshot_request_uncertain"
        )
        self.assertEqual(event[2]["diagnostics"]["http_status"], 500)
        self.assertIn(str(job_id), logs.output[0])
        self.assertIn(str(snapshot.pk), logs.output[0])
        self.assertNotIn("secret-token", str(logs.output) + str(event))

    def test_scan_reports_each_snapshot_stage_without_claiming_unconfirmed_processing(
        self,
    ):
        snapshot = self._snapshot()
        snapshot.complete = False
        snapshot.report_request_started_at = timezone.now()
        stages = {
            "requested": "requesting_report",
            "requesting": "recovering_report_request",
            "queued": "waiting_for_report",
            "in_progress": "waiting_for_report",
            "downloading": "downloading_report",
            "importing": "importing_report",
            "verifying": "verifying_report",
        }
        with (
            patch(
                "apps.ebay_bulk_end.inventory.access_context",
                return_value=(SimpleNamespace(ebay_user_id="seller-jv"), "token"),
            ),
            patch("apps.ebay_bulk_end.inventory.schedule_snapshot_step"),
            patch("apps.ebay_bulk_end.inventory.save_job"),
        ):
            for status, stage in stages.items():
                with self.subTest(status=status):
                    snapshot.status = status
                    snapshot.task_id = (
                        "confirmed-task"
                        if status not in {"requested", "requesting"}
                        else ""
                    )
                    snapshot.save()
                    job = {
                        "id": str(uuid4()),
                        "account": "jv",
                        "environment": "sandbox",
                        "kind": "scan",
                        "status": "queued",
                        "eans": [],
                        "inventory_snapshot_id": str(snapshot.pk),
                        "discovery_details": {},
                    }
                    self.assertEqual(run_inventory_scan_chunk(job), REPORT_POLL_SECONDS)
                    self.assertEqual(job["progress"]["stage"], stage)
                    self.assertIsNone(job["progress"]["total"])
                    self.assertEqual(
                        job["discovery_details"]["snapshot_status"], status
                    )
                    if status == "requesting":
                        self.assertIn("not confirmed", job["message"])
                        self.assertIsNotNone(
                            job["discovery_details"]["report_recovery_deadline"]
                        )

            snapshot.status = "in_progress"
            snapshot.error_message = "Temporary polling error."
            snapshot.save()
            run_inventory_scan_chunk(job)
            self.assertEqual(job["progress"]["stage"], "retrying_report")

            snapshot.status = "incomplete"
            snapshot.task_id = ""
            snapshot.save()
            self.assertIsNone(run_inventory_scan_chunk(job))
            self.assertEqual(job["status"], "incomplete")
            self.assertTrue(job["discovery_details"]["report_request_unconfirmed"])

    def test_uncertain_report_request_recovers_the_unique_unfiltered_task(self):
        started_at = timezone.now() - timedelta(seconds=30)
        snapshot = EbayInventorySnapshot.objects.create(
            account="jv",
            environment="sandbox",
            ebay_seller_id="seller-jv",
            marketplace_id="EBAY_DE",
            status="requesting",
            report_request_started_at=started_at,
        )
        task = {
            "taskId": "recovered-task",
            "feedType": "LMS_ACTIVE_INVENTORY_REPORT",
            "creationDate": timezone.now().isoformat(),
        }
        connection = SimpleNamespace(ebay_user_id="seller-jv")
        with (
            patch("apps.ebay_bulk_end.inventory.record_event"),
            patch(
                "apps.ebay_bulk_end.inventory.access_context",
                return_value=(connection, "token"),
            ),
            patch(
                "apps.ebay_bulk_end.inventory.get_inventory_tasks",
                return_value=([task], 1),
            ),
            patch("apps.ebay_bulk_end.inventory.get_inventory_task") as get_task,
        ):
            self.assertEqual(advance_inventory_snapshot_step(snapshot.pk, uuid4()), 1)

        snapshot.refresh_from_db()
        self.assertEqual(snapshot.status, "queued")
        self.assertEqual(snapshot.task_id, "recovered-task")
        get_task.assert_not_called()

    def test_uncertain_report_request_with_multiple_candidates_fails_closed(self):
        snapshot = EbayInventorySnapshot.objects.create(
            account="jv",
            environment="sandbox",
            ebay_seller_id="seller-jv",
            marketplace_id="EBAY_DE",
            status="requesting",
            report_request_started_at=timezone.now() - timedelta(seconds=30),
        )
        connection = SimpleNamespace(ebay_user_id="seller-jv")
        tasks = [
            {
                "taskId": "task-1",
                "feedType": "LMS_ACTIVE_INVENTORY_REPORT",
                "creationDate": timezone.now().isoformat(),
            },
            {
                "taskId": "task-2",
                "feedType": "LMS_ACTIVE_INVENTORY_REPORT",
                "creationDate": timezone.now().isoformat(),
            },
        ]
        with (
            patch("apps.ebay_bulk_end.inventory.record_event"),
            patch(
                "apps.ebay_bulk_end.inventory.access_context",
                return_value=(connection, "token"),
            ),
            patch(
                "apps.ebay_bulk_end.inventory.get_inventory_tasks",
                return_value=(tasks, 2),
            ),
            patch("apps.ebay_bulk_end.inventory.create_inventory_task") as create_task,
        ):
            self.assertIsNone(advance_inventory_snapshot_step(snapshot.pk, uuid4()))

        snapshot.refresh_from_db()
        self.assertEqual(snapshot.status, "incomplete")
        self.assertFalse(snapshot.complete)
        self.assertEqual(snapshot.task_id, "")
        create_task.assert_not_called()

    def test_uncertain_report_request_times_out_without_resubmitting(self):
        snapshot = EbayInventorySnapshot.objects.create(
            account="jv",
            environment="sandbox",
            ebay_seller_id="seller-jv",
            marketplace_id="EBAY_DE",
            status="requesting",
            report_request_started_at=timezone.now() - timedelta(minutes=11),
        )
        connection = SimpleNamespace(ebay_user_id="seller-jv")
        with (
            patch("apps.ebay_bulk_end.inventory.record_event"),
            patch(
                "apps.ebay_bulk_end.inventory.access_context",
                return_value=(connection, "token"),
            ),
            patch("apps.ebay_bulk_end.inventory.get_inventory_tasks") as list_tasks,
            patch("apps.ebay_bulk_end.inventory.create_inventory_task") as create_task,
        ):
            self.assertIsNone(advance_inventory_snapshot_step(snapshot.pk, uuid4()))

        snapshot.refresh_from_db()
        self.assertEqual(snapshot.status, "incomplete")
        list_tasks.assert_not_called()
        create_task.assert_not_called()

    def test_100k_inventory_looks_up_only_matching_skus_and_reports_unmatched_eans(
        self,
    ):
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
            str(100_000_000_000 + index): requested[index] for index in range(80)
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
        self.assertEqual(
            job["discovery_details"]["candidate_item_count"], MAX_MATCHES + 1
        )
        get_item.assert_not_called()
        candidate_rows.assert_not_called()

    def test_slow_large_report_waits_longer_with_bounded_poll_backoff(self):
        snapshot = self._snapshot()
        snapshot.complete = False
        snapshot.status = "in_progress"
        snapshot.task_id = "slow-report"
        snapshot.error_message = "Temporary polling error."
        snapshot.save(update_fields=("complete", "status", "task_id", "error_message"))
        EbayInventorySnapshot.objects.filter(pk=snapshot.pk).update(
            created_at=timezone.now() - timedelta(hours=1)
        )

        with (
            patch(
                "apps.ebay_bulk_end.inventory.access_context",
                return_value=(SimpleNamespace(ebay_user_id="seller-jv"), "token"),
            ),
            patch(
                "apps.ebay_bulk_end.inventory.get_inventory_task",
                return_value={"status": "IN_PROCESS"},
            ),
            patch("apps.ebay_bulk_end.inventory.record_event"),
        ):
            delays = [
                advance_inventory_snapshot_step(snapshot.pk, uuid4()) for _ in range(6)
            ]

        self.assertEqual(delays, [REPORT_POLL_SECONDS, 120, 240, 300, 300, 300])
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.status, "in_progress")
        self.assertEqual(snapshot.report_poll_attempts, 6)
        self.assertEqual(snapshot.error_message, "")

    def test_expired_snapshot_and_listings_remain_for_bounded_cleanup(self):
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
        self.assertTrue(EbayInventorySnapshot.objects.filter(pk=expired.pk).exists())
        self.assertEqual(expired.listings.count(), 1)
        self.assertEqual(snapshot.listings.count(), 0)

    def test_three_scan_jobs_share_one_active_snapshot(self):
        manager = User.objects.create_user(
            username="shared-snapshot-manager",
            email="shared-snapshot-manager@example.test",
            phone="+15550000005",
            password="test-password",
            role=User.Role.MANAGER,
        )
        snapshot, created = snapshot_for_scan(
            account="jv", environment="sandbox", seller_id="seller-jv"
        )
        self.assertTrue(created)

        shared_ids = []
        for index in range(3):
            reused, created = snapshot_for_scan(
                account="jv", environment="sandbox", seller_id="seller-jv"
            )
            self.assertFalse(created)
            shared_ids.append(reused.pk)
            EbayBulkEndJob.objects.create(
                created_by=manager,
                account="jv",
                environment="sandbox",
                ebay_seller_id="seller-jv",
                kind="scan",
                status="queued",
                inventory_snapshot=reused,
                input_eans=[str(index)],
            )

        self.assertEqual(shared_ids, [snapshot.pk] * 3)
        self.assertEqual(
            EbayInventorySnapshot.objects.filter(
                account="jv",
                environment="sandbox",
                ebay_seller_id="seller-jv",
                status__in=(
                    "requested",
                    "requesting",
                    "queued",
                    "in_progress",
                    "downloading",
                    "importing",
                    "verifying",
                ),
            ).count(),
            1,
        )

    def test_report_download_quota_is_reserved_across_snapshot_history(self):
        now = timezone.now()
        connection = EbayConnection.objects.create(
            environment="sandbox",
            account="jv",
            ebay_user_id="seller-jv",
            access_token_encrypted="encrypted-access",
            refresh_token_encrypted="encrypted-refresh",
            access_token_expires_at=now + timedelta(hours=1),
            refresh_token_expires_at=now + timedelta(days=1),
        )
        token = uuid4()
        snapshot = EbayInventorySnapshot.objects.create(
            account="jv",
            environment="sandbox",
            ebay_seller_id="seller-jv",
            marketplace_id="EBAY_DE",
            status="downloading",
            owner_token=token,
            ownership_generation=1,
            owner_expires_at=now + timedelta(minutes=5),
        )
        for index in range(REPORT_DOWNLOADS_PER_24_HOURS):
            attempted_at = now - timedelta(minutes=index + 1)
            EbayInventorySnapshot.objects.create(
                account="jv",
                environment="sandbox",
                ebay_seller_id="seller-jv",
                marketplace_id="EBAY_DE",
                status="failed",
                report_download_attempted_at=[attempted_at.isoformat()],
                last_report_download_attempt_at=attempted_at,
            )

        allowed, retry_after = _reserve_report_download(
            snapshot.pk, token, 1, connection
        )

        self.assertFalse(allowed)
        self.assertGreater(retry_after, 0)
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.report_download_attempts, 0)

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

    def test_cleanup_skips_a_snapshot_referenced_by_an_active_scan(self):
        expired = self._snapshot()
        expired.expires_at = timezone.now() - timedelta(minutes=1)
        expired.save(update_fields=("expires_at",))
        manager = User.objects.create_user(
            username="active-cleanup-manager",
            email="active-cleanup-manager@example.test",
            phone="+15550000007",
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
        EbayInventoryListing.objects.create(
            snapshot=expired,
            item_id="100000000003",
            sku="4006381333931",
            marketplace_id="EBAY_DE",
        )

        with patch(
            "apps.ebay_bulk_end.maintenance.delete_snapshot_staging_files"
        ) as delete_files:
            result = cleanup_snapshot_batch(expired.pk)

        self.assertEqual(result["snapshot_id"], str(expired.pk))
        self.assertGreater(result["countdown"], 0)
        self.assertTrue(EbayInventorySnapshot.objects.filter(pk=expired.pk).exists())
        self.assertEqual(expired.listings.count(), 1)
        delete_files.assert_not_called()

    def test_cleanup_does_not_remove_files_for_an_active_snapshot(self):
        snapshot = EbayInventorySnapshot.objects.create(
            account="jv",
            environment="sandbox",
            ebay_seller_id="seller-jv",
            marketplace_id="EBAY_DE",
            status="importing",
            report_path="ebay-bulk-end/active.csv",
            owner_token=uuid4(),
            owner_expires_at=timezone.now() + timedelta(minutes=5),
        )
        with patch(
            "apps.ebay_bulk_end.maintenance.delete_snapshot_staging_files"
        ) as delete_files:
            result = cleanup_snapshot_batch(snapshot.pk)

        self.assertIsNone(result["snapshot_id"])
        delete_files.assert_not_called()

    def test_cleanup_schedules_completed_snapshot_for_its_expiry(self):
        snapshot = EbayInventorySnapshot.objects.create(
            account="jv",
            environment="sandbox",
            ebay_seller_id="seller-jv",
            marketplace_id="EBAY_DE",
            status="complete",
            complete=True,
            completed_at=timezone.now(),
            expires_at=timezone.now() + timedelta(minutes=30),
        )
        with patch(
            "apps.ebay_bulk_end.maintenance.delete_snapshot_staging_files"
        ) as delete_files:
            result = cleanup_snapshot_batch(snapshot.pk)

        self.assertEqual(result["snapshot_id"], str(snapshot.pk))
        self.assertGreater(result["countdown"], 0)
        self.assertLessEqual(result["countdown"], 30 * 60)
        delete_files.assert_not_called()

    def test_cleanup_keeps_permanent_audit_history(self):
        snapshot = self._snapshot()
        snapshot.expires_at = timezone.now() - timedelta(minutes=1)
        snapshot.save(update_fields=("expires_at",))
        manager = User.objects.create_user(
            username="cleanup-audit-manager",
            email="cleanup-audit-manager@example.test",
            phone="+15550000006",
            password="test-password",
            role=User.Role.MANAGER,
        )
        job = EbayBulkEndJob.objects.create(
            created_by=manager,
            account="jv",
            environment="sandbox",
            ebay_seller_id="seller-jv",
            kind="scan",
            status="preview",
            inventory_snapshot=snapshot,
        )
        event = EbayBulkEndEvent.objects.create(
            job=job, event_type="snapshot_import_completed"
        )
        EbayInventoryListing.objects.create(
            snapshot=snapshot,
            item_id="100000000002",
            sku="4006381333931",
            marketplace_id="EBAY_DE",
        )

        self.assertTrue(cleanup_snapshot_batch(snapshot.pk)["more"])
        cleanup_snapshot_batch(snapshot.pk)

        self.assertFalse(EbayInventorySnapshot.objects.filter(pk=snapshot.pk).exists())
        self.assertTrue(EbayBulkEndEvent.objects.filter(pk=event.pk).exists())
        job.refresh_from_db()
        self.assertIsNone(job.inventory_snapshot_id)

    def test_cleanup_retains_download_quota_history_until_the_window_ends(self):
        attempted_at = timezone.now() - timedelta(hours=2)
        snapshot = EbayInventorySnapshot.objects.create(
            account="jv",
            environment="sandbox",
            ebay_seller_id="seller-jv",
            marketplace_id="EBAY_DE",
            status="complete",
            complete=True,
            expires_at=timezone.now() - timedelta(minutes=1),
            report_download_attempted_at=[attempted_at.isoformat()],
            last_report_download_attempt_at=attempted_at,
        )

        result = cleanup_snapshot_batch(snapshot.pk)

        self.assertEqual(result["snapshot_id"], str(snapshot.pk))
        self.assertGreater(result["countdown"], 0)
        self.assertTrue(EbayInventorySnapshot.objects.filter(pk=snapshot.pk).exists())
