from django.urls import path

from .catalog_views import EbayCategoriesView, EbayCategoryAspectsView
from .drafts import EbayDraftView
from .views import (
    EbayAccountDeletionView,
    EbayAuthorizationCallbackView,
    EbayAuthorizationStartView,
    EbayConnectionListView,
    EbayReadinessView,
)

app_name = "ebay"
urlpatterns = [
    path("oauth/start/", EbayAuthorizationStartView.as_view(), name="oauth-start"),
    path(
        "oauth/callback/",
        EbayAuthorizationCallbackView.as_view(),
        name="oauth-callback",
    ),
    path("connections/", EbayConnectionListView.as_view(), name="connections"),
    path("readiness/", EbayReadinessView.as_view(), name="readiness"),
    path("drafts/jv/<int:product_id>/", EbayDraftView.as_view(), name="draft-jv"),
    path("catalog/categories/", EbayCategoriesView.as_view(), name="categories"),
    path(
        "catalog/categories/<str:category_id>/aspects/",
        EbayCategoryAspectsView.as_view(),
        name="category-aspects",
    ),
    path(
        "account-deletion/", EbayAccountDeletionView.as_view(), name="account-deletion"
    ),
]
