from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet
from rest_framework.exceptions import ValidationError

from apps.marketplace.ebay.models import EbayConnection
from apps.marketplace.ebay.security import decrypt_token
from apps.marketplace.ebay.services import complete_authorization, start_authorization


@pytest.mark.unit
@pytest.mark.django_db
def test_oauth_state_is_single_use_and_seller_tokens_are_encrypted(
    settings, manager, monkeypatch
):
    settings.EBAY_ENVIRONMENT = "sandbox"
    settings.EBAY_CLIENT_ID = "offline-client"
    settings.EBAY_CLIENT_SECRET = "offline-secret"
    settings.EBAY_RUNAME = "offline-runame"
    settings.EBAY_PUBLIC_BASE_URL = "https://stage.benimdepom.info"
    settings.EBAY_TOKEN_ENCRYPTION_KEY = Fernet.generate_key().decode()
    monkeypatch.setattr(
        "apps.marketplace.ebay.services.token_request",
        lambda data: {
            "access_token": "offline-access",
            "refresh_token": "offline-refresh",
            "expires_in": 7200,
            "refresh_token_expires_in": 36000,
        },
    )
    monkeypatch.setattr(
        "apps.marketplace.ebay.services.get_ebay_user_id", lambda token: "offline-user"
    )
    url = start_authorization(account="jv", requested_by=manager)
    params = parse_qs(urlparse(url).query)
    assert urlparse(url).hostname == "auth.sandbox.ebay.com"
    assert params["redirect_uri"] == ["offline-runame"]
    connection = complete_authorization(state=params["state"][0], code="offline-code")
    assert connection.environment == "sandbox"
    assert connection.account == "jv"
    assert decrypt_token(connection.refresh_token_encrypted) == "offline-refresh"
    assert connection.access_token_encrypted != "offline-access"
    with pytest.raises(ValidationError, match="Invalid or expired"):
        complete_authorization(state=params["state"][0], code="offline-code")
    assert EbayConnection.objects.count() == 1
