import json
import logging
import math
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from pathlib import Path
from uuid import UUID, uuid4

from billiard.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from .audit import record_event
from .auth import access_context, verified_seller_username
from .domain import MAX_MATCHES, child, item_summary, listing_status, text
from .ebay import get_item
from .errors import (
    BulkEndError,
    EBayRateLimitError,
    EBayTransportError,
    ReportStorageError,
    SnapshotOwnershipLost,
)
from .feed import (
    MARKETPLACE_ID,
    create_inventory_task,
    download_inventory_report_to_path,
    get_inventory_task,
    get_inventory_tasks,
    inspect_staged_inventory_report,
    iter_inventory_chunk,
)
from .managed_publications import managed_depotum_item_ids
from .models import EbayInventoryListing, EbayInventorySnapshot
from .staging import (
    delete_report_path,
    relative_report_path,
    resolve_report_path,
    resolve_report_target,
)
from .staging import (
    report_path as make_report_path,
)
from .storage import save_job

# Keeps report creation below eBay's 25-per-day quota; selected candidates are still rechecked live.
SNAPSHOT_TTL = timedelta(minutes=58)
REPORT_TIMEOUT = timedelta(hours=24)
REPORT_POLL_SECONDS = 60
INITIAL_REPORT_POLL_SECONDS = 5
SCAN_PROGRESS_POLL_SECONDS = 10
REPORT_MAX_POLL_SECONDS = 5 * 60
IMPORT_BATCH_SIZE = 1000
IMPORT_ROWS_PER_TASK = 10_000
VERIFY_ITEMS_PER_TASK = 10
REPORT_DOWNLOAD_RETRIES = 3
REPORT_DOWNLOADS_PER_24_HOURS = 24
SNAPSHOT_OWNER_LEASE = timedelta(minutes=10)
TASK_RECOVERY_WAIT = timedelta(minutes=10)
ACTIVE_SNAPSHOT_STATUSES = (
    "requested",
    "requesting",
    "queued",
    "in_progress",
    "downloading",
    "importing",
    "verifying",
)
FINAL_SNAPSHOT_STATUSES = ("complete", "failed", "incomplete", "timed_out")
logger = logging.getLogger(__name__)


def _record_snapshot_error(snapshot, job_id, error, event_type="snapshot_report_error"):
    details = {
        "job_id": str(job_id),
        "snapshot_id": str(snapshot.pk),
        "account": snapshot.account,
        "environment": snapshot.environment,
        "stage": snapshot.status,
        "ebay_task_id": snapshot.task_id,
        "error_type": type(error).__name__,
        "diagnostics": getattr(error, "diagnostics", {}),
    }
    if event_type == "snapshot_request_uncertain":
        details["reason"] = "transport"
    if isinstance(error, BulkEndError):
        details["message"] = str(error)[:2000]
    logger.warning("eBay inventory error: %s", json.dumps(details, ensure_ascii=False))
    try:
        record_event(job_id, event_type, details)
    except DatabaseError:
        logger.warning(
            "eBay error audit unavailable: job_id=%s snapshot_id=%s",
            job_id,
            snapshot.pk,
        )


def snapshot_for_scan(*, account, environment, seller_id):
    scope = {
        "account": account,
        "environment": environment,
        "ebay_seller_id": seller_id,
        "marketplace_id": MARKETPLACE_ID,
    }
    now = timezone.now()
    snapshot = (
        EbayInventorySnapshot.objects.filter(
            **scope,
            complete=True,
            expires_at__gt=now,
        )
        .order_by("-completed_at")
        .first()
    )
    if snapshot:
        return snapshot, False
    EbayInventorySnapshot.objects.filter(
        **scope,
        status__in=ACTIVE_SNAPSHOT_STATUSES,
        created_at__lte=now - REPORT_TIMEOUT,
    ).filter(Q(owner_expires_at__lte=now) | Q(owner_expires_at__isnull=True)).update(
        status="timed_out",
        complete=False,
        error_message="Inventory report or import exceeded its allowed duration.",
        completed_at=now,
        owner_token=None,
        owner_job_id=None,
        owner_expires_at=None,
    )
    snapshot = (
        EbayInventorySnapshot.objects.filter(
            **scope,
            status__in=ACTIVE_SNAPSHOT_STATUSES,
        )
        .order_by("created_at")
        .first()
    )
    if snapshot:
        return snapshot, False
    try:
        with transaction.atomic():
            snapshot = EbayInventorySnapshot.objects.create(**scope)
        return snapshot, True
    except IntegrityError:
        snapshot = (
            EbayInventorySnapshot.objects.filter(
                **scope,
                status__in=ACTIVE_SNAPSHOT_STATUSES,
            )
            .order_by("created_at")
            .first()
        )
        if snapshot:
            return snapshot, False
        raise


def _claim_snapshot(snapshot_id, job_id):
    now = timezone.now()
    with transaction.atomic():
        snapshot = EbayInventorySnapshot.objects.select_for_update().get(pk=snapshot_id)
        if snapshot.complete or snapshot.status in FINAL_SNAPSHOT_STATUSES:
            return snapshot, None, None, None
        if (
            snapshot.owner_token
            and snapshot.owner_expires_at
            and snapshot.owner_expires_at > now
        ):
            return snapshot, None, None, snapshot.owner_expires_at
        previous_owner = str(snapshot.owner_job_id) if snapshot.owner_token else ""
        token = uuid4()
        generation = snapshot.ownership_generation + 1
        snapshot.owner_token = token
        snapshot.owner_job_id = UUID(str(job_id))
        snapshot.ownership_generation = generation
        snapshot.owner_expires_at = now + SNAPSHOT_OWNER_LEASE
        snapshot.last_heartbeat_at = now
        snapshot.step_scheduled_at = None
        snapshot.save(
            update_fields=(
                "owner_token",
                "owner_job_id",
                "ownership_generation",
                "owner_expires_at",
                "last_heartbeat_at",
                "step_scheduled_at",
            )
        )
        return snapshot, token, generation, previous_owner


