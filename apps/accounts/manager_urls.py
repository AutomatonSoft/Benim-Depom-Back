from django.urls import path

from .views import ManagerCreateView

app_name = "manager_users"

urlpatterns = [
    path("", ManagerCreateView.as_view(), name="manager-create"),
]
