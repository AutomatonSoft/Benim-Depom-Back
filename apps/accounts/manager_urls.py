from django.urls import path

from .views import ManagerCreateView, ManagerSellerListView

app_name = "manager_users"

urlpatterns = [
    path("sellers/", ManagerSellerListView.as_view(), name="seller-list"),
    path("", ManagerCreateView.as_view(), name="manager-create"),
]
