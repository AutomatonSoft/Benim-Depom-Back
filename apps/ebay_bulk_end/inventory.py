from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from .audit import record_event
from .auth import access_context
from .domain import MAX_MATCHES, child, item_summary, text
from .ebay import get_item
from .errors import BulkEndError, EBayRateLimitError
from .feed import (
    MARKETPLACE_ID,
    create_inventory_task,
    download_inventory_report,
    get_inventory_task,
    iter_inventory_rows,
)
from .managed_publications import managed_depotum_item_ids
from .models import EbayInventoryListing, EbayInventorySnapshot
from .storage import save_job

# Keeps report creation below eBay's 25-per-day quota; selected candidates are still rechecked live.
SNAPSHOT_TTL = timedelta(minutes=58)
REPORT_TIMEOUT = timedelta(hours=24)
REPORT_POLL_SECONDS = 60
REPORT_MAX_POLL_SECONDS = 5 * 60
IMPORT_BATCH_SIZE = 1000


def snapshot_for_scan(*, account, environment, seller_id):
    scope = {
        "account": account,
        "environment": environment,
        "ebay_seller_id": seller_id,
        "marketplace_id": MARKETPLACE_ID,
    }
    now = timezone.now()
    snapshot = EbayInventorySnapshot.objects.filter(
        **scope,
        complete=True,
        expires_at__gt=now,
    ).order_by("-completed_at").first()
    if snapshot:
        return snapshot, False
    EbayInventorySnapshot.objects.filter(
        **scope,
        status__in=("requested", "queued", "in_progress"),
        created_at__lte=now - REPORT_TIMEOUT,
    ).update(status="failed", error_message="Inventory report task timed out.")
    EbayInventorySnapshot.objects.filter(**scope).filter(
        Q(complete=True, expires_at__lte=now) | Q(status__in=("failed", "incomplete"))
    ).exclude(jobs__kind="scan", jobs__status__in=("queued", "scanning")).delete()
    snapshot = EbayInventorySnapshot.objects.filter(
        **scope,
        status__in=("requested", "queued", "in_progress"),
        created_at__gt=now - REPORT_TIMEOUT,
    ).order_by("created_at").first()
    if snapshot:
        return snapshot, False
    try:
        with transaction.atomic():
            snapshot = EbayInventorySnapshot.objects.create(**scope)
        return snapshot, True
    except IntegrityError:
        snapshot = EbayInventorySnapshot.objects.filter(
            **scope,
            status__in=("requested", "queued", "in_progress"),
            created_at__gt=now - REPORT_TIMEOUT,
        ).order_by("created_at").first()
        if snapshot:
            return snapshot, False
        raise


def import_inventory_report(snapshot, fileobj):
    count = 0
    batch = []
    try:
        for row in iter_inventory_rows(fileobj):
            count += 1
            if row["marketplace_id"] != snapshot.marketplace_id:
                raise BulkEndError("В отчёте eBay найдены объявления другого маркетплейса.")
            if not row["sku"]:
                continue
            batch.append(
                EbayInventoryListing(
                    snapshot=snapshot,
                    item_id=row["item_id"],
                    sku=row["sku"],
                    marketplace_id=row["marketplace_id"],
                )
            )
            if len(batch) >= IMPORT_BATCH_SIZE:
                EbayInventoryListing.objects.bulk_create(
                    batch, batch_size=IMPORT_BATCH_SIZE, ignore_conflicts=True
                )
                batch.clear()
        if batch:
            EbayInventoryListing.objects.bulk_create(
                batch, batch_size=IMPORT_BATCH_SIZE, ignore_conflicts=True
            )
    except Exception as error:
        snapshot.listings.all().delete()
        snapshot.status = "incomplete"
        snapshot.complete = False
        snapshot.record_count = count
        snapshot.error_message = str(error)[:2000]
        snapshot.completed_at = timezone.now()
        snapshot.save(
            update_fields=("status", "complete", "record_count", "error_message", "completed_at")
        )
        raise
    now = timezone.now()
    snapshot.status = "complete"
    snapshot.complete = True
    snapshot.record_count = count
    snapshot.completed_at = now
    snapshot.expires_at = now + SNAPSHOT_TTL
    snapshot.error_message = ""
    snapshot.save(
        update_fields=(
            "status",
            "complete",
            "record_count",
            "completed_at",
            "expires_at",
            "error_message",
        )
    )
    return count


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
    for item_id, sku in rows.order_by("item_id", "sku").values_list("item_id", "sku").iterator(
        chunk_size=1000
    ):
        by_item.setdefault(item_id, set()).add(sku)
    return [(item_id, sorted(skus)) for item_id, skus in sorted(by_item.items())]


