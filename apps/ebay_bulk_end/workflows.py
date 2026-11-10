import csv
import io
from datetime import datetime, timedelta
from uuid import uuid4

from django.utils import timezone

from .auth import access_context
from .domain import (
    MAX_MATCHES,
    auction_state,
    child,
    descendants,
    item_summary,
    safe_csv_cell,
    text,
)
from .ebay import end_items, get_active_page, get_item, get_item_state
from .errors import (
    BulkEndError,
    EBayRateLimitError,
    EBayTransportError,
    OwnershipMismatchError,
)
from .storage import (
    acquire_account_lock,
    cancellation_requested,
    load_job,
    release_account_lock,
    save_job,
)

SCAN_PAGES_PER_TASK = 1
MAX_SCAN_LISTINGS = 2000
END_ITEMS_PER_TASK = 10
PREVIEW_SECONDS = 15 * 60


def new_job(*, account, environment, manager_id, eans):
    now = timezone.now()
    return {
        "id": str(uuid4()),
        "manager_id": manager_id,
        "account": account,
        "environment": environment,
        "kind": "scan",
        "status": "queued",
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "progress": {"current": 0, "total": None},
        "eans": eans,
        "matches": [],
        "scan_phase": "discover",
        "snapshot_item_ids": [],
        "message": "",
    }


def start_scan(job):
    save_job(job)
    try:
        from .tasks import scan_ebay_eans

        scan_ebay_eans.apply_async(args=[job["id"]], queue="marketplace")
    except Exception as error:
        job["status"] = "failed"
        job["message"] = "Не удалось поставить сканирование в очередь."
        save_job(job)
        raise BulkEndError(job["message"]) from error


def fail_scan_continuation(job_id):
    job = load_job(job_id)
    if not job or job.get("kind") != "scan" or job.get("status") != "scanning":
        return
    job["status"] = "failed"
    job["message"] = "Не удалось продолжить сканирование в фоновой очереди."
    job["updated_at"] = timezone.now().isoformat()
    save_job(job)


def fail_end_continuation(job_id):
    job = load_job(job_id)
    if not job or job.get("kind") != "end" or job.get("status") != "ending":
        return
    finished_at = timezone.now().isoformat()
    job["status"] = "partial" if job.get("progress", {}).get("current") else "failed"
    job["message"] = "Не удалось поставить следующую пачку в фоновую очередь."
    for result in job.get("results", []):
        if result.get("status") in {"queued", "checking"}:
            result["status"] = "blocked"
            result["message"] = (
                "Эта пачка не была отправлена в eBay. Запустите новый поиск."
            )
            result["finished_at"] = finished_at
        elif result.get("status") == "sending":
            result["status"] = "uncertain"
            result["message"] = "Проверьте результат этого объявления в eBay вручную."
            result["finished_at"] = finished_at
    job["finished_at"] = finished_at
    job["updated_at"] = finished_at
    save_job(job)
    release_account_lock(job["environment"], job["account"], job["id"])


