import csv
import io
from datetime import datetime
from uuid import UUID

from django.conf import settings
from django.core.paginator import EmptyPage, Paginator
from django.db.models import Q, TextField
from django.db.models.functions import Cast
from django.http import Http404, HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import IsManager
from apps.common.throttles import ManagerMutationThrottleMixin

from .auth import connection_for, connection_status, current_environment
from .domain import MAX_EANS, safe_csv_cell
from .errors import BulkEndError, IdentifierConflictError, ManagedListingError
from .models import EbayBulkEndJob
from .serializers import ConfirmSerializer, ScanSerializer
from .storage import (
    cancellation_requested,
    load_job,
    redis_client,
    request_cancellation,
)
from .workflows import confirm_job, csv_content, new_job, report_rows, start_scan


class ManagerAPI(APIView):
    permission_classes = (IsAuthenticated, IsManager)


def _error(message, status_code=status.HTTP_400_BAD_REQUEST):
    return Response({"detail": message}, status=status_code)


def _owned_job(request, job_id):
    job = load_job(job_id)
    if not job or str(job.get("manager_id")) != str(request.user.pk):
        raise Http404
    return job


class ConnectionsView(ManagerAPI):
    def get(self, request):
        try:
            return Response({**connection_status(), "max_eans_per_scan": MAX_EANS})
        except BulkEndError as error:
            return _error(str(error), status.HTTP_503_SERVICE_UNAVAILABLE)


class ScanView(ManagerMutationThrottleMixin, ManagerAPI):
    def post(self, request):
        serializer = ScanSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        account = serializer.validated_data["account"]
        try:
            environment = current_environment()
            if connection_for(account, environment) is None:
                return _error(
                    "Подключите выбранный eBay аккаунт.", status.HTTP_409_CONFLICT
                )
            job = new_job(
                account=account,
                environment=environment,
                manager_id=request.user.pk,
                eans=serializer.validated_data["eans"],
            )
            start_scan(job)
            return Response(
                {"job_id": job["id"], "status": job["status"]},
                status=status.HTTP_202_ACCEPTED,
            )
        except BulkEndError as error:
            return _error(str(error), status.HTTP_503_SERVICE_UNAVAILABLE)


class JobView(ManagerAPI):
    def get(self, request, job_id):
        try:
            job = _owned_job(request, job_id)
            cancel_requested = (
                cancellation_requested(job_id) if job.get("kind") == "end" else False
            )
        except BulkEndError as error:
            return _error(str(error), status.HTTP_503_SERVICE_UNAVAILABLE)
        matched_eans = {
            match["ean"]
            for row in job.get("matches", [])
            for match in row.get("matches", [])
        }
        results = job.get("results", [])
        ended_results = [row for row in results if row.get("status") == "ended"]
        unknown_statuses = {"uncertain", "sending", "checking"}
        unknown_results = [
            row for row in results if row.get("status") in unknown_statuses
        ]
        failed_results = [
            row
            for row in results
            if row.get("status") != "ended"
            and row.get("status") not in unknown_statuses
        ]
        ended_eans = {ean for row in ended_results for ean in row.get("eans", [])}
        selected_item_ids = {row.get("item_id") for row in results}
        response = {
            "job_id": job["id"],
            "kind": job["kind"],
            "account": job["account"],
            "environment": job["environment"],
            "status": job["status"],
            "progress": job.get("progress", {}),
            "message": job.get("message", ""),
            "created_at": job.get("created_at"),
            "updated_at": job.get("updated_at"),
            "finished_at": job.get("finished_at"),
            "cancel_requested": cancel_requested,
            "summary": {
                "submitted_ean_count": len(job.get("eans", [])),
                "listing_count": len(job.get("matches", [])),
                "matched_ean_count": len(matched_eans),
                "eligible_count": sum(
                    not row.get("risky")
                    and not row.get("auction_risky")
                    and not row.get("managed_by_benim")
                    and not row.get("identifier_conflict")
                    for row in job.get("matches", [])
                ),
                "risky_count": sum(
                    bool(
                        (
                            row.get("risky")
                            or row.get("auction_risky")
                            or row.get("identifier_conflict")
                        )
                        and not row.get("managed_by_benim")
                    )
                    for row in job.get("matches", [])
                ),
                "managed_listing_count": sum(
                    bool(row.get("managed_by_benim")) for row in job.get("matches", [])
                ),
                "ended_item_count": len(ended_results),
                "ended_ean_count": len(ended_eans),
                "failed_item_count": len(failed_results),
                "not_found_ean_count": len(job.get("unmatched_eans", [])),
                "uncertain_item_count": len(unknown_results),
                "not_selected_item_count": sum(
                    row.get("item_id") not in selected_item_ids
                    for row in job.get("matches", [])
                )
                if job.get("kind") == "end"
                else 0,
            },
            "preview_expires_at": job.get("preview_expires_at"),
            "discovery_complete": job.get("discovery_complete", False),
            "discovery_strategy": job.get("discovery_strategy", ""),
            "discovery_details": job.get("discovery_details", {}),
            "unmatched_status": job.get("unmatched_status", "not_found"),
            "unmatched_eans": job.get("unmatched_eans", []),
            "unmatched_status_by_ean": job.get("unmatched_status_by_ean", {}),
            "matches": job.get("matches", []),
            "results": job.get("results", []),
            "report_rows": report_rows(job) if job.get("kind") == "end" else [],
        }
        expiry = job.get("preview_expires_at")
        if job["status"] == "preview" and expiry:
            try:
                if datetime.fromisoformat(expiry) <= timezone.now():
                    response["status"] = "expired"
                    response["matches"] = []
                    response["message"] = "Предпросмотр истёк. Запустите поиск заново."
            except ValueError:
                response["status"] = "expired"
                response["matches"] = []
                response["message"] = "Предпросмотр больше нельзя подтвердить."
        return Response(response)


