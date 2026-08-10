from django.urls import path

from .views import (
    CategoryListView,
    ColorListView,
    MaterialListView,
    ProductTypeListView,
)

app_name = "catalog"


urlpatterns = [
    path("categories/", CategoryListView.as_view(), name="categories"),
    path("types/", ProductTypeListView.as_view(), name="product-types"),
    path("materials/", MaterialListView.as_view(), name="materials"),
    path("colors/", ColorListView.as_view(), name="colors"),
]