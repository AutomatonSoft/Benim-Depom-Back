from django.urls import path

from .views import (
    CancelView,
    ConfirmView,
    ConnectionsView,
    JobCSVView,
    JobView,
    ScanView,
)

urlpatterns = [
    path("connections/", ConnectionsView.as_view(), name="connections"),
    path("scans/", ScanView.as_view(), name="scans"),
    path("jobs/<uuid:job_id>/", JobView.as_view(), name="job"),
    path("jobs/<uuid:job_id>/confirm/", ConfirmView.as_view(), name="confirm"),
    path("jobs/<uuid:job_id>/cancel/", CancelView.as_view(), name="cancel"),
    path("jobs/<uuid:job_id>/csv/", JobCSVView.as_view(), name="csv"),
]