class ConfirmView(ManagerMutationThrottleMixin, ManagerAPI):
    def post(self, request, job_id):
        serializer = ConfirmSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        try:
            lock = redis_client().lock(
                "ebay-bulk-end:v1:confirm-lock:" + str(UUID(str(job_id))),
                timeout=20,
                blocking_timeout=0,
            )
            if not lock.acquire(blocking=False):
                return _error(
                    "Подтверждение уже обрабатывается.", status.HTTP_409_CONFLICT
                )
            try:
                job = _owned_job(request, job_id)
                if job.get("kind") != "scan" or job.get("status") != "preview":
                    return _error(
                        "Это задание нельзя подтвердить.", status.HTTP_409_CONFLICT
                    )
                if not job.get("discovery_complete"):
                    return _error(
                        "Поиск не завершён; подтверждение недоступно.",
                        status.HTTP_409_CONFLICT,
                    )
                if datetime.fromisoformat(job["preview_expires_at"]) <= timezone.now():
                    return _error(
                        "Предпросмотр истёк. Запустите поиск заново.",
                        status.HTTP_410_GONE,
                    )
                if serializer.validated_data["typed_account"] != job["account"].upper():
                    return _error("Введите название выбранного аккаунта.")
                available = {row["item_id"]: row for row in job.get("matches", [])}
                selected_ids = serializer.validated_data["item_ids"]
                if any(item_id not in available for item_id in selected_ids):
                    return _error(
                        "Выбраны объявления вне предпросмотра.",
                        status.HTTP_409_CONFLICT,
                    )
                selected = [available[item_id] for item_id in selected_ids]
                if (
                    any(row.get("risky") for row in selected)
                    and not serializer.validated_data["include_variations"]
                ):
                    return _error(
                        "Для объявлений с вариантами требуется отдельное подтверждение."
                    )
                if (
                    any(row.get("auction_risky") for row in selected)
                    and not serializer.validated_data["include_auction_risk"]
                ):
                    return _error(
                        "Для аукционов с возможными ставками требуется отдельное подтверждение."
                    )
                if not serializer.validated_data["confirm_unavailable_reason"]:
                    return _error(
                        "Подтвердите, что причина «товар недоступен» подходит выбранным объявлениям."
                    )
                if (
                    not job.get("environment")
                    or settings.EBAY_ENVIRONMENT != job["environment"]
                ):
                    return _error(
                        "Среда eBay изменилась. Запустите поиск заново.",
                        status.HTTP_409_CONFLICT,
                    )
                confirm_job(
                    job,
                    selected_ids,
                    serializer.validated_data["include_variations"],
                    serializer.validated_data["include_auction_risk"],
                    serializer.validated_data["confirm_unavailable_reason"],
                )
                return Response(
                    {"job_id": job["id"], "status": job["status"]},
                    status=status.HTTP_202_ACCEPTED,
                )
            finally:
                try:
                    lock.release()
                except Exception:
                    pass
        except Http404:
            raise
        except (ManagedListingError, IdentifierConflictError) as error:
            return _error(str(error), status.HTTP_409_CONFLICT)
        except BulkEndError as error:
            return _error(str(error), status.HTTP_503_SERVICE_UNAVAILABLE)
        except (ValueError, KeyError):
            return _error(
                "Предпросмотр больше нельзя подтвердить.", status.HTTP_410_GONE
            )


