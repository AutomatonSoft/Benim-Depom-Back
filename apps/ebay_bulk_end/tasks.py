from celery import shared_task

from .storage import redis_client
from .workflows import (
    fail_end_continuation,
    fail_scan_continuation,
    run_end_chunk,
    run_scan_chunk,
)

JOB_LOCK_SECONDS = 600


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
    lock = _job_lock(job_id, scan_ebay_eans)
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
