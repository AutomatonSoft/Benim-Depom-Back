from django.urls import path

from .views import (
    CategoryListView,
    ProductTypeListView,
    ManagerCategoryDetailView,
    ManagerProductTypeDetailView,
)

app_name = "catalog"


urlpatterns = [
    path("categories/", CategoryListView.as_view(), name="categories"),
    path("types/", ProductTypeListView.as_view(), name="product-types"),
    path(
        "categories/<int:pk>/",
        ManagerCategoryDetailView.as_view(),
        name="manager-category-detail",
    ),
    path(
        "product-types/<int:pk>/",
        ManagerProductTypeDetailView.as_view(),
        name="manager-product-type-detail",
    ),
]
