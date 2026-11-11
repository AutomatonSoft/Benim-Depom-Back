from datetime import datetime
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from .errors import BulkEndError
from .models import (
    EbayBulkEndEvent,
    EbayBulkEndInFlight,
    EbayBulkEndJob,
    EbayBulkEndJobItem,
)

FINAL_ITEM_STATES = {
    "ended",
    "failed",
    "blocked",
    "changed",
    "not_active",
    "not_found",
    "already_ended",
    "ownership_mismatch",
    "cancelled",
}
UNCERTAIN_ITEM_STATES = {"uncertain", "sending", "checking"}
FAILED_ITEM_STATES = {
    "failed",
    "blocked",
    "changed",
    "not_active",
    "not_found",
    "already_ended",
    "ownership_mismatch",
    "cancelled",
}


def _date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return timezone.make_aware(result) if timezone.is_naive(result) else result


def _job_items(job):
    matches = {
        row["item_id"]: row for row in job.get("matches", []) if row.get("item_id")
    }
    results = {
        row["item_id"]: row for row in job.get("results", []) if row.get("item_id")
    }
    selected = {
        row["item_id"]: index
        for index, row in enumerate(job.get("selected", []))
        if row.get("item_id")
    }
    for item_id in matches.keys() | results.keys():
        match = matches.get(item_id, {})
        result = results.get(item_id, {})
        status = result.get("status", "matched")
        yield {
            "item_id": item_id,
            "sku": match.get("sku", result.get("sku", ""))[:255],
            "matched_eans": result.get("eans")
            or [row.get("ean") for row in match.get("matches", []) if row.get("ean")],
            "match_type": match.get("match_type", ""),
            "details": {"match": match, "result": result},
            "selected": item_id in selected or item_id in results,
            "selection_order": selected.get(item_id),
            "risky_variations": bool(match.get("risky")),
            "auction_risk": bool(match.get("auction_risky")),
            "managed_by_benim": bool(match.get("managed_by_benim")),
            "status": status,
            "ebay_error_code": str(result.get("error_code", ""))[:64],
            "ebay_error_message": str(result.get("message", ""))[:4000],
            "attempt_count": int(result.get("attempt_count", 0) or 0),
            "batch_id": _uuid_or_none(result.get("batch_id")),
            "ebay_correlation_id": str(result.get("correlation_id", ""))[:255],
            "remote_end_time": _date(result.get("end_time")),
            "last_verified_at": _date(result.get("last_verified_at")),
            "started_at": _date(result.get("started_at")),
            "finished_at": _date(result.get("finished_at")),
        }


def _uuid_or_none(value):
    try:
        return UUID(str(value)) if value else None
    except (TypeError, ValueError):
        return None


def _checkpoint(job):
    excluded = {
        "id",
        "manager_id",
        "account",
        "environment",
        "seller_id",
        "kind",
        "status",
        "created_at",
        "updated_at",
        "finished_at",
        "eans",
        "matches",
        "results",
        "selected",
        "progress",
        "message",
        "cancel_requested",
        "snapshot_item_ids",
        "verify_item_ids",
        "candidate_item_ids",
    }
    return {key: value for key, value in job.items() if key not in excluded}


def _append_event(job_row, event_type, metadata=None, *, item=None, actor_id=None):
    EbayBulkEndEvent.objects.create(
        job=job_row,
        item=item,
        actor_user_id=actor_id or job_row.created_by_id,
        event_type=event_type,
        metadata=metadata or {},
    )


