from django.urls import path

from .catalog_views import EbayCategoriesView, EbayCategoryAspectsView
from .drafts import EbayDraftView
from .preparation import EbayDraftPreparationView
from .preview import EbayDraftPreviewView
from .publishing import EbayDraftPublishView
from .remote import EbayDraftRemoteView
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
    path(
        "drafts/depotum/<int:product_id>/",
        EbayDraftView.as_view(account="depotum"),
        name="draft-depotum",
    ),
    path(
        "drafts/depotum/<int:product_id>/preview/",
        EbayDraftPreviewView.as_view(account="depotum"),
        name="draft-depotum-preview",
    ),
    path(
        "drafts/depotum/<int:product_id>/prepare/",
        EbayDraftPreparationView.as_view(account="depotum"),
        name="draft-depotum-prepare",
    ),
    path(
        "drafts/depotum/<int:product_id>/update/",
        EbayDraftUpdateView.as_view(account="depotum"),
        name="draft-depotum-update",
    ),
    path(
        "drafts/depotum/<int:product_id>/remote/",
        EbayDraftRemoteView.as_view(account="depotum"),
        name="draft-depotum-remote",
    ),
    path(
        "drafts/depotum/<int:product_id>/publish/",
        EbayDraftPublishView.as_view(account="depotum"),
        name="draft-depotum-publish",
    ),
    path("drafts/jv/<int:product_id>/", EbayDraftView.as_view(), name="draft-jv"),
    path(
        "drafts/jv/<int:product_id>/preview/",
        EbayDraftPreviewView.as_view(),
        name="draft-jv-preview",
    ),
    path("catalog/categories/", EbayCategoriesView.as_view(), name="categories"),
    path(
        "drafts/jv/<int:product_id>/publish/",
        EbayDraftPublishView.as_view(),
        name="draft-jv-publish",
    ),
    path(
        "drafts/jv/<int:product_id>/remote/",
        EbayDraftRemoteView.as_view(),
        name="draft-jv-remote",
    ),
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
