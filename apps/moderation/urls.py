from django.urls import path

from .views import (
    ManagerApproveProductView,
    ManagerProductListView,
    ManagerRejectProductView,
    ProductModerationHistoryView,
    SubmitProductView,
)

app_name = "moderation"

urlpatterns = [
    path(
        "products/<int:product_pk>/submit/",
        SubmitProductView.as_view(),
        name="product-submit",
    ),
    path(
        "products/<int:product_pk>/moderation-history/",
        ProductModerationHistoryView.as_view(),
        name="product-moderation-history",
    ),
    path(
        "manager/products/",
        ManagerProductListView.as_view(),
        name="manager-product-list",
    ),
    path(
        "manager/products/<int:product_pk>/approve/",
        ManagerApproveProductView.as_view(),
        name="manager-product-approve",
    ),
    path(
        "manager/products/<int:product_pk>/reject/",
        ManagerRejectProductView.as_view(),
        name="manager-product-reject",
    ),
]