def persist_job(job):
    job_uuid = UUID(str(job["id"]))
    items = list(_job_items(job))
    with transaction.atomic():
        job_row = EbayBulkEndJob.objects.select_for_update().filter(pk=job_uuid).first()
        created = job_row is None
        old_status = job_row.status if job_row else None
        if job_row and job_row.kind == "end" and job.get("kind", "scan") == "scan":
            raise BulkEndError(
                "Подтверждённое задание нельзя перезаписать состоянием поиска."
            )
        values = {
            "created_by_id": job["manager_id"],
            "account": job["account"],
            "environment": job["environment"],
            "ebay_seller_id": str(job.get("seller_id", ""))[:255],
            "kind": job.get("kind", "scan"),
            "status": job.get("status", "queued"),
            "discovery_strategy": job.get("discovery_strategy", "")[:40],
            "discovery_complete": bool(job.get("discovery_complete", False)),
            "discovery_details": job.get("discovery_details", {}),
            "workflow_state": _checkpoint(job),
            "cancel_requested": bool(
                job.get("cancel_requested", False)
                or (job_row and job_row.cancel_requested)
            ),
            "input_eans": job.get("eans", []),
            "progress": job.get("progress", {}),
            "unique_ean_count": len(set(job.get("eans", []))),
            "matched_item_count": len(job.get("matches", [])),
            "selected_item_count": len(job.get("selected", [])),
            "ended_count": sum(
                row.get("status") == "ended" for row in job.get("results", [])
            ),
            "failed_count": sum(
                row.get("status") in FAILED_ITEM_STATES
                for row in job.get("results", [])
            ),
            "uncertain_count": sum(
                row.get("status") in UNCERTAIN_ITEM_STATES
                for row in job.get("results", [])
            ),
            "not_found_count": len(job.get("unmatched_eans", [])),
            "report_task_id": str(job.get("report_task_id", ""))[:255],
            "preview_expires_at": _date(job.get("preview_expires_at")),
            "last_error": str(job.get("message", ""))[:8000],
            "scan_started_at": _date(job.get("scan_started_at")),
            "scan_finished_at": _date(job.get("scan_finished_at")),
            "confirmed_at": _date(job.get("confirmed_at")),
            "execution_started_at": _date(job.get("execution_started_at")),
            "finished_at": _date(job.get("finished_at")),
            "inventory_snapshot_id": _uuid_or_none(job.get("inventory_snapshot_id")),
        }
        if created:
            job_row = EbayBulkEndJob(id=job_uuid, **values)
            job_row.save()
        else:
            for key, value in values.items():
                setattr(job_row, key, value)
            job_row.save()

        existing = {
            row.item_id: row for row in EbayBulkEndJobItem.objects.filter(job=job_row)
        }
        create_rows = []
        update_rows = []
        changed_status = []
        changed_selection = []
        for values in items:
            item_id = values.pop("item_id")
            row = existing.get(item_id)
            if row is None:
                new_row = EbayBulkEndJobItem(job=job_row, item_id=item_id, **values)
                create_rows.append(new_row)
                continue
            if values["attempt_count"] < row.attempt_count or (
                row.status in FINAL_ITEM_STATES
                and values["status"] in {"queued", "checking", "sending"}
            ):
                raise BulkEndError(
                    "Состояние задания изменилось; устаревшая запись заблокирована."
                )
            if row.status != values["status"]:
                changed_status.append((row, row.status, values["status"]))
            if row.selected != values["selected"]:
                changed_selection.append((row, values["selected"]))
            for key, value in values.items():
                setattr(row, key, value)
            update_rows.append(row)
        if create_rows:
            EbayBulkEndJobItem.objects.bulk_create(create_rows, batch_size=1000)
        if update_rows:
            EbayBulkEndJobItem.objects.bulk_update(
                update_rows,
                fields=(
                    "sku",
                    "matched_eans",
                    "match_type",
                    "details",
                    "selected",
                    "selection_order",
                    "risky_variations",
                    "auction_risk",
                    "managed_by_benim",
                    "status",
                    "ebay_error_code",
                    "ebay_error_message",
                    "attempt_count",
                    "batch_id",
                    "ebay_correlation_id",
                    "remote_end_time",
                    "last_verified_at",
                    "started_at",
                    "finished_at",
                ),
                batch_size=500,
            )
        if created:
            _append_event(
                job_row,
                "scan_created" if job_row.kind == "scan" else "job_created",
                {"account": job_row.account},
            )
        if old_status != job_row.status:
            event_type = {
                "preview": "scan_completed",
                "ending": "preview_confirmed",
                "complete": "job_completed",
                "failed": "job_failed",
                "cancelled": "job_cancelled",
                "incomplete": "scan_incomplete",
            }.get(job_row.status, "job_status_changed")
            _append_event(
                job_row, event_type, {"from": old_status, "to": job_row.status}
            )
        item_lookup = {
            row.item_id: row for row in EbayBulkEndJobItem.objects.filter(job=job_row)
        }
        if create_rows:
            for row in create_rows:
                _append_event(
                    job_row,
                    "item_discovered",
                    {"item_id": row.item_id},
                    item=item_lookup[row.item_id],
                )
        event_for_status = {
            "ended": "item_ended",
            "failed": "item_failed",
            "uncertain": "item_uncertain",
            "blocked": "item_blocked",
        }
        for row, old_item_status, new_item_status in changed_status:
            _append_event(
                job_row,
                event_for_status.get(new_item_status, "item_status_changed"),
                {
                    "from": old_item_status,
                    "to": new_item_status,
                    "error_code": row.ebay_error_code,
                },
                item=row,
            )
        for row, selected in changed_selection:
            _append_event(
                job_row,
                "item_selected" if selected else "item_deselected",
                {"item_id": row.item_id},
                item=row,
            )
        for result in job.get("results", []):
            if result.get("status") in FINAL_ITEM_STATES:
                EbayBulkEndInFlight.objects.filter(
                    job=job_row, item_id=result.get("item_id")
                ).delete()