def run_scan_chunk(job_id):
    job = load_job(job_id)
    if not job or job["kind"] != "scan" or job["status"] not in {"queued", "scanning"}:
        return
    job["status"] = "scanning"
    job["updated_at"] = timezone.now().isoformat()
    save_job(job)
    try:
        connection, token = access_context(job["account"], job["environment"])
        seller_id = str(connection.ebay_user_id or "")
        if not seller_id:
            raise BulkEndError("У выбранного eBay аккаунта не указан seller ID.")
        if job.get("seller_id") and seller_id.casefold() != job["seller_id"].casefold():
            raise OwnershipMismatchError(
                "Подключённый eBay аккаунт изменился во время сканирования."
            )
        job["seller_id"] = seller_id
        requested = set(job["eans"])
        matches_by_id = {row["item_id"]: row for row in job["matches"]}
        phase = job.get("scan_phase", "discover")
        page_item_ids = []
        page_item_id_set = set()
        snapshot_item_ids = set(job.get("snapshot_item_ids", []))
        verify_item_ids = set(job.get("verify_item_ids", []))
        first_page = int(job.get("next_page", 1))
        for page in range(first_page, first_page + SCAN_PAGES_PER_TASK):
            if job.get("total_pages") and page > job["total_pages"]:
                break
            items, total_pages, total_entries, urls = get_active_page(token, page)
            if total_pages > 1667 or total_entries >= 25000:
                job["status"] = "incomplete"
                job["message"] = (
                    "Поиск достиг лимита Trading API в 25 000 объявлений. Полноту подтвердить нельзя; завершение заблокировано."
                )
                job["updated_at"] = timezone.now().isoformat()
                save_job(job)
                return
            if total_entries > MAX_SCAN_LISTINGS:
                job["status"] = "incomplete"
                job["message"] = (
                    "Для поиска EAN нужны отдельные запросы к каждому объявлению. "
                    "В текущем лимите eBay можно безопасно проверить не более "
                    f"{MAX_SCAN_LISTINGS} объявлений; завершение заблокировано."
                )
                job["updated_at"] = timezone.now().isoformat()
                save_job(job)
                return
            if phase == "discover" and page == 1:
                job["total_pages"] = total_pages
                job["total_entries"] = total_entries
                job["progress"]["total"] = total_pages * 2
            elif total_pages != job.get("total_pages") or total_entries != job.get(
                "total_entries"
            ):
                job["status"] = "incomplete"
                job["message"] = (
                    "Состав списка eBay изменился во время поиска. Запустите новый поиск; завершение заблокировано."
                )
                job["updated_at"] = timezone.now().isoformat()
                save_job(job)
                return
            if total_entries and not items:
                job["status"] = "incomplete"
                job["message"] = (
                    "eBay вернул пустую страницу при наличии объявлений. Полноту поиска подтвердить нельзя."
                )
                job["updated_at"] = timezone.now().isoformat()
                save_job(job)
                return
            for item in items:
                item_id = text(item, "ItemID")
                if not item_id.isdigit():
                    job["status"] = "incomplete"
                    job["message"] = (
                        "eBay вернул объявление без корректного ItemID; завершение заблокировано."
                    )
                    break
                known_ids = (
                    snapshot_item_ids if phase == "discover" else verify_item_ids
                )
                if item_id in page_item_id_set or item_id in known_ids:
                    job["status"] = "incomplete"
                    job["message"] = (
                        "eBay повторил ItemID в пагинации. Полноту списка подтвердить нельзя; завершение заблокировано."
                    )
                    break
                page_item_ids.append(item_id)
                page_item_id_set.add(item_id)
                if phase == "discover":
                    details = get_item(token, item_id)
                    if (
                        details is None
                        or text(details, "ItemID") != item_id
                        or text(child(details, "Seller"), "UserID").casefold()
                        != seller_id.casefold()
                        or text(details, "ListingStatus").casefold() != "active"
                    ):
                        job["status"] = "incomplete"
                        job["message"] = (
                            "Не удалось подтвердить активность и владельца объявления через GetItem; "
                            "запустите новый поиск. Завершение заблокировано."
                        )
                        break
                    summary = item_summary(details, requested, urls.get(item_id, ""))
                    if summary:
                        existing = matches_by_id.get(summary["item_id"])
                        if existing is None:
                            job["matches"].append(summary)
                            matches_by_id[summary["item_id"]] = summary
                        else:
                            matches_by_ean = {}
                            for match in existing["matches"] + summary["matches"]:
                                key = (match["ean"], match["type"])
                                matches_by_ean.setdefault(key, set())
                                if match.get("variation"):
                                    matches_by_ean[key].add(match["variation"])
                            existing["matches"] = [
                                {
                                    "ean": ean,
                                    "type": kind,
                                    "variation": " / ".join(sorted(labels)),
                                }
                                for (ean, kind), labels in sorted(
                                    matches_by_ean.items()
                                )
                            ]
                            existing["risky"] = existing["risky"] or summary["risky"]
                            existing["auction_risky"] = (
                                existing.get("auction_risky", False)
                                or summary["auction_risky"]
                            )
                            existing["variation_count"] = max(
                                existing.get("variation_count", 0),
                                summary["variation_count"],
                            )
            if job["status"] != "incomplete":
                if phase == "discover":
                    job.setdefault("snapshot_item_ids", []).extend(page_item_ids)
                else:
                    job.setdefault("verify_item_ids", []).extend(page_item_ids)
            job["progress"]["current"] = (
                page if phase == "discover" else job["total_pages"] + page
            )
            job["next_page"] = page + 1
            job["updated_at"] = timezone.now().isoformat()
            if job["status"] != "incomplete":
                job["rate_limit_retries"] = 0
            if len(job["matches"]) > MAX_MATCHES:
                job["status"] = "incomplete"
                job["message"] = (
                    "Найдено слишком много объявлений. Сузьте список EAN; завершение заблокировано."
                )
            save_job(job)
            if job["status"] == "incomplete":
                return
        if job["status"] == "incomplete":
            return
        if job.get("next_page", 1) <= (job.get("total_pages") or 1):
            return 2
        if phase == "discover":
            if len(job.get("snapshot_item_ids", [])) != job["total_entries"]:
                job["status"] = "incomplete"
                job["message"] = (
                    "eBay вернул неполный или повторяющийся набор активных ItemID; завершение заблокировано."
                )
                job["updated_at"] = timezone.now().isoformat()
                save_job(job)
                return
            job["scan_phase"] = "verify"
            job["verify_item_ids"] = []
            job["next_page"] = 1
            job["progress"]["current"] = job["total_pages"]
            job["updated_at"] = timezone.now().isoformat()
            save_job(job)
            return 2
        if (
            len(job.get("verify_item_ids", [])) != job["total_entries"]
            or set(job.get("verify_item_ids", [])) != snapshot_item_ids
        ):
            job["status"] = "incomplete"
            job["message"] = (
                "Состав списка eBay изменился во время проверки. Запустите новый поиск; завершение заблокировано."
            )
            job["updated_at"] = timezone.now().isoformat()
            save_job(job)
            return
        job["status"] = "preview"
        job["preview_expires_at"] = (
            timezone.now() + timedelta(seconds=PREVIEW_SECONDS)
        ).isoformat()
        matched_eans = {
            match["ean"] for row in job["matches"] for match in row["matches"]
        }
        job["unmatched_eans"] = sorted(set(job["eans"]) - matched_eans)
        job["updated_at"] = timezone.now().isoformat()
        save_job(job)
    except EBayRateLimitError as error:
        retries = int(job.get("rate_limit_retries", 0)) + 1
        job["rate_limit_retries"] = retries
        if retries > 3:
            job["status"] = "failed"
            job["message"] = (
                "eBay продолжает ограничивать запросы. Запустите поиск позже."
            )
            job["updated_at"] = timezone.now().isoformat()
            save_job(job)
            return
        job["updated_at"] = timezone.now().isoformat()
        save_job(job)
        return error.retry_after
    except BulkEndError as error:
        job["status"] = "failed"
        job["message"] = str(error)
        job["updated_at"] = timezone.now().isoformat()
        save_job(job)
    except Exception:
        job["status"] = "failed"
        job["message"] = "Сканирование остановилось из-за внутренней ошибки."
        job["updated_at"] = timezone.now().isoformat()
        save_job(job)


