from django.urls import path

from .views import (
    CancelView,
    ConfirmView,
    ConnectionsView,
    HistoryCSVView,
    HistoryDetailView,
    HistoryEventsView,
    HistoryItemsView,
    HistoryListView,
    JobCSVView,
    JobView,
    ScanView,
)

urlpatterns = [
    path("connections/", ConnectionsView.as_view(), name="connections"),
    path("scans/", ScanView.as_view(), name="scans"),
    path("history/", HistoryListView.as_view(), name="history-list"),
    path("history/<uuid:job_id>/", HistoryDetailView.as_view(), name="history-detail"),
    path("history/<uuid:job_id>/items/", HistoryItemsView.as_view(), name="history-items"),
    path("history/<uuid:job_id>/events/", HistoryEventsView.as_view(), name="history-events"),
    path("history/<uuid:job_id>/csv/", HistoryCSVView.as_view(), name="history-csv"),
    path("jobs/<uuid:job_id>/", JobView.as_view(), name="job"),
    path("jobs/<uuid:job_id>/confirm/", ConfirmView.as_view(), name="confirm"),
    path("jobs/<uuid:job_id>/cancel/", CancelView.as_view(), name="cancel"),
    path("jobs/<uuid:job_id>/csv/", JobCSVView.as_view(), name="csv"),
]
