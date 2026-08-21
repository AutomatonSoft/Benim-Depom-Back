from django.urls import path

from .views import (
    MarketplaceJobDetailView,
    ProductMarketplaceJobCreateView,
    ProductMarketplaceListingStateView,
    ProductMarketplacePublicationListView,
    MarketplacePublicationListView,
    ProductOttoListingConfigurationView,
    ProductOttoPayloadPreviewView,
    ProductHoodListingConfigurationView,
    ProductHoodPayloadPreviewView,
    ProductKauflandListingConfigurationView,
    ProductKauflandCreatePayloadPreviewView,
    ProductKauflandUpdatePayloadPreviewView,
    MarketplaceContentGenerationDetailView,
    ProductMarketplaceContentGenerationApplyView,
    ProductMarketplaceContentGenerationCreateView,
)

app_name = "orchestrator"

urlpatterns = [
    path(
        "products/<int:product_pk>/listing-state/",
        ProductMarketplaceListingStateView.as_view(),
        name="product-marketplace-listing-state",
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
    path(
        "products/<int:product_pk>/hood/<str:account>/configuration/",
        ProductHoodListingConfigurationView.as_view(),
        name="product-hood-listing-configuration",
    ),
    path(
        "products/<int:product_pk>/hood/<str:account>/payload-preview/",
        ProductHoodPayloadPreviewView.as_view(),
        name="product-hood-payload-preview",
    ),
    path(
        "products/<int:product_pk>/kaufland/<str:account>/configuration/",
        ProductKauflandListingConfigurationView.as_view(),
        name="product-kaufland-listing-configuration",
    ),
    path(
        "products/<int:product_pk>/kaufland/<str:account>/create-payload-preview/",
        ProductKauflandCreatePayloadPreviewView.as_view(),
        name="product-kaufland-create-payload-preview",
    ),
    path(
        "products/<int:product_pk>/kaufland/<str:account>/update-payload-preview/",
        ProductKauflandUpdatePayloadPreviewView.as_view(),
        name="product-kaufland-update-payload-preview",
    ),
    path(
        "products/<int:product_pk>/ai-content/generate/",
        ProductMarketplaceContentGenerationCreateView.as_view(),
        name="product-ai-content-generate",
    ),
    path(
        "ai-content/generations/<uuid:generation_id>/",
        MarketplaceContentGenerationDetailView.as_view(),
        name="ai-content-generation-detail",
    ),
    path(
        "products/<int:product_pk>/ai-content/generations/"
        "<uuid:generation_id>/apply/",
        ProductMarketplaceContentGenerationApplyView.as_view(),
        name="product-ai-content-apply",
    ),
    # Keep this catch-all route after every explicit product sub-resource.
    # Otherwise `/products/<id>/publications/` is interpreted as operation
    # `publications` and Django returns 405 for its GET endpoint.
    path(
        "products/<int:product_pk>/<str:operation>/",
        ProductMarketplaceJobCreateView.as_view(),
        name="product-marketplace-job-create",
    ),
]