def confirm_job(
    job, item_ids, include_variations, include_auction_risk, confirm_unavailable_reason
):
    matches = {row["item_id"]: row for row in job.get("matches", [])}
    selected = [matches[item_id] for item_id in item_ids]
    job["kind"] = "end"
    job["status"] = "ending"
    job["include_variations"] = bool(include_variations)
    job["include_auction_risk"] = bool(include_auction_risk)
    job["ending_reason"] = "NotAvailable"
    job["reason_confirmed"] = bool(confirm_unavailable_reason)
    job["selected"] = selected
    job["next_offset"] = 0
    job["progress"] = {"current": 0, "total": len(selected)}
    job["results"] = [
        {
            "item_id": row["item_id"],
            "title": row["title"],
            "eans": [match["ean"] for match in row["matches"]],
            "status": "queued",
            "message": "",
            "error_code": "",
            "end_time": "",
            "finished_at": "",
        }
        for row in selected
    ]
    job["updated_at"] = timezone.now().isoformat()
    save_job(job)
    try:
        from .tasks import end_ebay_job

        end_ebay_job.apply_async(args=[job["id"]], queue="marketplace")
    except Exception as error:
        job["status"] = "failed"
        job["message"] = "Не удалось поставить завершение объявлений в очередь."
        job["finished_at"] = timezone.now().isoformat()
        job["updated_at"] = job["finished_at"]
        for result in job["results"]:
            result["status"] = "failed"
            result["message"] = job["message"]
            result["finished_at"] = job["finished_at"]
        save_job(job)
        raise BulkEndError(job["message"]) from error


