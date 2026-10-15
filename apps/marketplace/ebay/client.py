from urllib.parse import quote

import requests
from django.conf import settings
from django.core.cache import cache
from rest_framework.exceptions import APIException

from apps.common.marketplace_http import build_marketplace_session


class EbayConfigurationError(APIException):
    status_code = 503
    default_detail = "eBay is not configured on this server."


class EbayAPIError(APIException):
    status_code = 502
    default_detail = "eBay request failed. Start authorization again or try later."


def api_base_url():
    environment = settings.EBAY_ENVIRONMENT
    if environment not in {"sandbox", "production"}:
        raise EbayConfigurationError("EBAY_ENVIRONMENT must be sandbox or production.")
    return (
        "https://api.sandbox.ebay.com"
        if environment == "sandbox"
        else "https://api.ebay.com"
    )


def token_request(data):
    """Запрашивает OAuth-токены eBay, скрывая детали ответа при ошибке."""
    if not settings.EBAY_CLIENT_ID or not settings.EBAY_CLIENT_SECRET:
        raise EbayConfigurationError()
    try:
        with build_marketplace_session() as session:
            response = session.post(
                f"{api_base_url()}/identity/v1/oauth2/token",
                auth=(settings.EBAY_CLIENT_ID, settings.EBAY_CLIENT_SECRET),
                data=data,
                timeout=(5, 15),
            )
        if not response.ok:
            raise EbayAPIError()
        payload = response.json()
        if not isinstance(payload, dict):
            raise EbayAPIError()
        return payload
    except (requests.RequestException, ValueError) as error:
        raise EbayAPIError() from error


def get_ebay_user_id(access_token):
    """Получает неизменяемый ID продавца для сопоставления уведомлений eBay."""
    identity_base_url = api_base_url().replace("://api.", "://apiz.", 1)
    try:
        with build_marketplace_session() as session:
            response = session.get(
                f"{identity_base_url}/commerce/identity/v1/user/",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=(5, 15),
            )
        if not response.ok:
            raise EbayAPIError(
                "eBay could not identify the authorized seller "
                f"(HTTP {response.status_code})."
            )
        user_id = response.json().get("userId")
        if not isinstance(user_id, str) or not user_id or len(user_id) > 255:
            raise EbayAPIError("eBay returned no valid seller identifier.")
        return user_id
    except (requests.RequestException, ValueError, AttributeError) as error:
        raise EbayAPIError() from error


def get_notification_public_key(key_id):
    """Получает ключ проверки подписи eBay и кеширует его на один час."""
    cache_key = f"ebay:notification-key:{settings.EBAY_ENVIRONMENT}:{key_id}"
    key = cache.get(cache_key)
    if key:
        return key
    token = token_request(
        {
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope",
        }
    ).get("access_token")
    if not isinstance(token, str) or not token:
        raise EbayAPIError()
    try:
        with build_marketplace_session() as session:
            response = session.get(
                f"{api_base_url()}/commerce/notification/v1/public_key/"
                f"{quote(key_id, safe='')}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=(5, 10),
            )
        if not response.ok:
            raise EbayAPIError("eBay notification verification is unavailable.")
        key = response.json().get("key")
        if not isinstance(key, str) or not key:
            raise EbayAPIError()
    except (requests.RequestException, ValueError, AttributeError) as error:
        raise EbayAPIError() from error
    cache.set(cache_key, key, timeout=3600)
    return key
