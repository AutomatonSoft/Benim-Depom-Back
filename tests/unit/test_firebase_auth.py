from unittest.mock import Mock

import pytest
from rest_framework.exceptions import ValidationError

from apps.accounts.firebase_auth import get_verified_phone_from_id_token


@pytest.mark.unit
def test_phone_verification_returns_phone_from_verified_firebase_token(monkeypatch):
    firebase_app = object()
    monkeypatch.setattr("apps.accounts.firebase_auth.get_firebase_app", lambda: firebase_app)
    monkeypatch.setattr(
        "apps.accounts.firebase_auth.auth.verify_id_token",
        Mock(return_value={"phone_number": "+77474412519"}),
    )

    assert get_verified_phone_from_id_token(id_token="firebase-id-token") == "+77474412519"


@pytest.mark.unit
def test_phone_verification_rejects_disabled_or_invalid_firebase_token(monkeypatch):
    monkeypatch.setattr("apps.accounts.firebase_auth.get_firebase_app", lambda: None)
    with pytest.raises(ValidationError, match="temporarily unavailable"):
        get_verified_phone_from_id_token(id_token="token")

    monkeypatch.setattr("apps.accounts.firebase_auth.get_firebase_app", lambda: object())
    monkeypatch.setattr(
        "apps.accounts.firebase_auth.auth.verify_id_token",
        Mock(side_effect=ValueError("bad token")),
    )
    with pytest.raises(ValidationError, match="Invalid or expired"):
        get_verified_phone_from_id_token(id_token="token")
