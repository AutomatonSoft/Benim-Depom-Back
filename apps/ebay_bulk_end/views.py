from datetime import datetime
from uuid import UUID

from django.conf import settings
from django.http import Http404, HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import IsManager
from apps.common.throttles import ManagerMutationThrottleMixin

from .auth import connection_for, connection_status, current_environment
from .domain import MAX_EANS
from .errors import BulkEndError, ManagedListingError
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
                    for row in job.get("matches", [])
                ),
                "risky_count": sum(
                    bool(
                        (row.get("risky") or row.get("auction_risky"))
                        and not row.get("managed_by_benim")
                    )
                    for row in job.get("matches", [])
                ),
                "managed_listing_count": sum(
                    bool(row.get("managed_by_benim"))
                    for row in job.get("matches", [])
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
            "unmatched_eans": job.get("unmatched_eans", []),
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
        except ManagedListingError as error:
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
