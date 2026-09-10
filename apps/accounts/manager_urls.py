from django.urls import path

from .views import (
    ManagerCreateView,
    ManagerSellerConfirmEmailView,
    ManagerSellerDeleteView,
    ManagerSellerListView,
    ManagerStaffListView,
)

app_name = "manager_users"

urlpatterns = [
    path("sellers/", ManagerSellerListView.as_view(), name="seller-list"),
    path("managers/", ManagerStaffListView.as_view(), name="manager-list"),
    path(
        "sellers/<int:user_id>/confirm-email/",
        ManagerSellerConfirmEmailView.as_view(),
        name="seller-confirm-email",
    ),
    path(
        "sellers/<int:user_id>/",
        ManagerSellerDeleteView.as_view(),
        name="seller-delete",
    ),
    path("", ManagerCreateView.as_view(), name="manager-create"),
]