class JobCSVView(ManagerAPI):
    def get(self, request, job_id):
        try:
            job = _owned_job(request, job_id)
        except BulkEndError as error:
            return _error(str(error), status.HTTP_503_SERVICE_UNAVAILABLE)
        if job.get("kind") != "end" or not job.get("results"):
            return _error("Для этого задания ещё нет отчёта.", status.HTTP_409_CONFLICT)
        if job.get("status") in {"queued", "scanning", "ending"}:
            return _error(
                "Отчёт появится после завершения задания.", status.HTTP_409_CONFLICT
            )
        response = HttpResponse(
            csv_content(job), content_type="text/csv; charset=utf-8"
        )
        response["Content-Disposition"] = (
            'attachment; filename="ebay-bulk-end-' + job["id"] + '.csv"'
        )
        return response


class CancelView(ManagerMutationThrottleMixin, ManagerAPI):
    def post(self, request, job_id):
        try:
            job = _owned_job(request, job_id)
            if job.get("kind") != "end" or job.get("status") != "ending":
                return _error(
                    "Это задание уже нельзя остановить.", status.HTTP_409_CONFLICT
                )
            request_cancellation(job_id)
            return Response(
                {"job_id": job["id"], "status": "cancelling"},
                status=status.HTTP_202_ACCEPTED,
            )
        except Http404:
            raise
        except BulkEndError as error:
            return _error(str(error), status.HTTP_503_SERVICE_UNAVAILABLE)


def _owned_history_job(request, job_id):
    try:
        return EbayBulkEndJob.objects.select_related("created_by").get(
            pk=job_id, created_by_id=request.user.pk
        )
    except EbayBulkEndJob.DoesNotExist as error:
        raise Http404 from error


def _page(queryset, request):
    try:
        number = max(1, int(request.query_params.get("page", "1")))
        size = min(100, max(1, int(request.query_params.get("page_size", "25"))))
    except (TypeError, ValueError):
        return None, _error("Параметры страницы должны быть целыми числами.")
    paginator = Paginator(queryset, size)
    try:
        return paginator.page(number), None
    except EmptyPage:
        return paginator.page(paginator.num_pages), None


def _manager_name(user):
    return user.get_full_name().strip() or user.email


def _history_summary(job):
    return {
        "submitted_ean_count": len(job.input_eans or []),
        "unique_ean_count": job.unique_ean_count,
        "matched_item_count": job.matched_item_count,
        "selected_item_count": job.selected_item_count,
        "ended_count": job.ended_count,
        "failed_count": job.failed_count,
        "uncertain_count": job.uncertain_count,
        "not_found_count": job.not_found_count,
    }


def _history_row(job):
    return {
        "job_id": str(job.pk),
        "account": job.account,
        "environment": job.environment,
        "manager": _manager_name(job.created_by),
        "manager_id": job.created_by_id,
        "status": job.status,
        "created_at": job.created_at,
        "finished_at": job.finished_at,
        **_history_summary(job),
    }


class HistoryListView(ManagerAPI):
    def get(self, request):
        jobs = EbayBulkEndJob.objects.filter(
            created_by_id=request.user.pk
        ).select_related("created_by")
        for parameter, field in (
            ("account", "account"),
            ("environment", "environment"),
            ("status", "status"),
        ):
            value = request.query_params.get(parameter)
            if value:
                jobs = jobs.filter(**{field: value})
        result = request.query_params.get("result")
        if result == "success":
            jobs = jobs.filter(ended_count__gt=0)
        elif result == "failure":
            jobs = jobs.filter(failed_count__gt=0)
        elif result == "uncertain":
            jobs = jobs.filter(uncertain_count__gt=0)
        manager = request.query_params.get("manager")
        if manager and manager not in {str(request.user.pk), request.user.email}:
            jobs = jobs.none()
        for parameter, lookup in (("date_from", "gte"), ("date_to", "lte")):
            value = request.query_params.get(parameter)
            if value:
                try:
                    parsed = parse_date(value)
                except ValueError:
                    parsed = None
                if parsed is None:
                    return _error(f"Параметр {parameter} должен быть датой YYYY-MM-DD.")
                jobs = jobs.filter(**{f"created_at__date__{lookup}": parsed})
        query = request.query_params.get("q", "").strip()[:255]
        if query:
            filters = (
                Q(input_eans_text__icontains=query)
                | Q(ebay_seller_id__icontains=query)
                | Q(items__item_id__icontains=query)
                | Q(items__sku__icontains=query)
            )
            try:
                filters |= Q(pk=UUID(query))
            except (TypeError, ValueError):
                pass
            jobs = (
                jobs.annotate(input_eans_text=Cast("input_eans", TextField()))
                .filter(filters)
                .distinct()
            )
        page, error = _page(jobs.order_by("-created_at"), request)
        if error:
            return error
        return Response(
            {
                "count": page.paginator.count,
                "page": page.number,
                "page_size": page.paginator.per_page,
                "pages": page.paginator.num_pages,
                "results": [_history_row(job) for job in page.object_list],
            }
        )


