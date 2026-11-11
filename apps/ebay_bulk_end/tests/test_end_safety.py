from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.ebay_bulk_end.errors import EBayTransportError
from apps.ebay_bulk_end.tasks import (
    JOB_LOCK_SECONDS,
    _job_lock,
    end_ebay_job,
    scan_ebay_eans,
)
from apps.ebay_bulk_end.workflows import _send_batch, run_end_chunk


class EndSafetyTests(SimpleTestCase):
    def test_end_task_has_time_for_preflight_and_readonly_reconciliation(self):
        self.assertEqual(end_ebay_job.soft_time_limit, 540)
        self.assertEqual(end_ebay_job.time_limit, 570)
        self.assertLess(end_ebay_job.time_limit, JOB_LOCK_SECONDS)

    def setUp(self):
        identity = patch(
            "apps.ebay_bulk_end.workflows.verified_seller_username",
            side_effect=lambda _token, user_id: user_id,
        )
        identity.start()
        self.addCleanup(identity.stop)
        patcher = patch(
            "apps.ebay_bulk_end.workflows.reserve_end_batch", return_value=True
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_concurrent_delivery_waits_until_job_lock_can_expire(self):
        task = MagicMock()
        apply_async = task.apply_async
        redis = MagicMock()
        redis.lock.return_value.acquire.return_value = False

        with (
            patch("apps.ebay_bulk_end.tasks.redis_client", return_value=redis),
        ):
            self.assertIsNone(_job_lock("job-1", task))

        redis.lock.assert_called_once_with(
            "ebay-bulk-end:v1:job-lock:job-1",
            timeout=JOB_LOCK_SECONDS,
            blocking_timeout=0,
        )
        apply_async.assert_called_once_with(
            args=["job-1"], queue="marketplace", countdown=JOB_LOCK_SECONDS + 5
        )

    def test_scan_continuation_is_queued_after_releasing_the_job_lock(self):
        events = []
        lock = MagicMock()
        lock.release.side_effect = lambda: events.append("released")

        with (
            patch("apps.ebay_bulk_end.tasks._job_lock", return_value=lock),
            patch("apps.ebay_bulk_end.tasks.run_scan_chunk", return_value=2),
            patch(
                "apps.ebay_bulk_end.tasks.scan_ebay_eans.apply_async",
                side_effect=lambda **kwargs: events.append(("queued", kwargs)),
            ),
        ):
            scan_ebay_eans.run("scan-1")

        self.assertEqual(events[0], "released")
        self.assertEqual(events[1][0], "queued")
        self.assertEqual(events[1][1]["countdown"], 2)

    def test_redis_owner_waits_for_a_worker_holding_the_postgres_fallback_lock(self):
        redis = MagicMock()
        redis.lock.return_value.acquire.return_value = True
        task = MagicMock()
        with (
            patch("apps.ebay_bulk_end.tasks.redis_client", return_value=redis),
            patch("apps.ebay_bulk_end.tasks._postgres_job_lock", return_value=None),
        ):
            self.assertIsNone(_job_lock("job-1", task))
        redis.lock.return_value.release.assert_called_once()
        task.apply_async.assert_called_once_with(
            args=["job-1"], queue="marketplace", countdown=60
        )

    def test_end_continuation_is_queued_after_releasing_the_job_lock(self):
        events = []
        lock = MagicMock()
        lock.release.side_effect = lambda: events.append("released")

        with (
            patch("apps.ebay_bulk_end.tasks._job_lock", return_value=lock),
            patch("apps.ebay_bulk_end.tasks.run_end_chunk", return_value=True),
            patch(
                "apps.ebay_bulk_end.tasks.end_ebay_job.apply_async",
                side_effect=lambda **kwargs: events.append(("queued", kwargs)),
            ),
        ):
            end_ebay_job.run("end-1")

        self.assertEqual(events[0], "released")
        self.assertEqual(events[1][0], "queued")

    def _job(self, statuses):
        selected = [
            {
                "item_id": str(123456789012 + index),
                "title": "Listing",
                "listing_type": "FixedPriceItem",
                "matches": [{"ean": "01234565", "type": "listing"}],
            }
            for index in range(len(statuses))
        ]
        return {
            "id": "end-job",
            "manager_id": 7,
            "kind": "end",
            "status": "ending",
            "account": "jv",
            "environment": "sandbox",
            "seller_id": "seller-jv",
            "selected": selected,
            "results": [
                {
                    "item_id": row["item_id"],
                    "title": row["title"],
                    "eans": ["01234565"],
                    "status": state,
                    "message": "",
                    "error_code": "",
                    "end_time": "",
                    "finished_at": "",
                }
                for row, state in zip(selected, statuses)
            ],
            "progress": {"current": 0, "total": len(selected)},
        }

    def _common_patches(self, job):
        return (
            patch("apps.ebay_bulk_end.workflows.load_job", return_value=job),
            patch("apps.ebay_bulk_end.workflows.save_job"),
            patch(
                "apps.ebay_bulk_end.workflows.acquire_account_lock", return_value=True
            ),
            patch("apps.ebay_bulk_end.workflows.release_account_lock"),
            patch(
                "apps.ebay_bulk_end.workflows.access_context",
                return_value=(SimpleNamespace(ebay_user_id="seller-jv"), "token"),
            ),
            patch(
                "apps.ebay_bulk_end.workflows.cancellation_requested",
                return_value=False,
            ),
            patch("apps.ebay_bulk_end.tasks.end_ebay_job.apply_async"),
        )

    def test_all_known_listing_failures_make_job_failed_not_complete(self):
        job = self._job(["queued"])
        common = self._common_patches(job)
        with (
            common[0],
            common[1],
            common[2],
            common[3],
            common[4],
            common[5],
            common[6],
            patch(
                "apps.ebay_bulk_end.workflows._verify_item",
                return_value=("blocked", "Not eligible"),
            ),
            patch("apps.ebay_bulk_end.workflows.end_items") as end_items,
        ):
            run_end_chunk(job["id"])

        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["results"][0]["status"], "blocked")
        end_items.assert_not_called()

    def test_account_lock_contention_finishes_item_rows_as_blocked(self):
        job = self._job(["queued"])
        with (
            patch("apps.ebay_bulk_end.workflows.load_job", return_value=job),
            patch("apps.ebay_bulk_end.workflows.save_job"),
            patch(
                "apps.ebay_bulk_end.workflows.acquire_account_lock", return_value=False
            ),
        ):
            run_end_chunk(job["id"])

        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["results"][0]["status"], "blocked")
        self.assertTrue(job["results"][0]["finished_at"])

    def test_crash_reconciliation_never_resends_an_uncertain_item(self):
        job = self._job(["sending"])
        common = self._common_patches(job)
        with (
            common[0],
            common[1],
            common[2],
            common[3],
            common[4],
            common[5],
            common[6],
            patch(
                "apps.ebay_bulk_end.workflows._reconcile",
                return_value=("uncertain", "Unknown"),
            ),
            patch("apps.ebay_bulk_end.workflows.end_items") as end_items,
            patch("apps.ebay_bulk_end.workflows._verify_item") as verify,
        ):
            run_end_chunk(job["id"])

        self.assertEqual(job["status"], "uncertain")
        end_items.assert_not_called()
        verify.assert_not_called()

    def test_redelivered_uncertain_item_is_never_sent_again(self):
        job = self._job(["uncertain"])
        job["status"] = "partial"
        common = self._common_patches(job)
        with (
            common[0],
            common[1],
            common[2],
            common[3],
            common[4],
            common[5],
            common[6],
            patch("apps.ebay_bulk_end.workflows._verify_item") as verify,
            patch("apps.ebay_bulk_end.workflows.end_items") as end_items,
        ):
            run_end_chunk(job["id"])

        self.assertEqual(job["results"][0]["status"], "uncertain")
        self.assertEqual(job["status"], "uncertain")
        verify.assert_not_called()
        end_items.assert_not_called()

    def test_transport_failure_reconciles_the_whole_bounded_batch_without_retry(self):
        ready = [{"item_id": str(123456789012 + index)} for index in range(10)]
        results = [
            {
                "item_id": row["item_id"],
                "status": "queued",
                "message": "",
                "finished_at": "",
            }
            for row in ready
        ]
        job = {"account": "jv", "updated_at": ""}

        with (
            patch("apps.ebay_bulk_end.workflows.save_job"),
            patch(
                "apps.ebay_bulk_end.workflows.end_items",
                side_effect=EBayTransportError("timeout"),
            ) as end_items,
            patch(
                "apps.ebay_bulk_end.workflows._reconcile",
                side_effect=[("ended", "Ended")] + [("uncertain", "Unknown")] * 9,
            ) as reconcile,
        ):
            _send_batch("token", ready, results, job)

        self.assertEqual(end_items.call_count, 1)
        self.assertEqual(reconcile.call_count, 10)
        self.assertEqual([row["status"] for row in results].count("ended"), 1)
        self.assertEqual([row["status"] for row in results].count("uncertain"), 9)

    def test_missing_per_item_responses_are_reconciled_for_the_whole_batch(self):
        ready = [{"item_id": str(123456789012 + index)} for index in range(10)]
        results = [
            {"item_id": row["item_id"], "status": "queued", "message": ""}
            for row in ready
        ]
        job = {"account": "jv", "updated_at": ""}

        with (
            patch("apps.ebay_bulk_end.workflows.save_job"),
            patch(
                "apps.ebay_bulk_end.workflows.end_items", return_value={}
            ) as end_items,
            patch(
                "apps.ebay_bulk_end.workflows._reconcile",
                return_value=("ended", "Ended"),
            ) as reconcile,
        ):
            _send_batch("token", ready, results, job)

        self.assertEqual(end_items.call_count, 1)
        self.assertEqual(reconcile.call_count, 10)
        self.assertTrue(all(row["status"] == "ended" for row in results))

    def test_per_item_failure_is_preserved_while_other_listing_succeeds(self):
        job = self._job(["queued", "queued"])
        success_id, failure_id = [row["item_id"] for row in job["selected"]]
        responses = {
            success_id: {
                "ack": "",
                "message": "eBay не завершил объявление.",
                "classification": "",
                "severity_code": "",
                "error_code": "",
                "end_time": "2026-11-10T12:00:00Z",
            },
            failure_id: {
                "ack": "",
                "message": "Invalid listing",
                "classification": "RequestError",
                "severity_code": "Error",
                "error_code": "21916700",
                "end_time": "",
            },
        }
        common = self._common_patches(job)
        with (
            common[0],
            common[1],
            common[2],
            common[3],
            common[4],
            common[5],
            common[6],
            patch(
                "apps.ebay_bulk_end.workflows._verify_item", return_value=("ready", "")
            ),
            patch("apps.ebay_bulk_end.workflows.end_items", return_value=responses),
        ):
            run_end_chunk(job["id"])

        self.assertEqual(job["status"], "partial")
        self.assertEqual([row["status"] for row in job["results"]], ["ended", "failed"])
        self.assertEqual(job["results"][1]["error_code"], "21916700")

    def test_endtime_is_required_before_reporting_success(self):
        ready = [{"item_id": "123456789012"}]
        results = [
            {
                "item_id": "123456789012",
                "status": "queued",
                "message": "",
                "finished_at": "",
            }
        ]
        response = {
            "123456789012": {
                "ack": "Success",
                "message": "eBay не завершил объявление.",
                "classification": "",
                "severity_code": "",
                "error_code": "",
                "end_time": "",
            }
        }

        with (
            patch("apps.ebay_bulk_end.workflows.save_job"),
            patch("apps.ebay_bulk_end.workflows.end_items", return_value=response),
            patch(
                "apps.ebay_bulk_end.workflows._reconcile",
                return_value=("uncertain", "Unknown"),
            ),
        ):
            _send_batch("token", ready, results, {"account": "jv", "updated_at": ""})

        self.assertEqual(results[0]["status"], "uncertain")

    def test_success_uses_per_item_endtime_and_preserves_ebay_warning(self):
        ready = [{"item_id": "123456789012"}]
        results = [
            {
                "item_id": "123456789012",
                "status": "queued",
                "message": "",
                "finished_at": "",
            }
        ]
        response = {
            "123456789012": {
                "ack": "",
                "message": "Ended with a warning",
                "classification": "RequestError",
                "severity_code": "Warning",
                "error_code": "21919144",
                "end_time": "2026-11-10T12:00:00Z",
            }
        }

        with (
            patch("apps.ebay_bulk_end.workflows.save_job"),
            patch("apps.ebay_bulk_end.workflows.end_items", return_value=response),
        ):
            _send_batch("token", ready, results, {"account": "jv", "updated_at": ""})

        self.assertEqual(results[0]["status"], "ended")
        self.assertEqual(results[0]["message"], "Ended with a warning")
