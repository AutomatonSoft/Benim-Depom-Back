import logging
from uuid import UUID

from billiard.exceptions import SoftTimeLimitExceeded
from celery import shared_task
from django.db import DatabaseError, connection
from redis.exceptions import RedisError

from .errors import BulkEndError
from .storage import redis_client
from .workflows import (
    fail_end_continuation,
    fail_scan_continuation,
    run_end_chunk,
    run_scan_chunk,
)

JOB_LOCK_SECONDS = 600
SNAPSHOT_RETRY_SECONDS = 60
logger = logging.getLogger(__name__)


class _PostgresJobLock:
    def __init__(self, job_id):
        self.job_id = str(job_id)
        value = UUID(str(job_id)).int & ((1 << 63) - 1)
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [value])
            self.acquired = cursor.fetchone()[0]

    def release(self):
        value = UUID(str(self.job_id)).int & ((1 << 63) - 1)
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [value])


def _postgres_job_lock(job_id):
    lock = _PostgresJobLock(job_id)
    return lock if lock.acquired else None


def _job_lock(job_id, task):
    lock = redis_client().lock(
        "ebay-bulk-end:v1:job-lock:" + str(job_id),
        timeout=JOB_LOCK_SECONDS,
        blocking_timeout=0,
    )
    if not lock.acquire(blocking=False):
        task.apply_async(
            args=[job_id], queue="marketplace", countdown=JOB_LOCK_SECONDS + 5
        )
        return None
    return lock


@shared_task(
    name="apps.ebay_bulk_end.scan_ebay_eans",
    acks_late=True,
    reject_on_worker_lost=True,
    ignore_result=True,
)
def scan_ebay_eans(job_id):
    try:
        lock = _job_lock(job_id, scan_ebay_eans)
    except (BulkEndError, RedisError):
        lock = _postgres_job_lock(job_id)
        if lock is None:
            scan_ebay_eans.apply_async(
                args=[job_id], queue="marketplace", countdown=JOB_LOCK_SECONDS + 5
            )
            return
    if lock is None:
        return
    try:
        countdown = run_scan_chunk(job_id)
    finally:
        try:
            lock.release()
        except Exception:
            pass
    if countdown is not None:
        try:
            scan_ebay_eans.apply_async(
                args=[job_id], queue="marketplace", countdown=countdown
            )
        except Exception:
            fail_scan_continuation(job_id)


@shared_task(
    name="apps.ebay_bulk_end.end_ebay_job",
    acks_late=True,
    reject_on_worker_lost=True,
    ignore_result=True,
)
def end_ebay_job(job_id):
    lock = _job_lock(job_id, end_ebay_job)
    if lock is None:
        return
    try:
        continue_job = run_end_chunk(job_id)
    finally:
        try:
            lock.release()
        except Exception:
            pass
    if continue_job:
        try:
            end_ebay_job.apply_async(args=[job_id], queue="marketplace")
        except Exception:
            fail_end_continuation(job_id)


@shared_task(
    name="apps.ebay_bulk_end.process_ebay_inventory_snapshot",
    acks_late=True,
    reject_on_worker_lost=True,
    ignore_result=True,
)
def process_ebay_inventory_snapshot(snapshot_id, job_id):
    from .inventory import advance_inventory_snapshot_step, schedule_snapshot_step
    from .models import EbayInventorySnapshot

    try:
        countdown = advance_inventory_snapshot_step(snapshot_id, job_id)
    except (DatabaseError, SoftTimeLimitExceeded):
        # The committed checkpoint remains authoritative; the scan task can enqueue recovery after its lease expires.
        countdown = SNAPSHOT_RETRY_SECONDS
    if countdown is not None:
        try:
            schedule_snapshot_step(snapshot_id, job_id, countdown=countdown)
        except Exception:
            EbayInventorySnapshot.objects.filter(pk=snapshot_id).update(
                step_scheduled_at=None
            )
        return

    cleanup_ebay_inventory_snapshots.apply_async(
        args=[str(snapshot_id)], queue="maintenance"
    )
    cleanup_ebay_inventory_snapshots.apply_async(args=[None], queue="maintenance")


@shared_task(
    name="apps.ebay_bulk_end.cleanup_ebay_inventory_snapshots",
    acks_late=True,
    reject_on_worker_lost=True,
    ignore_result=True,
)
def cleanup_ebay_inventory_snapshots(snapshot_id=None):
    from .maintenance import cleanup_snapshot_batch

    try:
        result = cleanup_snapshot_batch(snapshot_id)
    except (DatabaseError, OSError, SoftTimeLimitExceeded):
        logger.exception("eBay inventory snapshot cleanup failed; retrying")
        cleanup_ebay_inventory_snapshots.apply_async(
            args=[snapshot_id], queue="maintenance", countdown=SNAPSHOT_RETRY_SECONDS
        )
        return
    if result["more"] or result["countdown"]:
        cleanup_ebay_inventory_snapshots.apply_async(
            args=[result["snapshot_id"]],
            queue="maintenance",
            countdown=max(1, result["countdown"] or 1),
        )