def _verify_item(token, selected, job):
    item = get_item(token, selected["item_id"])
    if item is None:
        return "not_found", "Объявление больше не найдено в eBay."
    if text(item, "ListingStatus") != "Active":
        return "already_ended", "Объявление уже не активно."
    seller_id = text(child(item, "Seller"), "UserID")
    if not seller_id or seller_id.casefold() != job["seller_id"].casefold():
        return "ownership_mismatch", "Аккаунт продавца не совпал."
    current = item_summary(item, {match["ean"] for match in selected["matches"]})
    expected = [
        {
            "ean": match["ean"],
            "type": match["type"],
            "variation": match.get("variation", ""),
        }
        for match in selected["matches"]
    ]
    if not expected or current is None or current["matches"] != expected:
        return "changed", "EAN объявления изменился после предпросмотра."
    if text(item, "Title")[:500] != selected.get("title", ""):
        return "changed", "Название объявления изменилось после предпросмотра."
    if len(descendants(child(item, "Variations"), "Variation")) != selected.get(
        "variation_count", 0
    ):
        return "changed", "Количество вариантов изменилось после предпросмотра."
    listing_type, bid_count, auction_risky = auction_state(item)
    if listing_type.casefold() != selected.get("listing_type", "").casefold():
        return "changed", "Тип объявления изменился после предпросмотра."
    if listing_type.casefold() != "fixedpriceitem" and bid_count != selected.get(
        "bid_count"
    ):
        return (
            "changed",
            "Количество ставок изменилось после предпросмотра. Запустите новый поиск.",
        )
    if child(item, "Variations") is not None and not job.get("include_variations"):
        return "blocked", "Объявление содержит варианты; отдельно подтвердите риск."
    if auction_risky and not job.get("include_auction_risk"):
        return (
            "blocked",
            "Аукцион с возможными ставками требует отдельного подтверждения.",
        )
    if listing_type.casefold() != "fixedpriceitem" and bid_count is None:
        return "blocked", "Не удалось проверить число ставок аукциона."
    if listing_type.casefold() != "fixedpriceitem" and bid_count:
        end_time = text(child(item, "ListingDetails"), "EndTime")
        try:
            end_time = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        except ValueError:
            return (
                "blocked",
                "Не удалось проверить время окончания аукциона со ставками.",
            )
        if timezone.is_naive(end_time) or end_time <= timezone.now() + timedelta(
            hours=12
        ):
            return (
                "blocked",
                "Аукцион со ставками нельзя завершить в последние 12 часов.",
            )
    return "ready", ""


def _reconcile(token, item_id):
    try:
        status = get_item_state(token, item_id)
    except Exception:
        return "uncertain", "Ответ eBay не удалось сверить."
    if status in {"Completed", "Ended"}:
        return "ended", "eBay подтвердил, что объявление больше не активно."
    return "uncertain", "Результат запроса eBay неясен; не отправляли повторный запрос."