def run_inventory_scan_chunk(job):
    if not job or job.get("kind") != "scan" or job.get("status") not in {"queued", "scanning"}:
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
            _finish_scan(job, "failed", "Подключённый eBay аккаунт изменился во время задания.")
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
        if snapshot is None:
            snapshot, created = snapshot_for_scan(
                account=job["account"],
                environment=job["environment"],
                seller_id=seller_id,
            )
            job["inventory_snapshot_id"] = str(snapshot.pk)
            job["discovery_strategy"] = "sell_feed_active_inventory_sku_index"
            job["discovery_details"] = {
                "marketplace_id": MARKETPLACE_ID,
                "coverage": "active_listings_by_sku",
                "ean_field_in_report": False,
                "not_found_status": "not_found_in_sku_index",
            }
            if created:
                snapshot.task_id = create_inventory_task(token)
                snapshot.status = "queued"
                snapshot.save(update_fields=("task_id", "status"))
                job["report_task_id"] = snapshot.task_id
                record_event(
                    job["id"], "feed_report_requested", {"task_id": snapshot.task_id}
                )

        if not snapshot.complete and snapshot.status in {"failed", "incomplete"}:
            _finish_scan(
                job,
                "incomplete" if snapshot.status == "incomplete" else "failed",
                snapshot.error_message or "Отчёт eBay завершился без полного результата.",
            )
            return None

        if not snapshot.complete:
            if snapshot.status == "requested" and not snapshot.task_id:
                if now - snapshot.created_at > timedelta(seconds=60):
                    snapshot.status = "failed"
                    snapshot.error_message = "Report task creation did not finish."
                    snapshot.save(update_fields=("status", "error_message"))
                    job.pop("inventory_snapshot_id", None)
                    save_job(job)
                    return 1
                job["progress"] = {
                    "stage": "requesting_report",
                    "current": 0,
                    "total": None,
                }
                job["message"] = "Запрашиваем у eBay полный отчёт по активным объявлениям."
                save_job(job)
                return REPORT_POLL_SECONDS
            if now - snapshot.created_at > REPORT_TIMEOUT:
                snapshot.status = "failed"
                snapshot.error_message = "Inventory report task timed out."
                snapshot.save(update_fields=("status", "error_message"))
                _finish_scan(
                    job,
                    "incomplete",
                    "eBay не завершил отчёт вовремя. Повторите поиск позже.",
                )
                return None
            task = get_inventory_task(token, snapshot.task_id)
            state = task["status"]
            if state in {"CREATED", "QUEUED", "IN_PROCESS"}:
                snapshot.status = "in_progress"
                snapshot.save(update_fields=("status",))
                job["report_task_id"] = snapshot.task_id
                job["progress"] = {
                    "stage": "waiting_for_report",
                    "current": 0,
                    "total": None,
                }
                job["message"] = "eBay формирует полный отчёт; ожидание продолжается автоматически."
                job["updated_at"] = timezone.now().isoformat()
                poll_attempts = int(job.get("report_poll_attempts", 0))
                job["report_poll_attempts"] = poll_attempts + 1
                save_job(job)
                return min(
                    REPORT_MAX_POLL_SECONDS,
                    REPORT_POLL_SECONDS * (2 ** min(poll_attempts, 3)),
                )
            if state != "COMPLETED":
                partial = state in {"COMPLETED_WITH_ERROR", "PARTIALLY_PROCESSED"}
                snapshot.status = "incomplete" if partial else "failed"
                snapshot.error_message = f"eBay report task finished with status {state}."
                snapshot.completed_at = timezone.now()
                snapshot.save(
                    update_fields=("status", "error_message", "completed_at")
                )
                _finish_scan(
                    job,
                    "incomplete" if partial else "failed",
                    "eBay не сформировал полный отчёт. Результат поиска нельзя считать полным.",
                )
                return None
            job["progress"] = {
                "stage": "downloading_report",
                "current": 0,
                "total": None,
            }
            job["updated_at"] = timezone.now().isoformat()
            save_job(job)
            with download_inventory_report(token, snapshot.task_id) as report:
                record_count = import_inventory_report(snapshot, report)
            job["discovery_details"]["report_record_count"] = record_count
            job["discovery_details"]["report_completed_at"] = snapshot.completed_at.isoformat()
            record_event(
                job["id"],
                "feed_report_completed",
                {"task_id": snapshot.task_id, "record_count": record_count},
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

        offset = int(job.get("candidate_offset", 0))
        batch = candidates[offset : offset + 20]
        matches_by_id = {row["item_id"]: row for row in job.get("matches", [])}
        for item_id, indexed_skus in batch:
            details = get_item(token, item_id)
            if details is None or text(details, "ListingStatus").casefold() != "active":
                job["discovery_details"]["stale_candidates"] = int(
                    job["discovery_details"].get("stale_candidates", 0)
                ) + 1
                job["stale_candidate_eans"] = sorted(
                    set(job.get("stale_candidate_eans", [])) | set(indexed_skus)
                )
                continue
            actual_seller = text(child(details, "Seller"), "UserID")
            if not actual_seller or actual_seller.casefold() != seller_id.casefold():
                _finish_scan(
                    job,
                    "incomplete",
                    "Владелец одного из найденных объявлений не подтверждён.",
                )
                return None
            summary = item_summary(details, set(job["eans"]))
            if summary is None or not any(
                match.get("source") in {"sku", "sku_and_ean"}
                for match in summary["matches"]
            ):
                job["discovery_details"]["verification_failures"] = int(
                    job["discovery_details"].get("verification_failures", 0)
                ) + 1
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
        job["discovery_details"]["snapshot_expires_at"] = snapshot.expires_at.isoformat()
        job["status"] = "preview"
        job["message"] = "Отчёт по активным объявлениям обработан. Поиск выполнен по SKU; EAN в отчёте отсутствует."
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
        if job.get("inventory_snapshot_id"):
            EbayInventorySnapshot.objects.filter(pk=job["inventory_snapshot_id"]).update(
                status="failed", complete=False, error_message=str(error)[:2000]
            )
        _finish_scan(job, "failed", str(error))
        return None
    except Exception:
        _finish_scan(job, "failed", "Сканирование остановилось из-за внутренней ошибки.")
        return None
