from unittest.mock import call, patch

from billiard.exceptions import SoftTimeLimitExceeded
from django.db import OperationalError
from django.test import SimpleTestCase

from apps.ebay_bulk_end.tasks import (
    SNAPSHOT_RETRY_SECONDS,
    cleanup_ebay_inventory_snapshots,
    process_ebay_inventory_snapshot,
)


class SnapshotMaintenanceTaskTests(SimpleTestCase):
    @patch("apps.ebay_bulk_end.inventory.schedule_snapshot_step")
    @patch("apps.ebay_bulk_end.inventory.advance_inventory_snapshot_step")
    def test_soft_time_limit_reschedules_from_the_committed_checkpoint(
        self, advance_snapshot, schedule_snapshot
    ):
        advance_snapshot.side_effect = SoftTimeLimitExceeded()

        process_ebay_inventory_snapshot("snapshot-1", "job-1")

        schedule_snapshot.assert_called_once_with(
            "snapshot-1", "job-1", countdown=SNAPSHOT_RETRY_SECONDS
        )

    def test_snapshot_task_redelivers_after_worker_loss(self):
        self.assertTrue(process_ebay_inventory_snapshot.acks_late)
        self.assertTrue(process_ebay_inventory_snapshot.reject_on_worker_lost)

    @patch("apps.ebay_bulk_end.tasks.cleanup_ebay_inventory_snapshots.apply_async")
    @patch("apps.ebay_bulk_end.inventory.advance_inventory_snapshot_step")
    def test_completed_processing_queues_cleanup_on_maintenance(
        self, advance_snapshot, enqueue_cleanup
    ):
        advance_snapshot.return_value = None

        process_ebay_inventory_snapshot("snapshot-1", "job-1")

        self.assertEqual(
            enqueue_cleanup.call_args_list,
            [
                call(args=["snapshot-1"], queue="maintenance"),
                call(args=[None], queue="maintenance"),
            ],
        )

    @patch("apps.ebay_bulk_end.tasks.cleanup_ebay_inventory_snapshots.apply_async")
    @patch("apps.ebay_bulk_end.maintenance.cleanup_snapshot_batch")
    def test_cleanup_requeues_after_a_transient_database_error(
        self, cleanup_batch, enqueue_cleanup
    ):
        cleanup_batch.side_effect = OperationalError("database unavailable")

        with self.assertLogs("apps.ebay_bulk_end.tasks", level="ERROR"):
            cleanup_ebay_inventory_snapshots("snapshot-1")

        enqueue_cleanup.assert_called_once_with(
            args=["snapshot-1"],
            queue="maintenance",
            countdown=SNAPSHOT_RETRY_SECONDS,
        )

    @patch("apps.ebay_bulk_end.tasks.cleanup_ebay_inventory_snapshots.apply_async")
    @patch("apps.ebay_bulk_end.maintenance.cleanup_snapshot_batch")
    def test_cleanup_continues_bounded_batches_on_maintenance(
        self, cleanup_batch, enqueue_cleanup
    ):
        cleanup_batch.return_value = {
            "snapshot_id": "snapshot-1", "more": True, "countdown": 0
        }

        cleanup_ebay_inventory_snapshots("snapshot-1")

        enqueue_cleanup.assert_called_once_with(
            args=["snapshot-1"], queue="maintenance", countdown=1
        )
