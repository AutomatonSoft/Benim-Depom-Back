import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.common.permissions import is_manager

from .client import (
    EbayAPIError,
    EbayConfigurationError,
    api_base_url,
    get_ebay_user_id,
    token_request,
)
from .models import EbayConnection, EbayOAuthAttempt
from .security import decrypt_token, public_endpoint, token_cipher

OAUTH_SCOPES = (
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/commerce.identity.readonly",
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.account",
)


def start_authorization(*, account, requested_by):
    """Создаёт одноразовый state на 10 минут и ссылку согласия продавца eBay."""
    api_base_url()
    token_cipher()
    public_endpoint("/api/v1/ebay/oauth/callback/")
    if not all(
        (settings.EBAY_CLIENT_ID, settings.EBAY_CLIENT_SECRET, settings.EBAY_RUNAME)
    ):
        raise EbayConfigurationError("Set eBay client credentials and EBAY_RUNAME.")
    now = timezone.now()
    state = secrets.token_urlsafe(32)
    with transaction.atomic():
        EbayOAuthAttempt.objects.filter(expires_at__lte=now).delete()
        EbayOAuthAttempt.objects.filter(
            account=account, environment=settings.EBAY_ENVIRONMENT
        ).delete()
        EbayOAuthAttempt.objects.create(
            state_hash=hashlib.sha256(state.encode()).hexdigest(),
            environment=settings.EBAY_ENVIRONMENT,
            account=account,
            requested_by=requested_by,
            expires_at=now + timedelta(minutes=10),
        )
    auth_base = (
        "https://auth.sandbox.ebay.com"
        if settings.EBAY_ENVIRONMENT == "sandbox"
        else "https://auth.ebay.com"
    )
    return f"{auth_base}/oauth2/authorize?" + urlencode(
        {
            "client_id": settings.EBAY_CLIENT_ID,
            "redirect_uri": settings.EBAY_RUNAME,
            "response_type": "code",
            "scope": " ".join(OAUTH_SCOPES),
            "state": state,
        }
    )


def token_expiry(data, field):
    """Проверяет срок из ответа eBay и переводит секунды в дату истечения."""
    seconds = data.get(field)
    if type(seconds) is not int or seconds <= 0:
        raise EbayAPIError("eBay returned an invalid token expiry.")
    try:
        return timezone.now() + timedelta(seconds=seconds)
    except OverflowError as error:
        raise EbayAPIError("eBay returned an invalid token expiry.") from error


def complete_authorization(*, state, code, denied=False):
    """Однократно принимает OAuth state, обменивает код и сохраняет подключение."""
    if not state or len(state) > 128:
        raise ValidationError("Invalid or expired eBay authorization state.")
    with transaction.atomic():
        attempt = (
            EbayOAuthAttempt.objects.select_for_update()
            .select_related("requested_by")
            .filter(
                state_hash=hashlib.sha256(state.encode()).hexdigest(),
                environment=settings.EBAY_ENVIRONMENT,
                expires_at__gt=timezone.now(),
            )
            .first()
        )
        if attempt is None:
            raise ValidationError("Invalid or expired eBay authorization state.")
        attempt.delete()
    if denied:
        raise ValidationError("eBay authorization was declined.")
    if not attempt.requested_by.is_active or not is_manager(attempt.requested_by):
        raise ValidationError("The manager no longer has permission to connect eBay.")
    if not code or len(code) > 8192:
        raise ValidationError("An eBay authorization code is required.")
    tokens = token_request(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.EBAY_RUNAME,
        }
    )
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    if not all(
        isinstance(token, str) and token for token in (access_token, refresh_token)
    ):
        raise EbayAPIError("eBay returned no usable user tokens.")
    cipher = token_cipher()
    user_id = get_ebay_user_id(access_token)
    try:
        with transaction.atomic():
            connection, _ = EbayConnection.objects.update_or_create(
                environment=attempt.environment,
                account=attempt.account,
                defaults={
                    "ebay_user_id": user_id,
                    "access_token_encrypted": cipher.encrypt(
                        access_token.encode()
                    ).decode(),
                    "refresh_token_encrypted": cipher.encrypt(
                        refresh_token.encode()
                    ).decode(),
                    "access_token_expires_at": token_expiry(tokens, "expires_in"),
                    "refresh_token_expires_at": token_expiry(
                        tokens, "refresh_token_expires_in"
                    ),
                },
            )
    except IntegrityError as error:
        raise ValidationError(
            "This eBay seller is already connected to another account."
        ) from error
    return connection


def get_access_token(*, account):
    """Возвращает токен внутреннему клиенту, обновляя его перед истечением срока."""
    with transaction.atomic():
        connection = (
            EbayConnection.objects.select_for_update()
            .filter(account=account, environment=settings.EBAY_ENVIRONMENT)
            .first()
        )
        if connection is None:
            raise EbayConfigurationError("Connect this eBay account first.")
        now = timezone.now()
        if connection.access_token_expires_at > now + timedelta(seconds=60):
            return decrypt_token(connection.access_token_encrypted)
        if connection.refresh_token_expires_at <= now:
            raise EbayConfigurationError("Authorize this eBay seller again.")
        tokens = token_request(
            {
                "grant_type": "refresh_token",
                "refresh_token": decrypt_token(connection.refresh_token_encrypted),
                "scope": " ".join(OAUTH_SCOPES),
            }
        )
        access_token = tokens.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise EbayAPIError()
        connection.access_token_encrypted = (
            token_cipher().encrypt(access_token.encode()).decode()
        )
        connection.access_token_expires_at = token_expiry(tokens, "expires_in")
        connection.save(
            update_fields=(
                "access_token_encrypted",
                "access_token_expires_at",
                "updated_at",
            )
        )
        return access_token


def process_account_deletion(payload):
    """Удаляет локальное подключение по userId; аккаунт на eBay не удаляет."""
    metadata = payload.get("metadata")
    notification = payload.get("notification")
    if not isinstance(metadata, dict) or not isinstance(notification, dict):
        raise ValidationError("Invalid eBay notification payload.")
    if metadata.get("topic") != "MARKETPLACE_ACCOUNT_DELETION":
        raise ValidationError("Unsupported eBay notification topic.")
    data = notification.get("data")
    if not isinstance(data, dict):
        raise ValidationError("Invalid eBay deletion data.")
    user_id = data.get("userId")
    if not isinstance(user_id, str) or not user_id or len(user_id) > 255:
        raise ValidationError("A valid eBay userId is required.")
    with transaction.atomic():
        connections = EbayConnection.objects.select_for_update().filter(
            environment=settings.EBAY_ENVIRONMENT, ebay_user_id=user_id
        )
        accounts = list(connections.values_list("account", flat=True))
        EbayOAuthAttempt.objects.filter(
            environment=settings.EBAY_ENVIRONMENT, account__in=accounts
        ).delete()
        connections.delete()