class HistoryDetailView(ManagerAPI):
    def get(self, request, job_id):
        job = _owned_history_job(request, job_id)
        return Response(
            {
                **_history_row(job),
                "seller_id": job.ebay_seller_id,
                "kind": job.kind,
                "discovery_strategy": job.discovery_strategy,
                "discovery_complete": job.discovery_complete,
                "discovery_details": job.discovery_details,
                "input_eans": job.input_eans,
                "unmatched_eans": job.workflow_state.get("unmatched_eans", []),
                "unmatched_status_by_ean": job.workflow_state.get(
                    "unmatched_status_by_ean", {}
                ),
                "progress": job.progress,
                "last_error": job.last_error,
                "cancel_requested": job.cancel_requested,
                "timestamps": {
                    "scan_started_at": job.scan_started_at,
                    "scan_finished_at": job.scan_finished_at,
                    "confirmed_at": job.confirmed_at,
                    "execution_started_at": job.execution_started_at,
                    "finished_at": job.finished_at,
                },
            }
        )


class HistoryItemsView(ManagerAPI):
    def get(self, request, job_id):
        job = _owned_history_job(request, job_id)
        page, error = _page(job.items.order_by("selection_order", "item_id"), request)
        if error:
            return error
        return Response(
            {
                "count": page.paginator.count,
                "page": page.number,
                "page_size": page.paginator.per_page,
                "pages": page.paginator.num_pages,
                "results": [
                    {
                        "item_id": item.item_id,
                        "eans": item.matched_eans,
                        "sku": item.sku,
                        "title": (item.details.get("match") or {}).get("title", ""),
                        "match_type": item.match_type,
                        "status": item.status,
                        "selected": item.selected,
                        "error_code": item.ebay_error_code,
                        "error_message": item.ebay_error_message,
                        "ended_at": item.remote_end_time,
                        "processed_at": item.finished_at,
                    }
                    for item in page.object_list
                ],
            }
        )


class HistoryEventsView(ManagerAPI):
    def get(self, request, job_id):
        job = _owned_history_job(request, job_id)
        page, error = _page(
            job.events.select_related("actor_user", "item").order_by(
                "-created_at", "-id"
            ),
            request,
        )
        if error:
            return error
        return Response(
            {
                "count": page.paginator.count,
                "page": page.number,
                "page_size": page.paginator.per_page,
                "pages": page.paginator.num_pages,
                "results": [
                    {
                        "event_type": event.event_type,
                        "created_at": event.created_at,
                        "actor": _manager_name(event.actor_user)
                        if event.actor_user
                        else "",
                        "item_id": event.item.item_id if event.item else "",
                        "metadata": event.metadata,
                    }
                    for event in page.object_list
                ],
            }
        )


class HistoryCSVView(ManagerAPI):
    def get(self, request, job_id):
        job = _owned_history_job(request, job_id)
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(
            (
                "EAN",
                "SKU",
                "ItemID",
                "Title",
                "Match type",
                "Result",
                "eBay error code",
                "Error",
                "Ended at",
                "Processed at",
            )
        )
        matched_eans = set()
        rows = job.items.order_by("item_id").values_list(
            "matched_eans",
            "sku",
            "item_id",
            "details",
            "match_type",
            "status",
            "ebay_error_code",
            "ebay_error_message",
            "remote_end_time",
            "finished_at",
        )
        for (
            eans,
            sku,
            item_id,
            details,
            match_type,
            item_status,
            code,
            message,
            ended_at,
            processed_at,
        ) in rows.iterator(chunk_size=500):
            matched_eans.update(eans or [])
            match = (details or {}).get("match") or {}
            writer.writerow(
                safe_csv_cell(value)
                for value in (
                    ", ".join(eans or []),
                    sku,
                    item_id,
                    match.get("title", ""),
                    match_type,
                    item_status,
                    code,
                    message,
                    ended_at.isoformat() if ended_at else "",
                    processed_at.isoformat() if processed_at else "",
                )
            )
        for ean in job.input_eans or []:
            if ean not in matched_eans:
                writer.writerow(
                    (
                        safe_csv_cell(ean),
                        "",
                        "",
                        "",
                        "",
                        job.workflow_state.get("unmatched_status_by_ean", {}).get(
                            ean,
                            "not_found_in_sku_index"
                            if job.discovery_complete
                            else "not_verified",
                        ),
                        "",
                        "",
                        "",
                        "",
                    )
                )
        response = HttpResponse(
            output.getvalue(), content_type="text/csv; charset=utf-8"
        )
        response["Content-Disposition"] = (
            f'attachment; filename="ebay-bulk-end-{job.pk}.csv"'
        )
        return response
