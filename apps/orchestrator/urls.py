from django.urls import path

from .views import MarketplaceJobDetailView, ProductMarketplaceJobCreateView

app_name = "orchestrator"

urlpatterns = [
    path(
        "products/<int:product_pk>/<str:operation>/",
        ProductMarketplaceJobCreateView.as_view(),
        name="product-marketplace-job-create",
    ),
    path("jobs/<uuid:job_id>/", MarketplaceJobDetailView.as_view(), name="job-detail"),
]
