from django.urls import path

from .views import (
    ManagerCreateView,
    ManagerRegistrationRequestListView,
    ManagerSellerDeleteView,
    ManagerSellerListView,
    ManagerSellerRegistrationApproveView,
    ManagerSellerRegistrationRejectView,
)

app_name = "manager_users"

urlpatterns = [
    path(
        "sellers/registration-requests/",
        ManagerRegistrationRequestListView.as_view(),
        name="seller-registration-requests",
    ),
    path(
        "sellers/<int:user_id>/approve/",
        ManagerSellerRegistrationApproveView.as_view(),
        name="seller-registration-approve",
    ),
    path(
        "sellers/<int:user_id>/reject/",
        ManagerSellerRegistrationRejectView.as_view(),
        name="seller-registration-reject",
    ),
    path("sellers/", ManagerSellerListView.as_view(), name="seller-list"),
    path(
        "sellers/<int:user_id>/",
        ManagerSellerDeleteView.as_view(),
        name="seller-delete",
    ),
    path("", ManagerCreateView.as_view(), name="manager-create"),
]