def _send_batch(token, ready, results, job):
    item_ids = [row["item_id"] for row in ready]
    if not item_ids:
        return
    result_by_id = {row["item_id"]: row for row in results}
    for item_id in item_ids:
        result_by_id[item_id]["status"] = "sending"
        result_by_id[item_id]["message"] = "Запрос отправлен в eBay."
    job["updated_at"] = timezone.now().isoformat()
    save_job(job)
    try:
        responses = end_items(token, item_ids)
    except EBayTransportError as error:
        for item_id in item_ids:
            result = result_by_id[item_id]
            result["status"], result["message"] = _reconcile(token, item_id)
            if result["status"] == "uncertain":
                result["message"] += " " + str(error)
            result["finished_at"] = timezone.now().isoformat()
        return
    missing = [item_id for item_id in item_ids if item_id not in responses]
    reconciled = {item_id: _reconcile(token, item_id) for item_id in missing}
    for item_id in item_ids:
        response = responses.get(item_id)
        if response is None:
            if item_id in reconciled:
                result_by_id[item_id]["status"], result_by_id[item_id]["message"] = (
                    reconciled[item_id]
                )
            else:
                result_by_id[item_id]["status"] = "uncertain"
                result_by_id[item_id]["message"] = (
                    "eBay не вернул результат для этого объявления; проверьте его вручную."
                )
        elif response.get("end_time"):
            result_by_id[item_id]["status"] = "ended"
            if (
                response.get("severity_code")
                or response.get("error_code")
                or response.get("ack") == "Warning"
            ):
                result_by_id[item_id]["message"] = (
                    response.get("message")
                    or "eBay завершил объявление и вернул предупреждение."
                )
            else:
                result_by_id[item_id]["message"] = "eBay завершил объявление."
            result_by_id[item_id]["end_time"] = response.get("end_time", "")
        elif response.get("classification") == "SystemError":
            result_by_id[item_id]["status"] = "uncertain"
            result_by_id[item_id]["message"] = response["message"]
        elif response.get("severity_code") == "Error" or response.get("error_code"):
            result_by_id[item_id]["status"] = "failed"
            result_by_id[item_id]["message"] = response["message"]
        else:
            result_by_id[item_id]["status"] = "uncertain"
            result_by_id[item_id]["message"] = (
                "eBay не подтвердил время завершения объявления; проверьте его вручную."
            )
        result_by_id[item_id]["error_code"] = (
            response.get("error_code", "") if response else ""
        )
        result_by_id[item_id]["finished_at"] = timezone.now().isoformat()


def _cancel_end_job(job):
    for result in job.get("results", []):
        if result["status"] == "sending":
            result["status"] = "uncertain"
            result["message"] = (
                "Пачка уже могла быть отправлена; проверьте объявление в eBay."
            )
            result["finished_at"] = timezone.now().isoformat()
        elif result["status"] in {"queued", "checking"}:
            result["status"] = "cancelled"
            result["message"] = "Остановлено до отправки следующей пачки."
            result["finished_at"] = timezone.now().isoformat()
    job["status"] = "cancelled"
    job["message"] = (
        "Задание остановлено; отправленная пачка могла быть обработана eBay."
    )
    job["finished_at"] = timezone.now().isoformat()
    job["updated_at"] = job["finished_at"]


