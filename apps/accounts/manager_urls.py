from django.urls import path

from .views import ManagerCreateView, ManagerSellerDeleteView, ManagerSellerListView

app_name = "manager_users"

urlpatterns = [
    path("sellers/", ManagerSellerListView.as_view(), name="seller-list"),
    path(
        "sellers/<int:user_id>/",
        ManagerSellerDeleteView.as_view(),
        name="seller-delete",
    ),
    path("", ManagerCreateView.as_view(), name="manager-create"),
]
