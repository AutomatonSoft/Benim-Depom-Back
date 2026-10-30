from time import monotonic

from django.conf import settings
from django.utils import timezone
from urllib3.util.retry import Retry

from apps.common.marketplace_http import build_marketplace_session

from .client import EbayAPIError, get_seller_resource
from .services import get_access_token

MARKETPLACE = "EBAY_DE"
SANDBOX_REGISTRATION_WARNING = (
    "eBay Sandbox reports incomplete seller registration; "
    "eBay will validate eligibility when publishing."
)
POLICIES = (
    ("fulfillment", "fulfillmentPolicies", "fulfillmentPolicyId"),
    ("payment", "paymentPolicies", "paymentPolicyId"),
    ("return", "returnPolicies", "returnPolicyId"),
)


def _response_items(payload, key):
    """Проверяет список объектов eBay, отличая пустой ответ от неверной структуры."""
    items = payload.get(key)
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise EbayAPIError("eBay returned an invalid resource list.")
    return items


def _policy_summary(policy, id_field):
    """Оставляет идентификатор политики и её применимость к обычным товарам."""
    categories = _response_items(policy, "categoryTypes")
    return {
        "id": policy.get(id_field),
        "name": policy.get("name", ""),
        "marketplace_id": policy.get("marketplaceId"),
        "applicable_to_furniture": (
            policy.get("marketplaceId") == MARKETPLACE
            and bool(policy.get(id_field))
            and any(
                item.get("name") == "ALL_EXCLUDING_MOTORS_VEHICLES"
                for item in categories
            )
        ),
    }


def _location_summary(location):
    """Возвращает ключ склада, состояние и краткий адрес без контактных данных."""
    details = location.get("location", {})
    if not isinstance(details, dict):
        raise EbayAPIError("eBay returned invalid inventory location details.")
    address = details.get("address", {})
    if not isinstance(address, dict):
        raise EbayAPIError("eBay returned an invalid inventory location address.")
    return {
        "merchant_location_key": location.get("merchantLocationKey"),
        "name": location.get("name", ""),
        "status": location.get("merchantLocationStatus"),
        "location_types": location.get("locationTypes", []),
        "country": address.get("country"),
        "postal_code": address.get("postalCode"),
        "city": address.get("city"),
        "usable": (
            bool(location.get("merchantLocationKey"))
            and location.get("merchantLocationStatus") == "ENABLED"
            and bool(address.get("country"))
            and bool(
                address.get("postalCode")
                or (address.get("city") and address.get("stateOrProvince"))
            )
        ),
    }


def check_seller_readiness(*, account):
    """Читает настройки аккаунта для EBAY_DE; товары и политики не изменяет."""
    access_token = get_access_token(account=account)
    deadline = monotonic() + 45
    errors = {}
    missing = []
    warnings = []
    result = {
        "account": account,
        "environment": settings.EBAY_ENVIRONMENT,
        "marketplace_id": MARKETPLACE,
        "seller_registration_completed": None,
        "selling_limit": None,
        "business_policies_enabled": None,
        "policies": {name: None for name, _, _ in POLICIES},
        "inventory_locations": None,
    }
    with build_marketplace_session() as session:
        # Ручная проверка не должна зависать на повторах каждого запроса.
        for adapter in session.adapters.values():
            adapter.max_retries = Retry(total=0)

        def read(path, params=None):
            """Ограничивает общий обход и обращается только к заданному пути API."""
            if monotonic() >= deadline:
                raise EbayAPIError("The eBay readiness check time budget was exceeded.")
            return get_seller_resource(
                session=session, access_token=access_token, path=path, params=params
            )

        try:
            privileges = read("/sell/account/v1/privilege")
            registered = privileges.get("sellerRegistrationCompleted")
            if not isinstance(registered, bool):
                raise EbayAPIError("eBay returned an invalid registration status.")
            limit = privileges.get("sellingLimit")
            if limit is not None and not isinstance(limit, dict):
                raise EbayAPIError("eBay returned invalid selling limits.")
            result["seller_registration_completed"] = registered
            result["selling_limit"] = limit
            if not registered:
                if settings.EBAY_ENVIRONMENT == "sandbox":
                    warnings.append(SANDBOX_REGISTRATION_WARNING)
                else:
                    missing.append("seller_registration")
        except EbayAPIError as error:
            errors["privileges"] = str(error.detail)

        try:
            programs = _response_items(
                read("/sell/account/v1/program/get_opted_in_programs"), "programs"
            )
            enabled = any(
                item.get("programType") == "SELLING_POLICY_MANAGEMENT"
                for item in programs
            )
            result["business_policies_enabled"] = enabled
            if not enabled:
                missing.append("business_policies")
        except EbayAPIError as error:
            errors["programs"] = str(error.detail)

        for name, response_key, id_field in POLICIES:
            try:
                payload = read(
                    f"/sell/account/v1/{name}_policy", {"marketplace_id": MARKETPLACE}
                )
                policies = [
                    _policy_summary(item, id_field)
                    for item in _response_items(payload, response_key)
                ]
                result["policies"][name] = policies
                if not any(item["applicable_to_furniture"] for item in policies):
                    missing.append(f"{name}_policy")
            except EbayAPIError as error:
                errors[f"{name}_policy"] = str(error.detail)

        try:
            locations = []
            offset = 0
            while True:
                payload = read(
                    "/sell/inventory/v1/location", {"limit": 100, "offset": offset}
                )
                page = _response_items(payload, "locations")
                locations.extend(_location_summary(item) for item in page)
                if not payload.get("next"):
                    break
                if not page:
                    raise EbayAPIError(
                        "eBay returned invalid inventory location pagination."
                    )
                offset += len(page)
            result["inventory_locations"] = locations
            if not any(item["usable"] for item in locations):
                missing.append("inventory_location")
        except EbayAPIError as error:
            errors["inventory_locations"] = str(error.detail)

    result.update(
        checked_at=timezone.now(),
        checks_complete=not errors,
        ready_for_listing_setup=not errors and not missing,
        missing_requirements=missing,
        warnings=warnings,
        errors=errors,
    )
    return result