def _release_snapshot_owner(snapshot_id, token, generation):
    if not token:
        return
    try:
        EbayInventorySnapshot.objects.filter(
            pk=snapshot_id, owner_token=token, ownership_generation=generation
        ).update(owner_token=None, owner_job_id=None, owner_expires_at=None)
    except DatabaseError:
        pass


def _heartbeat_snapshot(snapshot_id, token, generation):
    now = timezone.now()
    return (
        EbayInventorySnapshot.objects.filter(
            pk=snapshot_id,
            owner_token=token,
            ownership_generation=generation,
            owner_expires_at__gt=now,
        ).update(owner_expires_at=now + SNAPSHOT_OWNER_LEASE, last_heartbeat_at=now)
        == 1
    )


def _require_snapshot_heartbeat(snapshot_id, token, generation):
    if not _heartbeat_snapshot(snapshot_id, token, generation):
        raise SnapshotOwnershipLost("Snapshot import ownership expired.")


def schedule_snapshot_step(snapshot_id, job_id, *, countdown=0):
    countdown = min(3000, max(0, countdown))
    now = timezone.now()
    with transaction.atomic():
        snapshot = (
            EbayInventorySnapshot.objects.select_for_update()
            .filter(pk=snapshot_id)
            .first()
        )
        if (
            not snapshot
            or snapshot.complete
            or snapshot.status in FINAL_SNAPSHOT_STATUSES
        ):
            return False
        if (
            snapshot.owner_token
            and snapshot.owner_expires_at
            and snapshot.owner_expires_at > now
        ):
            return False
        if snapshot.step_scheduled_at and snapshot.step_scheduled_at > now:
            return False
        snapshot.step_scheduled_at = now + timedelta(seconds=max(0, countdown))
        snapshot.save(update_fields=("step_scheduled_at",))
    try:
        from .tasks import process_ebay_inventory_snapshot

        snapshot = EbayInventorySnapshot.objects.only("status").get(pk=snapshot_id)
        options = {}
        if snapshot.status == "downloading":
            # One report download is streamed to disk; leave headroom below Redis' 3600s visibility timeout.
            options = {"soft_time_limit": 2940, "time_limit": 3000}
        process_ebay_inventory_snapshot.apply_async(
            args=[str(snapshot_id), str(job_id)],
            queue="marketplace",
            countdown=countdown,
            **options,
        )
    except Exception:
        EbayInventorySnapshot.objects.filter(pk=snapshot_id).update(
            step_scheduled_at=None
        )
        raise
    return True


def _parse_ebay_datetime(value):
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return timezone.make_aware(result) if timezone.is_naive(result) else result