def load_persisted_job(job_id):
    try:
        job_row = EbayBulkEndJob.objects.select_related("inventory_snapshot").get(
            pk=UUID(str(job_id))
        )
    except (EbayBulkEndJob.DoesNotExist, TypeError, ValueError):
        return None
    checkpoint = job_row.workflow_state
    job = {
        "id": str(job_row.pk),
        "manager_id": job_row.created_by_id,
        "account": job_row.account,
        "environment": job_row.environment,
        "seller_id": job_row.ebay_seller_id,
        "kind": job_row.kind,
        "status": job_row.status,
        "created_at": job_row.created_at.isoformat(),
        "updated_at": job_row.finished_at.isoformat()
        if job_row.finished_at
        else job_row.created_at.isoformat(),
        "finished_at": job_row.finished_at.isoformat() if job_row.finished_at else None,
        "progress": job_row.progress,
        "message": job_row.last_error,
        "eans": job_row.input_eans,
        "matches": [],
        "results": [],
        "unmatched_eans": checkpoint.get("unmatched_eans", []),
        "discovery_complete": job_row.discovery_complete,
        "discovery_strategy": job_row.discovery_strategy,
        "discovery_details": job_row.discovery_details,
        "inventory_snapshot_id": str(job_row.inventory_snapshot_id)
        if job_row.inventory_snapshot_id
        else None,
        "report_task_id": job_row.report_task_id,
        "preview_expires_at": job_row.preview_expires_at.isoformat()
        if job_row.preview_expires_at
        else None,
        "cancel_requested": job_row.cancel_requested,
        **checkpoint,
    }
    matches_by_id = {}
    for row in job_row.items.order_by("selection_order", "id"):
        match = row.details.get("match")
        result = row.details.get("result")
        if match:
            matches_by_id[row.item_id] = match
        if result:
            job["results"].append(result)
        if row.selected and match:
            job.setdefault("selected", []).append(match)
    job["matches"] = list(matches_by_id.values())
    return job


def record_event(job_id, event_type, metadata=None, *, item_id=None, actor_id=None):
    try:
        job = EbayBulkEndJob.objects.get(pk=UUID(str(job_id)))
    except (EbayBulkEndJob.DoesNotExist, TypeError, ValueError):
        return
    item = None
    if item_id:
        item = EbayBulkEndJobItem.objects.filter(job=job, item_id=item_id).first()
    _append_event(job, event_type, metadata, item=item, actor_id=actor_id)


def reserve_end_batch(job, item_ids, batch_id):
    try:
        with transaction.atomic():
            existing_job = (
                EbayBulkEndJob.objects.select_for_update()
                .filter(pk=UUID(str(job["id"])))
                .first()
            )
            if (
                existing_job
                and existing_job.items.filter(
                    item_id__in=item_ids, attempt_count__gt=0
                ).exists()
            ):
                raise BulkEndError(
                    "Повторная отправка уже обработанной пачки запрещена."
                )
            persist_job(job)
            job_row = EbayBulkEndJob.objects.select_for_update().get(
                pk=UUID(str(job["id"]))
            )
            items_by_id = {
                row.item_id: row
                for row in EbayBulkEndJobItem.objects.select_for_update().filter(
                    job=job_row, item_id__in=item_ids
                )
            }
            if len(items_by_id) != len(item_ids):
                return False
            for item_id in item_ids:
                row = items_by_id[item_id]
                EbayBulkEndInFlight.objects.create(
                    job=job_row,
                    job_item=row,
                    seller_id=job_row.ebay_seller_id,
                    environment=job_row.environment,
                    item_id=item_id,
                    batch_id=batch_id,
                )
                _append_event(
                    job_row,
                    "batch_sending",
                    {"batch_id": str(batch_id), "attempt": row.attempt_count},
                    item=row,
                )
        return True
    except IntegrityError:
        return False
