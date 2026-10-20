from django.urls import path

from .catalog_views import EbayCategoriesView, EbayCategoryAspectsView
from .drafts import EbayDraftView
from .preparation import EbayDraftPreparationView
from .preview import EbayDraftPreviewView
from .updating import EbayDraftUpdateView
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
    path(
        "drafts/jv/<int:product_id>/preview/",
        EbayDraftPreviewView.as_view(),
        name="draft-jv-preview",
    ),
    path("catalog/categories/", EbayCategoriesView.as_view(), name="categories"),
    path(
        "drafts/jv/<int:product_id>/update/",
        EbayDraftUpdateView.as_view(),
        name="draft-jv-update",
    ),
    path(
        "drafts/jv/<int:product_id>/prepare/",
        EbayDraftPreparationView.as_view(),
        name="draft-jv-prepare",
    ),
    path(
        "catalog/categories/<str:category_id>/aspects/",
        EbayCategoryAspectsView.as_view(),
        name="category-aspects",
    ),
    path(
        "account-deletion/", EbayAccountDeletionView.as_view(), name="account-deletion"
    ),
]
