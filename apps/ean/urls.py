from django.urls import path

from .views import ManagerEanImportView, ManagerEanListView, ManagerEanSummaryView

app_name = "ean"

urlpatterns = [
    path("", ManagerEanListView.as_view(), name="ean-list"),
    path("import/", ManagerEanImportView.as_view(), name="ean-import"),
    path("summary/", ManagerEanSummaryView.as_view(), name="ean-summary"),
]
