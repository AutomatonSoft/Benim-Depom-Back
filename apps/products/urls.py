from django.urls import path

from .views import (
    ProductDetailView,
    ProductImageDeleteView,
    ProductImagePrimaryView,
    ProductImageReorderView,
    ProductImageUploadView,
    ProductListCreateView,
    ProductAvailabilityView,
)

app_name = "products"

urlpatterns = [
    path("", ProductListCreateView.as_view(), name="product-list-create"),
    path("<int:pk>/", ProductDetailView.as_view(), name="product-detail"),
    path(
        "<int:product_pk>/images/",
        ProductImageUploadView.as_view(),
        name="product-image-upload",
    ),
    path(
        "<int:product_pk>/images/reorder/",
        ProductImageReorderView.as_view(),
        name="product-image-reorder",
    ),
    path(
        "<int:product_pk>/images/<int:image_pk>/",
        ProductImageDeleteView.as_view(),
        name="product-image-delete",
    ),
    path(
        "<int:product_pk>/images/<int:image_pk>/make-primary/",
        ProductImagePrimaryView.as_view(),
        name="product-image-make-primary",
    ),
    path(
        "<int:product_pk>/availability/",
        ProductAvailabilityView.as_view(),
        name="product-availability",
    ),
]