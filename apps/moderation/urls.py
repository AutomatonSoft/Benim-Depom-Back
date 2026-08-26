from django.urls import path

from .views import (
    ManagerApproveProductView,
    ManagerDeactivateProductView,
    ManagerProductListView,
    ManagerRejectProductView,
    ManagerRequestProductAvailabilityView,
    ManagerSendProductNotificationView,
    ProductModerationHistoryView,
)

app_name = "moderation"

urlpatterns = [
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
    path(
        "manager/products/<int:product_pk>/notifications/",
        ManagerSendProductNotificationView.as_view(),
        name="manager-product-notification",
    ),
    path(
        "manager/products/<int:product_pk>/availability-request/",
        ManagerRequestProductAvailabilityView.as_view(),
        name="manager-product-availability-request",
    ),
    path(
        "manager/products/<int:product_pk>/deactivate/",
        ManagerDeactivateProductView.as_view(),
        name="manager-product-deactivate",
    ),
]
