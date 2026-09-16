from django.urls import path

from .views import (
    ProductAvailabilityView,
    ProductDeactivateView,
    ProductDetailView,
    ProductGeneratedImageDeleteView,
    ProductImageDeleteView,
    ProductImagePrimaryView,
    ProductImageProcessView,
    ProductImageReorderView,
    ProductImageUploadView,
    ProductListCreateView,
    ProductPriceNegotiationHistoryView,
    ProductPriceNegotiationRespondView,
    ProductWithdrawView,
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
        "<int:product_pk>/images/<int:image_pk>/generated/<int:generated_pk>/",
        ProductGeneratedImageDeleteView.as_view(),
        name="product-generated-image-delete",
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
    path(
        "<int:product_pk>/price-negotiation/respond/",
        ProductPriceNegotiationRespondView.as_view(),
        name="product-price-negotiation-respond",
    ),
    path(
        "<int:product_pk>/price-negotiations/",
        ProductPriceNegotiationHistoryView.as_view(),
        name="product-price-negotiation-history",
    ),
    path(
        "<int:product_pk>/images/<int:image_pk>/process/",
        ProductImageProcessView.as_view(),
        name="product-image-process",
    ),
    path(
        "<int:product_pk>/deactivate/",
        ProductDeactivateView.as_view(),
        name="product-deactivate",
    ),
    path(
        "<int:product_pk>/withdraw/",
        ProductWithdrawView.as_view(),
        name="product-withdraw",
    ),
]