def _recover_inventory_task(token, started_at, now):
    start = started_at - timedelta(minutes=2)
    sandbox = settings.EBAY_ENVIRONMENT == "sandbox"
    # Sandbox returned fixed UTC-7 wall time labelled Z, including after DST ended.
    sandbox_skew = timedelta(hours=7)
    query_start = start - sandbox_skew if sandbox else start

    def ebay_time(value):
        return (
            value.astimezone(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
            + "Z"
        )

    tasks, total = get_inventory_tasks(
        token, f"{ebay_time(query_start)}..{ebay_time(now)}"
    )
    matches = []
    for task in tasks:
        created_at = _parse_ebay_datetime(task.get("creationDate"))
        if not created_at:
            continue
        possible_dates = [created_at]
        if sandbox:
            possible_dates.append(created_at + sandbox_skew)
        if not any(start <= instant <= now for instant in possible_dates):
            continue
        if task.get("feedType") != "LMS_ACTIVE_INVENTORY_REPORT":
            continue
        # Our request has no listingFormat filter; a partial report cannot be used as a full snapshot.
        if not task.get("filterCriteria"):
            matches.append(task)
    if total > len(tasks) or len(matches) > 1:
        return "ambiguous", None
    if not matches:
        return "missing", None
    task_id = matches[0].get("taskId")
    if not isinstance(task_id, str) or not task_id or len(task_id) > 255:
        return "invalid", None
    return "found", task_id


def _fail_snapshot(snapshot_id, token, generation, message, status="failed"):
    now = timezone.now()
    with transaction.atomic():
        snapshot = (
            EbayInventorySnapshot.objects.select_for_update()
            .filter(
                pk=snapshot_id,
                owner_token=token,
                ownership_generation=generation,
                owner_expires_at__gt=now,
            )
            .first()
        )
        if not snapshot:
            return False
        job_id = snapshot.owner_job_id
        snapshot.status = status
        snapshot.complete = False
        snapshot.error_message = str(message)[:2000]
        snapshot.completed_at = now
        snapshot.owner_token = None
        snapshot.owner_job_id = None
        snapshot.owner_expires_at = None
        snapshot.step_scheduled_at = None
        snapshot.save(
            update_fields=(
                "status",
                "complete",
                "error_message",
                "completed_at",
                "owner_token",
                "owner_job_id",
                "owner_expires_at",
                "step_scheduled_at",
            )
        )
        logger.warning(
            "eBay snapshot stopped: job_id=%s snapshot_id=%s account=%s environment=%s status=%s",
            job_id,
            snapshot.pk,
            snapshot.account,
            snapshot.environment,
            status,
        )
        return True


def _reserve_report_download(snapshot_id, token, generation, connection):
    now = timezone.now()
    cutoff = now - timedelta(hours=24)
    with transaction.atomic():
        # The environment + seller ID pair uniquely identifies and locks this eBay connection.
        connection.__class__.objects.select_for_update().get(pk=connection.pk)
        snapshot = EbayInventorySnapshot.objects.select_for_update().get(pk=snapshot_id)
        if (
            snapshot.owner_token != token
            or snapshot.ownership_generation != generation
            or not snapshot.owner_expires_at
            or snapshot.owner_expires_at <= now
        ):
            return False, 5
        scope = (
            EbayInventorySnapshot.objects.filter(
                environment=snapshot.environment,
                ebay_seller_id=snapshot.ebay_seller_id,
                marketplace_id=snapshot.marketplace_id,
                last_report_download_attempt_at__gte=cutoff,
            )
            .exclude(pk=snapshot.pk)
            .values_list("report_download_attempted_at", flat=True)
        )
        attempts = [
            instant
            for history in list(scope) + [snapshot.report_download_attempted_at]
            for value in (history or [])
            if (instant := _parse_ebay_datetime(value)) and instant > cutoff
        ]
        if len(attempts) >= REPORT_DOWNLOADS_PER_24_HOURS:
            retry_at = min(attempts) + timedelta(hours=24)
            return False, max(1, math.ceil((retry_at - now).total_seconds()))
        history = [
            value
            for value in snapshot.report_download_attempted_at
            if (instant := _parse_ebay_datetime(value)) and instant > cutoff
        ]
        history.append(now.isoformat())
        snapshot.report_download_attempted_at = history
        snapshot.report_download_attempts += 1
        snapshot.last_report_download_attempt_at = now
        snapshot.save(
            update_fields=(
                "report_download_attempted_at",
                "report_download_attempts",
                "last_report_download_attempt_at",
            )
        )
    return True, 0


def advance_inventory_snapshot_step(snapshot_id, job_id):
    snapshot, token, generation, previous_owner = _claim_snapshot(snapshot_id, job_id)
    if token is None:
        if previous_owner is not None:
            return max(
                5,
                min(
                    60, math.ceil((previous_owner - timezone.now()).total_seconds()) + 5
                ),
            )
        return None
    if previous_owner:
        record_event(
            job_id, "snapshot_ownership_lost", {"previous_owner_job_id": previous_owner}
        )
        record_event(
            job_id,
            "snapshot_import_resumed",
            {"generation": snapshot.ownership_generation},
        )
    record_event(
        job_id,
        "snapshot_claimed",
        {
            "snapshot_id": str(snapshot.pk),
            "generation": snapshot.ownership_generation,
            "stage": snapshot.status,
        },
    )
    next_delay = None
    try:
        connection, access_token = access_context(
            snapshot.account, snapshot.environment
        )
        seller_id = str(connection.ebay_user_id or "")
        if not seller_id or seller_id.casefold() != snapshot.ebay_seller_id.casefold():
            _fail_snapshot(
                snapshot.pk,
                token,
                generation,
                "The connected eBay seller changed during snapshot processing.",
            )
            record_event(job_id, "snapshot_import_failed", {"reason": "seller_changed"})
            return None

        if snapshot.status == "requested" and not snapshot.task_id:
            started_at = timezone.now()
            with transaction.atomic():
                connection.__class__.objects.select_for_update().get(pk=connection.pk)
                current = EbayInventorySnapshot.objects.select_for_update().get(
                    pk=snapshot.pk
                )
                if (
                    current.owner_token != token
                    or current.ownership_generation != generation
                    or current.status != "requested"
                    or not current.owner_expires_at
                    or current.owner_expires_at <= timezone.now()
                ):
                    return 1
                recent_requests = EbayInventorySnapshot.objects.filter(
                    environment=current.environment,
                    ebay_seller_id=current.ebay_seller_id,
                    report_request_started_at__gt=started_at - timedelta(hours=24),
                ).order_by("report_request_started_at")
                if recent_requests.count() >= REPORT_DOWNLOADS_PER_24_HOURS:
                    current.error_message = "The daily eBay report request budget is exhausted. Waiting for quota to recover."
                    current.save(update_fields=("error_message",))
                    return min(
                        3000,
                        max(
                            1,
                            math.ceil(
                                (
                                    recent_requests.first().report_request_started_at
                                    + timedelta(hours=24)
                                    - started_at
                                ).total_seconds()
                            ),
                        ),
                    )
                current.status = "requesting"
                current.report_request_started_at = started_at
                current.error_message = ""
                current.save(
                    update_fields=(
                        "status",
                        "report_request_started_at",
                        "error_message",
                    )
                )
            try:
                task_id = create_inventory_task(access_token)
            except EBayTransportError as error:
                snapshot.status = "requesting"
                _record_snapshot_error(
                    snapshot, job_id, error, "snapshot_request_uncertain"
                )
                EbayInventorySnapshot.objects.filter(
                    pk=snapshot.pk,
                    owner_token=token,
                    ownership_generation=generation,
                    owner_expires_at__gt=timezone.now(),
                ).update(error_message=str(error)[:2000])
                return REPORT_POLL_SECONDS
            with transaction.atomic():
                current = EbayInventorySnapshot.objects.select_for_update().get(
                    pk=snapshot.pk
                )
                if (
                    current.owner_token != token
                    or current.ownership_generation != generation
                    or not current.owner_expires_at
                    or current.owner_expires_at <= timezone.now()
                ):
                    return None
                current.task_id = task_id
                current.status = "queued"
                current.save(update_fields=("task_id", "status"))
            record_event(job_id, "snapshot_requested", {"task_id": task_id})
            return INITIAL_REPORT_POLL_SECONDS

        if snapshot.status == "requesting":
            started_at = snapshot.report_request_started_at
            if not started_at:
                _fail_snapshot(
                    snapshot.pk,
                    token,
                    generation,
                    "The report request outcome could not be recovered safely.",
                    "incomplete",
                )
                record_event(
                    job_id,
                    "snapshot_import_failed",
                    {"reason": "request_started_at_missing"},
                )
                return None
            now = timezone.now()
            if now - started_at > TASK_RECOVERY_WAIT:
                _record_snapshot_error(
                    snapshot,
                    job_id,
                    BulkEndError(
                        "The report request could not be confirmed within 10 minutes.",
                        diagnostics={
                            "operation": "recover_inventory_task",
                            "reason": "request_recovery_timeout",
                        },
                    ),
                )
                _fail_snapshot(
                    snapshot.pk,
                    token,
                    generation,
                    "eBay did not expose a unique report task after an uncertain request.",
                    "incomplete",
                )
                record_event(
                    job_id,
                    "snapshot_import_failed",
                    {"reason": "request_recovery_timeout"},
                )
                return None
            state, task_id = _recover_inventory_task(access_token, started_at, now)
            record_event(
                job_id,
                "snapshot_recovery_checked",
                {
                    "snapshot_id": str(snapshot.pk),
                    "outcome": state,
                    "task_id": task_id,
                },
            )
            if state == "ambiguous" or state == "invalid":
                _fail_snapshot(
                    snapshot.pk,
                    token,
                    generation,
                    "The report task could not be identified uniquely after an uncertain request.",
                    "incomplete",
                )
                record_event(
                    job_id,
                    "snapshot_import_failed",
                    {"reason": "request_recovery_ambiguous"},
                )
                return None
            if state == "missing":
                return REPORT_POLL_SECONDS
            with transaction.atomic():
                current = EbayInventorySnapshot.objects.select_for_update().get(
                    pk=snapshot.pk
                )
                if (
                    current.owner_token != token
                    or current.ownership_generation != generation
                    or current.status != "requesting"
                    or not current.owner_expires_at
                    or current.owner_expires_at <= timezone.now()
                ):
                    return None
                current.task_id = task_id
                current.status = "queued"
                current.error_message = ""
                if (
                    EbayInventorySnapshot.objects.exclude(pk=current.pk)
                    .filter(
                        environment=current.environment,
                        ebay_seller_id=current.ebay_seller_id,
                        task_id=task_id,
                    )
                    .exists()
                ):
                    raise BulkEndError(
                        "Recovered eBay report already belongs to another snapshot."
                    )
                current.save(update_fields=("task_id", "status", "error_message"))
            record_event(job_id, "snapshot_request_recovered", {"task_id": task_id})
            return 1

        if snapshot.status in {"requested", "requesting", "queued", "in_progress"}:
            if timezone.now() - snapshot.created_at > REPORT_TIMEOUT:
                _fail_snapshot(
                    snapshot.pk,
                    token,
                    generation,
                    "Inventory report task timed out.",
                    "timed_out",
                )
                record_event(job_id, "snapshot_import_failed", {"reason": "timed_out"})
                return None
            if not snapshot.task_id:
                return REPORT_POLL_SECONDS
            task = get_inventory_task(access_token, snapshot.task_id)
            state = task["status"]
            if state in {"CREATED", "QUEUED", "IN_PROCESS"}:
                attempts = snapshot.report_poll_attempts + 1
                EbayInventorySnapshot.objects.filter(
                    pk=snapshot.pk,
                    owner_token=token,
                    ownership_generation=generation,
                    owner_expires_at__gt=timezone.now(),
                ).update(
                    status="in_progress",
                    report_poll_attempts=attempts,
                    error_message="",
                )
                return min(
                    REPORT_MAX_POLL_SECONDS,
                    REPORT_POLL_SECONDS * (2 ** min(attempts - 1, 3)),
                )
            if state != "COMPLETED":
                partial = state in {"COMPLETED_WITH_ERROR", "PARTIALLY_PROCESSED"}
                _fail_snapshot(
                    snapshot.pk,
                    token,
                    generation,
                    f"eBay report task finished with status {state}.",
                    "incomplete" if partial else "failed",
                )
                record_event(
                    job_id,
                    "snapshot_import_failed",
                    {"task_id": snapshot.task_id, "status": state},
                )
                return None
            completed_at = _parse_ebay_datetime(task.get("completionDate"))
            EbayInventorySnapshot.objects.filter(
                pk=snapshot.pk,
                owner_token=token,
                ownership_generation=generation,
                owner_expires_at__gt=timezone.now(),
            ).update(
                status="downloading", report_completed_at=completed_at, error_message=""
            )
            record_event(
                job_id, "snapshot_download_started", {"task_id": snapshot.task_id}
            )
            return 1

        if snapshot.status == "downloading":
            existing_stage = None
            target = None
            if snapshot.report_path:
                target = resolve_report_target(snapshot.report_path)
                if target.is_file() and not target.is_symlink():
                    staged = inspect_staged_inventory_report(
                        target,
                        heartbeat=lambda: _require_snapshot_heartbeat(
                            snapshot.pk, token, generation
                        ),
                    )
                    existing_stage = staged
                else:
                    # A lost owner may still be writing; each new download needs its own paths.
                    target = None
            if existing_stage is None:
                if snapshot.report_download_attempts >= REPORT_DOWNLOAD_RETRIES:
                    _fail_snapshot(
                        snapshot.pk,
                        token,
                        generation,
                        "The report download was interrupted too many times to resume safely.",
                        "incomplete",
                    )
                    record_event(
                        job_id,
                        "snapshot_import_failed",
                        {"reason": "download_retries_exhausted"},
                    )
                    return None
                allowed, retry_after = _reserve_report_download(
                    snapshot.pk, token, generation, connection
                )
                if not allowed:
                    return retry_after
                if target is None:
                    target = make_report_path(snapshot.pk, generation, token)
                    path_name = relative_report_path(target)
                    with transaction.atomic():
                        current = EbayInventorySnapshot.objects.select_for_update().get(
                            pk=snapshot.pk
                        )
                        if (
                            current.owner_token != token
                            or current.ownership_generation != generation
                            or not current.owner_expires_at
                            or current.owner_expires_at <= timezone.now()
                        ):
                            return None
                        current.report_path = path_name
                        current.import_generation = generation
                        current.save(update_fields=("report_path", "import_generation"))
                else:
                    path_name = snapshot.report_path
                staged = download_inventory_report_to_path(
                    access_token,
                    snapshot.task_id,
                    target,
                    heartbeat=lambda: _require_snapshot_heartbeat(
                        snapshot.pk, token, generation
                    ),
                )
                path_name = relative_report_path(staged["path"])
            else:
                path_name = snapshot.report_path
            with transaction.atomic():
                current = EbayInventorySnapshot.objects.select_for_update().get(
                    pk=snapshot.pk
                )
                if (
                    current.owner_token != token
                    or current.ownership_generation != generation
                    or not current.owner_expires_at
                    or current.owner_expires_at <= timezone.now()
                ):
                    return None
                current.report_path = path_name
                current.report_size = staged["size"]
                current.report_sha256 = staged["sha256"]
                current.report_mtime_ns = Path(staged["path"]).stat().st_mtime_ns
                current.import_generation = current.import_generation or generation
                current.downloaded_at = timezone.now()
                current.import_started_at = current.import_started_at or timezone.now()
                current.status = "importing"
                current.error_message = ""
                current.save(
                    update_fields=(
                        "report_path",
                        "report_size",
                        "report_sha256",
                        "downloaded_at",
                        "report_mtime_ns",
                        "import_generation",
                        "import_started_at",
                        "status",
                        "error_message",
                    )
                )
            record_event(
                job_id,
                "snapshot_import_started",
                {
                    "task_id": snapshot.task_id,
                    "report_size": staged["size"],
                    "sha256": staged["sha256"],
                },
            )
            return 1

        if snapshot.status == "importing":
            path = resolve_report_path(snapshot.report_path)
            staged_stat = path.stat()
            if (
                staged_stat.st_size != snapshot.report_size
                or staged_stat.st_mtime_ns != snapshot.report_mtime_ns
            ):
                raise BulkEndError("Staged eBay report size changed during import.")
            with path.open("rb") as report:
                rows, cookie, eof = iter_inventory_chunk(
                    report, snapshot.checkpoint_cookie, IMPORT_ROWS_PER_TASK
                )
            batch = []
            for row in rows:
                if row["marketplace_id"] != snapshot.marketplace_id:
                    raise BulkEndError(
                        "eBay report contains listings for another marketplace."
                    )
                if row["sku"]:
                    batch.append(
                        EbayInventoryListing(
                            snapshot_id=snapshot.pk,
                            item_id=row["item_id"],
                            sku=row["sku"],
                            marketplace_id=row["marketplace_id"],
                        )
                    )
            with transaction.atomic():
                current = EbayInventorySnapshot.objects.select_for_update().get(
                    pk=snapshot.pk
                )
                if (
                    current.owner_token != token
                    or current.ownership_generation != generation
                    or not current.owner_expires_at
                    or current.owner_expires_at <= timezone.now()
                ):
                    return None
                if current.checkpoint_row != snapshot.checkpoint_row:
                    return 1
                for start in range(0, len(batch), IMPORT_BATCH_SIZE):
                    EbayInventoryListing.objects.bulk_create(
                        batch[start : start + IMPORT_BATCH_SIZE],
                        batch_size=IMPORT_BATCH_SIZE,
                        ignore_conflicts=True,
                    )
                current.checkpoint_row += len(rows)
                current.record_count += len(rows)
                current.checkpoint_cookie = cookie
                if eof:
                    current.status = "verifying"
                current.save(
                    update_fields=(
                        "checkpoint_row",
                        "record_count",
                        "checkpoint_cookie",
                        "status",
                    )
                )
            record_event(
                job_id,
                "snapshot_checkpoint_saved",
                {
                    "rows_committed": len(rows),
                    "row_count": snapshot.checkpoint_row + len(rows),
                },
            )
            return 1

        if snapshot.status == "verifying":
            path = resolve_report_path(snapshot.report_path)
            staged_stat = path.stat()
            if (
                staged_stat.st_size != snapshot.report_size
                or staged_stat.st_mtime_ns != snapshot.report_mtime_ns
                or snapshot.checkpoint_row != snapshot.record_count
            ):
                raise BulkEndError(
                    "eBay report did not pass final completeness verification."
                )
            indexed_rows = snapshot.listings.count()
            if indexed_rows > snapshot.record_count:
                raise BulkEndError(
                    "Inventory index contains more rows than the fully parsed report."
                )
            now = timezone.now()
            with transaction.atomic():
                current = EbayInventorySnapshot.objects.select_for_update().get(
                    pk=snapshot.pk
                )
                if (
                    current.owner_token != token
                    or current.ownership_generation != generation
                    or not current.owner_expires_at
                    or current.owner_expires_at <= timezone.now()
                ):
                    return None
                current.status = "complete"
                current.complete = True
                current.completed_at = now
                current.import_completed_at = now
                current.expires_at = now + SNAPSHOT_TTL
                current.error_message = ""
                current.save(
                    update_fields=(
                        "status",
                        "complete",
                        "completed_at",
                        "import_completed_at",
                        "expires_at",
                        "error_message",
                    )
                )
            record_event(
                job_id,
                "snapshot_import_completed",
                {
                    "row_count": snapshot.record_count,
                    "indexed_listing_count": indexed_rows,
                    "expires_at": (now + SNAPSHOT_TTL).isoformat(),
                },
            )
            delete_report_path(snapshot.report_path)
            return None
        return None
    except (DatabaseError, SoftTimeLimitExceeded) as error:
        _record_snapshot_error(snapshot, job_id, error)
        raise
    except EBayRateLimitError as error:
        _record_snapshot_error(snapshot, job_id, error)
        EbayInventorySnapshot.objects.filter(
            pk=snapshot.pk,
            owner_token=token,
            ownership_generation=generation,
            owner_expires_at__gt=timezone.now(),
        ).update(error_message=str(error)[:2000])
        next_delay = error.retry_after
    except ReportStorageError as error:
        _record_snapshot_error(snapshot, job_id, error)
        _fail_snapshot(snapshot.pk, token, generation, error, "failed")
        record_event(job_id, "snapshot_import_failed", {"reason": "staging_storage"})
        return None
    except EBayTransportError as error:
        _record_snapshot_error(snapshot, job_id, error)
        if (
            snapshot.status == "downloading"
            and snapshot.report_download_attempts >= REPORT_DOWNLOAD_RETRIES
        ):
            _fail_snapshot(snapshot.pk, token, generation, error, "incomplete")
            record_event(
                job_id,
                "snapshot_import_failed",
                {"reason": "download_retries_exhausted"},
            )
            return None
        attempts = snapshot.retry_count + 1
        if attempts > 5:
            _fail_snapshot(snapshot.pk, token, generation, error, "failed")
            record_event(
                job_id,
                "snapshot_import_failed",
                {"reason": "transport_retries_exhausted"},
            )
            return None
        EbayInventorySnapshot.objects.filter(
            pk=snapshot.pk,
            owner_token=token,
            ownership_generation=generation,
            owner_expires_at__gt=timezone.now(),
        ).update(retry_count=attempts, error_message=str(error)[:2000])
        next_delay = min(300, 30 * (2 ** min(attempts - 1, 3)))
    except SnapshotOwnershipLost:
        return None
    except BulkEndError as error:
        _record_snapshot_error(snapshot, job_id, error)
        _fail_snapshot(snapshot.pk, token, generation, error, "incomplete")
        record_event(job_id, "snapshot_import_failed", {"reason": "report_invalid"})
        return None
    except Exception as error:
        _record_snapshot_error(snapshot, job_id, error)
        _fail_snapshot(snapshot.pk, token, generation, error, "failed")
        record_event(job_id, "snapshot_import_failed", {"reason": type(error).__name__})
        return None
    finally:
        _release_snapshot_owner(snapshot.pk, token, generation)
    return next_delay or REPORT_POLL_SECONDS


def _finish_scan(job, status, message=""):
    now = timezone.now().isoformat()
    job["status"] = status
    job["message"] = message
    job["updated_at"] = now
    job["scan_finished_at"] = now
    if status in {"failed", "incomplete"}:
        job["finished_at"] = now
    save_job(job)


def _candidate_rows(rows):
    by_item = {}
    for item_id, sku in (
        rows.order_by("item_id", "sku")
        .values_list("item_id", "sku")
        .iterator(chunk_size=1000)
    ):
        by_item.setdefault(item_id, set()).add(sku)
    return [(item_id, sorted(skus)) for item_id, skus in sorted(by_item.items())]


def run_inventory_scan_chunk(job):
    if (
        not job
        or job.get("kind") != "scan"
        or job.get("status") not in {"queued", "scanning"}
    ):
        return None
    now = timezone.now()
    job["status"] = "scanning"
    job.setdefault("scan_started_at", now.isoformat())
    try:
        connection, token = access_context(job["account"], job["environment"])
        seller_id = str(connection.ebay_user_id or "")
        if not seller_id:
            raise BulkEndError("У выбранного eBay аккаунта не указан seller ID.")
        if job.get("seller_id") and job["seller_id"].casefold() != seller_id.casefold():
            _finish_scan(
                job, "failed", "Подключённый eBay аккаунт изменился во время задания."
            )
            return None
        job["seller_id"] = seller_id

        snapshot = None
        snapshot_id = job.get("inventory_snapshot_id")
        if snapshot_id:
            snapshot = EbayInventorySnapshot.objects.filter(
                pk=snapshot_id,
                account=job["account"],
                environment=job["environment"],
                ebay_seller_id=seller_id,
                marketplace_id=MARKETPLACE_ID,
            ).first()
            if (
                snapshot
                and snapshot.complete
                and (not snapshot.expires_at or snapshot.expires_at <= now)
            ):
                snapshot = None
                job.pop("inventory_snapshot_id", None)
        if snapshot is None:
            snapshot, created = snapshot_for_scan(
                account=job["account"],
                environment=job["environment"],
                seller_id=seller_id,
            )
            job["inventory_snapshot_id"] = str(snapshot.pk)
            job["snapshot_reused"] = not created
            job["discovery_strategy"] = "sell_feed_active_inventory_sku_index"
            job["discovery_details"] = {
                "marketplace_id": MARKETPLACE_ID,
                "coverage": "active_listings_by_sku",
                "ean_field_in_report": False,
                "not_found_status": "not_found_in_sku_index",
            }
        elif "snapshot_reused" not in job:
            job["snapshot_reused"] = True

        if not snapshot.complete and snapshot.status in FINAL_SNAPSHOT_STATUSES:
            job["discovery_details"]["report_request_unconfirmed"] = bool(
                not snapshot.task_id and snapshot.report_request_started_at
            )
            _finish_scan(
                job,
                "incomplete"
                if snapshot.status in {"incomplete", "timed_out"}
                else "failed",
                snapshot.error_message or "The eBay report did not complete.",
            )
            return None

        if not snapshot.complete:
            try:
                schedule_snapshot_step(snapshot.pk, job["id"])
            except Exception as error:
                raise BulkEndError("Could not queue eBay report processing.") from error
            stage = {
                "requested": "requesting_report",
                "requesting": "recovering_report_request",
                "queued": "waiting_for_report",
                "in_progress": "waiting_for_report",
                "downloading": "downloading_report",
                "importing": "importing_report",
                "verifying": "verifying_report",
            }.get(snapshot.status, "preparing_report")
            if snapshot.status == "requested" and snapshot.error_message:
                stage = "waiting_report_quota"
            if snapshot.error_message and snapshot.status in {
                "queued",
                "in_progress",
                "downloading",
            }:
                stage = "retrying_report"
            job["report_task_id"] = snapshot.task_id
            job["progress"] = {
                "stage": stage,
                "current": snapshot.checkpoint_row,
                "total": None,
            }
            job["message"] = {
                "requesting_report": "Requesting the eBay inventory report.",
                "recovering_report_request": "eBay report creation is not confirmed. Checking the original request; no duplicate request is sent.",
                "waiting_for_report": "eBay accepted the report request. Waiting for the report to complete.",
                "downloading_report": "Downloading the completed eBay report.",
                "importing_report": "Importing the downloaded eBay report.",
                "verifying_report": "Verifying the imported eBay report.",
                "retrying_report": "A temporary error interrupted the eBay report request. Retrying the current step.",
                "waiting_report_quota": "Waiting for the daily eBay report request quota to recover.",
            }.get(stage, "Preparing inventory discovery.")
            job["discovery_details"].update(
                {
                    "snapshot_id": str(snapshot.pk),
                    "snapshot_status": snapshot.status,
                    "report_request_started_at": snapshot.report_request_started_at.isoformat()
                    if snapshot.report_request_started_at
                    else None,
                    "report_recovery_deadline": (
                        snapshot.report_request_started_at + TASK_RECOVERY_WAIT
                    ).isoformat()
                    if snapshot.status == "requesting"
                    and snapshot.report_request_started_at
                    else None,
                    "snapshot_generated_at": snapshot.created_at.isoformat(),
                    "report_completed_at": snapshot.report_completed_at.isoformat()
                    if snapshot.report_completed_at
                    else None,
                    "snapshot_reused": job.get("snapshot_reused", False),
                    "sku_discovery_complete": False,
                    "ean_only_discovery_supported": False,
                }
            )
            save_job(job)
            return SCAN_PROGRESS_POLL_SECONDS

        job["report_task_id"] = snapshot.task_id
        job["discovery_details"].update(
            {
                "snapshot_id": str(snapshot.pk),
                "snapshot_generated_at": snapshot.created_at.isoformat(),
                "report_completed_at": snapshot.report_completed_at.isoformat()
                if snapshot.report_completed_at
                else None,
                "import_completed_at": snapshot.import_completed_at.isoformat()
                if snapshot.import_completed_at
                else None,
                "snapshot_age_seconds": max(
                    0, int((timezone.now() - snapshot.created_at).total_seconds())
                ),
                "snapshot_reused": job.get("snapshot_reused", False),
                "snapshot_expires_at": snapshot.expires_at.isoformat()
                if snapshot.expires_at
                else None,
                "sku_discovery_complete": True,
                "ean_only_discovery_supported": False,
                "report_record_count": snapshot.record_count,
            }
        )
        listing_rows = snapshot.listings.filter(sku__in=job["eans"])
        job["indexed_eans"] = sorted(
            listing_rows.order_by().values_list("sku", flat=True).distinct()
        )
        candidate_count = listing_rows.order_by().values("item_id").distinct().count()
        job["discovery_details"]["candidate_item_count"] = candidate_count
        if candidate_count > MAX_MATCHES:
            _finish_scan(
                job,
                "incomplete",
                f"Найдено больше {MAX_MATCHES} объявлений; сократите список EAN, чтобы проверить все совпадения.",
            )
            return None
        candidates = _candidate_rows(listing_rows)
        seller_username = (
            verified_seller_username(token, seller_id) if candidates else ""
        )
        job["seller_username"] = seller_username

        offset = int(job.get("candidate_offset", 0))
        batch = candidates[offset : offset + VERIFY_ITEMS_PER_TASK]
        matches_by_id = {row["item_id"]: row for row in job.get("matches", [])}
        for item_id, indexed_skus in batch:
            details = get_item(token, item_id)
            if details is None or listing_status(details).casefold() != "active":
                job["discovery_details"]["stale_candidates"] = (
                    int(job["discovery_details"].get("stale_candidates", 0)) + 1
                )
                job["stale_candidate_eans"] = sorted(
                    set(job.get("stale_candidate_eans", [])) | set(indexed_skus)
                )
                continue
            actual_seller = text(child(details, "Seller"), "UserID")
            if (
                not actual_seller
                or actual_seller.casefold() != seller_username.casefold()
            ):
                _finish_scan(
                    job,
                    "incomplete",
                    "Владелец одного из найденных объявлений не подтверждён.",
                )
                return None
            summary = item_summary(details, set(job["eans"]))
            if (
                summary is None
                or summary["item_id"] != item_id
                or not any(
                    match.get("source") in {"sku", "sku_and_ean"}
                    for match in summary["matches"]
                )
            ):
                job["discovery_details"]["verification_failures"] = (
                    int(job["discovery_details"].get("verification_failures", 0)) + 1
                )
                continue
            matches_by_id[item_id] = summary

        job["matches"] = list(matches_by_id.values())
        job["candidate_offset"] = offset + len(batch)
        job["progress"] = {
            "stage": "verifying_candidates",
            "current": job["candidate_offset"],
            "total": len(candidates),
        }
        job["updated_at"] = timezone.now().isoformat()
        save_job(job)
        if job["candidate_offset"] < len(candidates):
            return 1
        if job["discovery_details"].get("verification_failures"):
            _finish_scan(
                job,
                "incomplete",
                "Не все кандидаты удалось подтвердить по текущему объявлению. Запустите новый поиск; завершение заблокировано.",
            )
            return None
        if job["account"] == "depotum":
            try:
                managed_ids = managed_depotum_item_ids(
                    [row["item_id"] for row in job["matches"]], job["environment"]
                )
            except Exception as error:
                raise BulkEndError(
                    "Не удалось безопасно проверить публикации BENIM DEPOM; завершение заблокировано."
                ) from error
            for row in job["matches"]:
                if row["item_id"] in managed_ids:
                    row["managed_by_benim"] = True
        matched_eans = {
            match["ean"] for row in job["matches"] for match in row["matches"]
        }
        job["unmatched_eans"] = sorted(set(job["eans"]) - matched_eans)
        job["unmatched_status_by_ean"] = {
            ean: (
                "verification_failed"
                if ean in set(job.get("stale_candidate_eans", []))
                else "not_found_in_sku_index"
            )
            for ean in job["unmatched_eans"]
        }
        # shortcut: the report has SKU, not EAN; add a verified GTIN source for wider EAN coverage.
        job["unmatched_status"] = "not_found_in_sku_index"
        job["discovery_complete"] = True
        job["discovery_details"]["ean_search_complete"] = False
        job["discovery_details"]["sku_search_complete"] = True
        job["discovery_details"]["snapshot_expires_at"] = (
            snapshot.expires_at.isoformat()
        )
        job["status"] = "preview"
        job["message"] = (
            "Отчёт по активным объявлениям обработан. Поиск выполнен по SKU; EAN в отчёте отсутствует."
        )
        job["preview_expires_at"] = (
            timezone.now() + timedelta(seconds=15 * 60)
        ).isoformat()
        job["scan_finished_at"] = timezone.now().isoformat()
        job["progress"] = {
            "stage": "preview_ready",
            "current": len(candidates),
            "total": len(candidates),
        }
        job["updated_at"] = timezone.now().isoformat()
        save_job(job)
        return None
    except EBayRateLimitError as error:
        retries = int(job.get("rate_limit_retries", 0)) + 1
        job["rate_limit_retries"] = retries
        if retries > 3:
            _finish_scan(
                job,
                "failed",
                "eBay продолжает ограничивать запросы. Запустите поиск позже.",
            )
            return None
        job["updated_at"] = timezone.now().isoformat()
        save_job(job)
        return error.retry_after
    except BulkEndError as error:
        logger.warning(
            "eBay scan failed: job_id=%s error_type=%s diagnostics=%s",
            job["id"],
            type(error).__name__,
            getattr(error, "diagnostics", {}),
        )
        _finish_scan(job, "failed", str(error))
        return None
    except Exception:
        _finish_scan(
            job, "failed", "Сканирование остановилось из-за внутренней ошибки."
        )
        return None