def run_end_chunk(job_id):
    job = load_job(job_id)
    if not job or job["kind"] != "end" or job["status"] not in {"ending", "partial"}:
        return
    try:
        if not acquire_account_lock(job["environment"], job["account"], job["id"]):
            job["status"] = "partial" if job["progress"]["current"] else "failed"
            job["message"] = "Для этого аккаунта уже выполняется другое завершение."
            finished_at = timezone.now().isoformat()
            for result in job.get("results", []):
                if result["status"] in {"queued", "checking"}:
                    result["status"] = "blocked"
                    result["message"] = job["message"]
                    result["finished_at"] = finished_at
                elif result["status"] == "sending":
                    result["status"] = "uncertain"
                    result["message"] = (
                        "Запрос мог быть обработан eBay; проверьте объявление вручную."
                    )
                    result["finished_at"] = finished_at
            job["finished_at"] = finished_at
            job["updated_at"] = finished_at
            save_job(job)
            return
        connection, token = access_context(job["account"], job["environment"])
        seller_id = str(connection.ebay_user_id or "")
        if (
            not job.get("seller_id")
            or seller_id.casefold() != job["seller_id"].casefold()
        ):
            raise OwnershipMismatchError(
                "Подключённый eBay аккаунт отличается от аккаунта предпросмотра."
            )
        if cancellation_requested(job["id"]):
            _cancel_end_job(job)
            save_job(job)
            release_account_lock(job["environment"], job["account"], job["id"])
            return
        offset = int(job.get("next_offset", 0))
        batch = job["selected"][offset : offset + END_ITEMS_PER_TASK]
        results_by_id = {row["item_id"]: row for row in job["results"]}
        ready = []
        for selected in batch:
            result = results_by_id[selected["item_id"]]
            if result["status"] == "sending":
                result["status"], result["message"] = _reconcile(
                    token, selected["item_id"]
                )
                continue
            if result["status"] in {
                "uncertain",
                "ended",
                "failed",
                "blocked",
                "changed",
                "not_active",
                "not_found",
                "already_ended",
                "ownership_mismatch",
                "cancelled",
            }:
                continue
            result["status"] = "checking"
            result["message"] = "Проверяем объявление перед завершением."
            job["updated_at"] = timezone.now().isoformat()
            save_job(job)
            try:
                status, message = _verify_item(token, selected, job)
            except EBayTransportError:
                status, message = (
                    "uncertain",
                    "Не удалось проверить объявление перед завершением.",
                )
            except BulkEndError as error:
                status, message = "uncertain", str(error)
            if status == "ready":
                ready.append(selected)
            else:
                result["status"] = status
                result["message"] = message
                result["finished_at"] = timezone.now().isoformat()
        if cancellation_requested(job["id"]):
            _cancel_end_job(job)
            save_job(job)
            release_account_lock(job["environment"], job["account"], job["id"])
            return
        _send_batch(token, ready, job["results"], job)
        job["next_offset"] = offset + len(batch)
        job["progress"] = {"current": job["next_offset"], "total": len(job["selected"])}
        job["updated_at"] = timezone.now().isoformat()
        if cancellation_requested(job["id"]):
            _cancel_end_job(job)
            save_job(job)
            release_account_lock(job["environment"], job["account"], job["id"])
            return
        save_job(job)
        if job["next_offset"] < len(job["selected"]):
            return True
        statuses = {result["status"] for result in job["results"]}
        if statuses.intersection({"uncertain", "sending", "checking"}):
            job["status"] = "uncertain"
        elif statuses.intersection(
            {
                "failed",
                "blocked",
                "changed",
                "not_active",
                "not_found",
                "already_ended",
                "ownership_mismatch",
            }
        ):
            job["status"] = "partial" if "ended" in statuses else "failed"
        else:
            job["status"] = "complete"
        job["message"] = ""
        job["updated_at"] = timezone.now().isoformat()
        job["finished_at"] = job["updated_at"]
        for result in job["results"]:
            if not result.get("finished_at"):
                result["finished_at"] = job["finished_at"]
        save_job(job)
        release_account_lock(job["environment"], job["account"], job["id"])
    except OwnershipMismatchError as error:
        job["status"] = (
            "partial" if job.get("progress", {}).get("current") else "failed"
        )
        job["message"] = str(error)
        for result in job.get("results", []):
            if result["status"] in {"queued", "checking"}:
                result["status"] = "ownership_mismatch"
                result["message"] = str(error)
                result["finished_at"] = timezone.now().isoformat()
            elif result["status"] == "sending":
                result["status"] = "uncertain"
                result["message"] = (
                    "Запрос мог быть обработан eBay; проверьте объявление вручную."
                )
                result["finished_at"] = timezone.now().isoformat()
        job["finished_at"] = timezone.now().isoformat()
        job["updated_at"] = job["finished_at"]
        save_job(job)
        release_account_lock(job["environment"], job["account"], job["id"])
    except BulkEndError as error:
        job["status"] = (
            "partial" if job.get("progress", {}).get("current") else "failed"
        )
        job["message"] = str(error)
        for result in job.get("results", []):
            if result["status"] in {"queued", "checking"}:
                result["status"] = "blocked"
                result["message"] = str(error)
                result["finished_at"] = timezone.now().isoformat()
            elif result["status"] == "sending":
                result["status"] = "uncertain"
                result["message"] = (
                    "Запрос мог быть обработан eBay; проверьте объявление вручную."
                )
                result["finished_at"] = timezone.now().isoformat()
        job["finished_at"] = timezone.now().isoformat()
        job["updated_at"] = job["finished_at"]
        save_job(job)
        release_account_lock(job["environment"], job["account"], job["id"])
    except Exception:
        job["status"] = (
            "partial" if job.get("progress", {}).get("current") else "failed"
        )
        job["message"] = "Завершение остановилось из-за внутренней ошибки."
        finished_at = timezone.now().isoformat()
        for result in job.get("results", []):
            if result["status"] in {"sending", "checking"}:
                result["status"] = "uncertain"
                result["message"] = (
                    "Задание прервалось во время обработки; проверьте объявление в eBay."
                )
                result["finished_at"] = finished_at
            elif result["status"] == "queued":
                result["status"] = "failed"
                result["message"] = job["message"]
                result["finished_at"] = finished_at
        job["finished_at"] = finished_at
        job["updated_at"] = finished_at
        save_job(job)
        release_account_lock(job["environment"], job["account"], job["id"])


