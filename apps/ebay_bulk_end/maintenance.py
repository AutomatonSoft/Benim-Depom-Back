import math
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .audit import record_event
from .models import EbayInventoryListing, EbayInventorySnapshot
from .staging import delete_report_path, delete_snapshot_staging_files

DELETE_BATCH_SIZE = 1000
QUOTA_WINDOW = timedelta(hours=24)
FINAL_STATUSES = ("complete", "failed", "incomplete", "timed_out")


def cleanup_snapshot_batch(snapshot_id=None):
    now = timezone.now()
    cutoff = now - QUOTA_WINDOW
    file_path = ""
    next_delay = 0
    more = False
    snapshot_uuid = str(snapshot_id) if snapshot_id else None
    event_job_id = None
    with transaction.atomic():
        snapshots = EbayInventorySnapshot.objects.select_for_update(skip_locked=True)
        eligible = (
            snapshots.filter(status__in=FINAL_STATUSES)
            .filter(
                Q(status="complete", expires_at__lte=now)
                | Q(
                    status__in=("failed", "incomplete", "timed_out"),
                    created_at__lte=now - timedelta(hours=1),
                )
            )
            .filter(Q(owner_token__isnull=True) | Q(owner_expires_at__lte=now))
            .exclude(jobs__kind="scan", jobs__status__in=("queued", "scanning"))
        )
        if snapshot_uuid:
            snapshot = eligible.filter(pk=snapshot_uuid).first()
        else:
            snapshot = eligible.order_by("expires_at", "created_at").first()

        if snapshot:
            snapshot_uuid = str(snapshot.pk)
            event_job_id = (
                snapshot.jobs.order_by("created_at")
                .values_list("pk", flat=True)
                .first()
            )
            if not snapshot.cleanup_started_at:
                snapshot.cleanup_started_at = now
                snapshot.save(update_fields=("cleanup_started_at",))
                if event_job_id:
                    record_event(
                        str(event_job_id),
                        "snapshot_cleanup_started",
                        {"snapshot_id": snapshot_uuid},
                    )
            listing_ids = list(
                EbayInventoryListing.objects.filter(snapshot_id=snapshot.pk)
                .order_by("pk")
                .values_list("pk", flat=True)[:DELETE_BATCH_SIZE]
            )
            if listing_ids:
                EbayInventoryListing.objects.filter(pk__in=listing_ids).delete()
                more = True
            else:
                file_path = snapshot.report_path
                snapshot.report_path = ""
                snapshot.report_size = 0
                snapshot.report_mtime_ns = 0
                snapshot.checkpoint_cookie = ""
                snapshot.cleanup_completed_at = now
                snapshot.save(
                    update_fields=(
                        "report_path",
                        "report_size",
                        "report_mtime_ns",
                        "checkpoint_cookie",
                        "cleanup_completed_at",
                    )
                )
                if event_job_id:
                    record_event(
                        str(event_job_id),
                        "snapshot_cleanup_completed",
                        {"snapshot_id": snapshot_uuid},
                    )
                if (
                    snapshot.last_report_download_attempt_at
                    and snapshot.last_report_download_attempt_at > cutoff
                ):
                    next_delay = max(
                        1,
                        math.ceil(
                            (
                                snapshot.last_report_download_attempt_at
                                + QUOTA_WINDOW
                                - now
                            ).total_seconds()
                        ),
                    )
                else:
                    snapshot.delete()

    if file_path:
        delete_report_path(file_path)
    if snapshot_id and not snapshot:
        existing = EbayInventorySnapshot.objects.filter(pk=snapshot_uuid).first()
        if existing and existing.status in FINAL_STATUSES:
            ready_at = (
                existing.expires_at
                if existing.status == "complete" and existing.expires_at
                else existing.created_at + timedelta(hours=1)
            )
            if (
                existing.owner_token
                and existing.owner_expires_at
                and existing.owner_expires_at > now
            ):
                ready_at = max(
                    ready_at, existing.owner_expires_at + timedelta(seconds=5)
                )
            if existing.jobs.filter(
                kind="scan", status__in=("queued", "scanning")
            ).exists():
                ready_at = max(ready_at, now + timedelta(seconds=60))
            next_delay = min(3000, max(1, math.ceil((ready_at - now).total_seconds())))
            return {
                "snapshot_id": snapshot_uuid,
                "more": False,
                "countdown": next_delay,
            }
        if existing:
            snapshot_uuid = None
    files_remain = (
        delete_snapshot_staging_files(snapshot_uuid) if snapshot_uuid else False
    )
    return {
        "snapshot_id": snapshot_uuid,
        "more": more or files_remain,
        "countdown": next_delay,
    }
