from django.urls import path

from .views import (
    DeviceTokenDeactivateView,
    DeviceTokenRegisterView,
    NotificationListView,
    NotificationReadAllView,
    NotificationReadView,
)

app_name = "notifications"

urlpatterns = [
    path(
        "devices/",
        DeviceTokenRegisterView.as_view(),
        name="device-register",
    ),
    path(
        "devices/deactivate/",
        DeviceTokenDeactivateView.as_view(),
        name="device-deactivate",
    ),
    path(
        "",
        NotificationListView.as_view(),
        name="notification-list",
    ),
    path(
        "read-all/",
        NotificationReadAllView.as_view(),
        name="notification-read-all",
    ),
    path(
        "<int:notification_pk>/read/",
        NotificationReadView.as_view(),
        name="notification-read",
    ),
]