def report_rows(job):
    results_by_id = {row["item_id"]: row for row in job.get("results", [])}
    matched_eans_by_id = {
        listing["item_id"]: [match["ean"] for match in listing.get("matches", [])]
        for listing in job.get("matches", [])
    }
    rows = []
    for result in job.get("results", []):
        rows.append(
            {
                "eans": result.get(
                    "eans", matched_eans_by_id.get(result["item_id"], [])
                ),
                "item_id": result["item_id"],
                "title": result.get("title", ""),
                "status": result.get("status", ""),
                "error_code": result.get("error_code", ""),
                "message": result.get("message", ""),
                "end_time": result.get("end_time", ""),
                "finished_at": result.get("finished_at", "")
                or job.get("finished_at", ""),
            }
        )
    selected_ids = set(results_by_id)
    for listing in job.get("matches", []):
        if listing["item_id"] not in selected_ids:
            rows.append(
                {
                    "eans": matched_eans_by_id[listing["item_id"]],
                    "item_id": listing["item_id"],
                    "title": listing.get("title", ""),
                    "status": "not_selected",
                    "error_code": "",
                    "message": "Объявление найдено, но не выбрано для завершения.",
                    "end_time": "",
                    "finished_at": job.get("finished_at", ""),
                }
            )
    for ean in job.get("unmatched_eans", []):
        rows.append(
            {
                "eans": [ean],
                "item_id": "",
                "title": "",
                "status": "not_found",
                "error_code": "",
                "message": "Нет активного объявления с точным совпадением EAN.",
                "end_time": "",
                "finished_at": job.get("finished_at", ""),
            }
        )
    return rows


def csv_content(job):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        (
            "Original EANs",
            "Account",
            "Item ID",
            "Title",
            "Status",
            "Error code",
            "Error message",
            "eBay end time",
            "Finished at",
        )
    )
    for row in report_rows(job):
        writer.writerow(
            tuple(
                safe_csv_cell(value)
                for value in (
                    ", ".join(row.get("eans", [])),
                    job.get("account", "").upper(),
                    row.get("item_id"),
                    row.get("title"),
                    row.get("status"),
                    row.get("error_code"),
                    row.get("message"),
                    row.get("end_time"),
                    row.get("finished_at"),
                )
            )
        )
    return output.getvalue()
