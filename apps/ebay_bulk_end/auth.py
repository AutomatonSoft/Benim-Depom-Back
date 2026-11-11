from datetime import timedelta

import requests
from django.conf import settings
from django.utils import timezone

from apps.common.marketplace_http import build_marketplace_session
from apps.marketplace.ebay.client import api_base_url, token_request
from apps.marketplace.ebay.models import EbayConnection
from apps.marketplace.ebay.security import decrypt_token
from apps.marketplace.ebay.services import OAUTH_SCOPES

from .errors import BulkEndError, EBayTransportError

ALLOWED_ACCOUNTS = ("jv", "xl", "depotum")
ALLOWED_ENVIRONMENTS = {"sandbox", "production"}


def current_environment():
    environment = settings.EBAY_ENVIRONMENT
    if environment not in ALLOWED_ENVIRONMENTS:
        raise BulkEndError("Среда eBay не настроена.")
    return environment


def connection_for(account, environment=None):
    if account not in ALLOWED_ACCOUNTS:
        raise BulkEndError("Доступны аккаунты JV, XL и Depotum.")
    environment = environment or current_environment()
    if environment != current_environment():
        raise BulkEndError("Среда eBay изменилась. Запустите задание заново.")
    return EbayConnection.objects.filter(
        account=account, environment=environment
    ).first()


def connection_status():
    environment = current_environment()
    now = timezone.now()
    rows = EbayConnection.objects.filter(
        account__in=ALLOWED_ACCOUNTS, environment=environment
    ).values(
        "account", "access_token_expires_at", "refresh_token_expires_at", "updated_at"
    )
    by_account = {row["account"]: row for row in rows}
    accounts = []
    for account in ALLOWED_ACCOUNTS:
        row = by_account.get(account)
        accounts.append(
            {
                "account": account,
                "connected": row is not None,
                "access_token_valid": bool(
                    row and row["access_token_expires_at"] > now + timedelta(seconds=60)
                ),
                "refresh_token_valid": bool(
                    row and row["refresh_token_expires_at"] > now
                ),
                "updated_at": row["updated_at"].isoformat() if row else None,
            }
        )
    return {"environment": environment, "accounts": accounts}


def access_context(account, environment):
    connection = connection_for(account, environment)
    if connection is None:
        raise BulkEndError("Подключите выбранный eBay аккаунт.")
    now = timezone.now()
    if connection.access_token_expires_at > now + timedelta(seconds=60):
        return connection, decrypt_token(connection.access_token_encrypted)
    if connection.refresh_token_expires_at <= now:
        raise BulkEndError("Повторно авторизуйте выбранный eBay аккаунт.")
    try:
        tokens = token_request(
            {
                "grant_type": "refresh_token",
                "refresh_token": decrypt_token(connection.refresh_token_encrypted),
                "scope": " ".join(OAUTH_SCOPES),
            }
        )
    except Exception as error:
        raise BulkEndError(
            "Не удалось обновить доступ eBay. Повторно подключите аккаунт."
        ) from error
    access_token = tokens.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise BulkEndError("eBay не вернул действующий токен.")
    api_base_url()
    return connection, access_token


def verified_seller_username(token, expected_user_id):
    """Bind Trading UserID (username) to the immutable ID stored at OAuth authorization."""
    identity_base = api_base_url().replace("://api.", "://apiz.", 1)
    try:
        with build_marketplace_session() as session:
            response = session.get(
                identity_base + "/commerce/identity/v1/user/",
                headers={"Authorization": f"Bearer {token}"},
                timeout=(5, 15),
            )
        if not response.ok:
            raise BulkEndError(
                "Не удалось подтвердить владельца eBay через Identity API.",
                diagnostics={
                    "operation": "verify_seller_identity",
                    "http_status": response.status_code,
                },
            )
        identity = response.json()
    except (requests.RequestException, ValueError) as error:
        raise EBayTransportError(
            "Не удалось проверить владельца eBay. Повторите поиск позже.",
            diagnostics={
                "operation": "verify_seller_identity",
                "exception_type": type(error).__name__,
            },
        ) from error
    if (
        not isinstance(identity, dict)
        or not expected_user_id
        or identity.get("userId") != expected_user_id
    ):
        raise BulkEndError(
            "Identity API вернул другой аккаунт eBay; операция заблокирована."
        )
    username = identity.get("username")
    if not isinstance(username, str) or not username.strip() or len(username) > 255:
        raise BulkEndError(
            "eBay не вернул подтверждённый логин продавца; операция заблокирована."
        )
    return username.strip()
