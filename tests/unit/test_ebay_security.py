import base64
import hashlib
import json

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from apps.marketplace.ebay.client import EbayConfigurationError
from apps.marketplace.ebay.security import (
    InvalidEbaySignature,
    challenge_response,
    decrypt_token,
    token_cipher,
    verify_notification,
)


@pytest.mark.unit
def test_challenge_uses_configured_url_not_request_host(settings):
    settings.EBAY_PUBLIC_BASE_URL = "https://stage.benimdepom.info"
    settings.EBAY_NOTIFICATION_VERIFICATION_TOKEN = "a" * 32
    expected = hashlib.sha256(
        (
            "challenge"
            + "a" * 32
            + "https://stage.benimdepom.info/api/v1/ebay/account-deletion/"
        ).encode()
    ).hexdigest()
    assert challenge_response("challenge") == expected


@pytest.mark.unit
def test_tokens_are_encrypted_and_wrong_key_cannot_decrypt(settings):
    settings.EBAY_TOKEN_ENCRYPTION_KEY = Fernet.generate_key().decode()
    encrypted = token_cipher().encrypt(b"sample-user-token").decode()
    assert "sample-user-token" not in encrypted
    assert decrypt_token(encrypted) == "sample-user-token"
    settings.EBAY_TOKEN_ENCRYPTION_KEY = Fernet.generate_key().decode()
    with pytest.raises(EbayConfigurationError):
        decrypt_token(encrypted)


@pytest.mark.unit
@pytest.mark.parametrize("digest", ["SHA1", "SHA256"])
def test_notification_signature_rejects_modified_payload(monkeypatch, digest):
    key = ec.generate_private_key(ec.SECP256R1())
    pem = (
        key.public_key()
        .public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        .decode()
    )
    monkeypatch.setattr(
        "apps.marketplace.ebay.security.get_notification_public_key", lambda key_id: pem
    )
    payload = {
        "metadata": {"topic": "MARKETPLACE_ACCOUNT_DELETION"},
        "notification": {"data": {"userId": "offline-test-user"}},
    }
    compact = json.dumps(payload, separators=(",", ":")).encode()
    signature = key.sign(
        compact, ec.ECDSA(hashes.SHA1() if digest == "SHA1" else hashes.SHA256())
    )
    header = base64.b64encode(
        json.dumps(
            {
                "alg": "ECC",
                "digest": digest,
                "kid": "test-key",
                "signature": base64.b64encode(signature).decode(),
            }
        ).encode()
    ).decode()
    assert (
        verify_notification(json.dumps(payload, indent=2).encode(), header) == payload
    )
    with pytest.raises(InvalidEbaySignature):
        verify_notification(
            compact.replace(b"offline-test-user", b"another-test-user"), header
        )


@pytest.mark.unit
def test_missing_signature_is_rejected_before_any_http_request():
    with pytest.raises(InvalidEbaySignature):
        verify_notification(b"{}", "")
