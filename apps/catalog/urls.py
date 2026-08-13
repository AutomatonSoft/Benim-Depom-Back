from django.urls import path

from .views import (
    CategoryListView,
    ManagerCategoryDetailView,
)

app_name = "catalog"


urlpatterns = [
    path("categories/", CategoryListView.as_view(), name="categories"),
    path(
        "categories/<int:pk>/",
        ManagerCategoryDetailView.as_view(),
        name="manager-category-detail",
    ),
]
