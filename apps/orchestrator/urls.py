from django.urls import path

from .views import (
    MarketplaceJobDetailView,
    ProductMarketplaceJobCreateView,
    ProductMarketplacePublicationListView,
    MarketplacePublicationListView,
    ProductOttoListingConfigurationView,
    ProductOttoPayloadPreviewView,
)

app_name = "orchestrator"

urlpatterns = [
    path(
        "products/<int:product_pk>/<str:operation>/",
        ProductMarketplaceJobCreateView.as_view(),
        name="product-marketplace-job-create",
    ),
    path("jobs/<uuid:job_id>/", MarketplaceJobDetailView.as_view(), name="job-detail"),
    path(
        "products/<int:product_pk>/publications/",
        ProductMarketplacePublicationListView.as_view(),
        name="product-marketplace-publications",
    ),
    path(
        "publications/",
        MarketplacePublicationListView.as_view(),
        name="marketplace-publication-list",
    ),
    path(
        "products/<int:product_pk>/otto/<str:account>/configuration/",
        ProductOttoListingConfigurationView.as_view(),
        name="product-otto-listing-configuration",
    ),
    path(
        "products/<int:product_pk>/otto/<str:account>/payload-preview/",
        ProductOttoPayloadPreviewView.as_view(),
        name="product-otto-payload-preview",
    ),
]
