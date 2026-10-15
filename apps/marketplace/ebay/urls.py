from django.urls import path

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
        "account-deletion/", EbayAccountDeletionView.as_view(), name="account-deletion"
    ),
]
