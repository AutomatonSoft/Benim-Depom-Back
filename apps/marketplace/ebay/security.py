import base64
import binascii
import hashlib
import json
import re
from urllib.parse import urlparse

from cryptography.exceptions import InvalidSignature
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.conf import settings
from rest_framework.exceptions import APIException, ValidationError

from .client import EbayConfigurationError, get_notification_public_key


class InvalidEbaySignature(APIException):
    status_code = 412
    default_detail = "Invalid eBay notification signature."


def token_cipher():
    """Создаёт шифр Fernet из серверного ключа для хранения OAuth-токенов."""
    try:
        return Fernet(settings.EBAY_TOKEN_ENCRYPTION_KEY.encode("ascii"))
    except (ValueError, UnicodeError) as error:
        raise EbayConfigurationError(
            "Set a valid EBAY_TOKEN_ENCRYPTION_KEY."
        ) from error


def decrypt_token(value):
    try:
        return token_cipher().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError) as error:
        raise EbayConfigurationError(
            "The stored eBay token could not be decrypted."
        ) from error


def public_endpoint(path):
    """Собирает адрес callback из настроенного публичного HTTPS-домена."""
    base = settings.EBAY_PUBLIC_BASE_URL.rstrip("/")
    parsed = urlparse(base)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    ):
        raise EbayConfigurationError(
            "Set EBAY_PUBLIC_BASE_URL to the public HTTPS origin."
        )
    return f"{base}{path}"


def challenge_response(challenge_code):
    """Вычисляет SHA-256 ответ для подтверждения адреса уведомлений в eBay."""
    token = settings.EBAY_NOTIFICATION_VERIFICATION_TOKEN
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,80}", token):
        raise EbayConfigurationError("Set EBAY_NOTIFICATION_VERIFICATION_TOKEN.")
    if not challenge_code or len(challenge_code) > 1024:
        raise ValidationError({"challenge_code": "A valid challenge code is required."})
    endpoint = public_endpoint("/api/v1/ebay/account-deletion/")
    return hashlib.sha256(f"{challenge_code}{token}{endpoint}".encode()).hexdigest()


def verify_notification(body, signature_header):
    """Проверяет подпись eBay и возвращает содержимое подлинного уведомления."""
    if len(body) > 65536 or not signature_header or len(signature_header) > 4096:
        raise InvalidEbaySignature()
    try:
        signature = json.loads(base64.b64decode(signature_header, validate=True))
        if not isinstance(signature, dict):
            raise InvalidEbaySignature()
        key_id = signature.get("kid")
        if (
            signature.get("alg") not in {"ECC", "ECDSA"}
            or signature.get("digest") not in {"SHA1", "SHA256"}
            or not isinstance(key_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,255}", key_id)
        ):
            raise InvalidEbaySignature()
        signature_bytes = base64.b64decode(signature["signature"], validate=True)
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise InvalidEbaySignature()
    except (ValueError, TypeError, KeyError, binascii.Error, RecursionError) as error:
        raise InvalidEbaySignature() from error

    public_key = get_notification_public_key(key_id)
    try:
        pem = public_key.encode("ascii")
        key = serialization.load_pem_public_key(pem)
        if not isinstance(key, ec.EllipticCurvePublicKey):
            raise InvalidEbaySignature()
        digest = hashes.SHA1() if signature["digest"] == "SHA1" else hashes.SHA256()
        # eBay's SDK verifies compact JSON; also accept an exactly signed raw body.
        compact = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
        try:
            key.verify(signature_bytes, body, ec.ECDSA(digest))
        except InvalidSignature:
            key.verify(signature_bytes, compact, ec.ECDSA(digest))
    except (InvalidSignature, ValueError, TypeError, UnicodeError) as error:
        raise InvalidEbaySignature() from error
    return payload
