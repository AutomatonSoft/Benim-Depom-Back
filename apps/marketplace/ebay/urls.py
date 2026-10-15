from django.urls import path

from .views import (
    EbayAccountDeletionView,
    EbayAuthorizationCallbackView,
    EbayAuthorizationStartView,
    EbayConnectionListView,
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
    path(
        "account-deletion/", EbayAccountDeletionView.as_view(), name="account-deletion"
    ),
]
