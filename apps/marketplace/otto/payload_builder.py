"""Build validated OTTO wrapper payloads from our domain models.

This module has no HTTP side effects.  It is intentionally kept separate from
the marketplace client so a manager can preview and fix a payload before any
external request is sent.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any
from urllib.parse import urlparse

from django.utils.dateparse import parse_datetime

from apps.catalog.otto_catalog import OttoCatalogError, get_otto_catalog
from apps.catalog.otto_shipping_profiles import (
    OttoShippingProfilesError,
    get_otto_shipping_profile,
)
from apps.marketplace.colors import german_color_name

OTTO_DELIVERY_TYPES = (
    "PARCEL",
    "FORWARDER_PREFERREDLOCATION",
    "FORWARDER_CURBSIDE",
    "FORWARDER_HEAVYDUTY",
    "FORWARDED_PREFERREDLOCATION",
    "FORWARDED_CURBSIDE",
)
OTTO_VAT_VALUES = ("FULL", "REDUCED", "FREE")


class OttoPayloadValidationError(ValueError):
    """Structured preflight errors suitable for a web-panel form."""

    def __init__(self, errors: dict[str, str]):
        self.errors = errors
        super().__init__("OTTO payload preflight failed.")


def calculate_uvp(standard_price: Decimal) -> Decimal:
    """Return the manager's OTTO UVP/MSRP rule rounded to euro cents.

    Price bands are inclusive from their lower boundary.  This intentionally
    makes 5,000.00 use the 10 percent band, removing the gap in the original
    informal formula between 4,999 and 5,000.
    """

    if standard_price >= Decimal("5000"):
        multiplier = Decimal("1.10")
    elif standard_price >= Decimal("2500"):
        multiplier = Decimal("1.18")
    elif standard_price >= Decimal("1000"):
        multiplier = Decimal("1.25")
    else:
        multiplier = Decimal("1.35")

    return (standard_price * multiplier).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )


def _as_positive_decimal(value: Any) -> Decimal | None:
    try:
        price = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None

    if not price.is_finite() or price <= 0:
        return None

    return price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _as_positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None

    return number if number >= 1 else None

def _as_optional_datetime(value: Any) -> str | None:
    if value is None or value == "":
        return None

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, str):
        parsed = parse_datetime(value)
        if parsed is not None:
            return parsed.isoformat()

    return None

def _is_public_http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False

    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _get_account_ean(product, account: str) -> str:
    if account == "jv":
        return product.ean_jv.strip()
    if account == "xl":
        return product.ean_xl.strip()
    return ""


def _normalize_attribute_values(value: Any) -> list[str]:
    if value is None:
        return []

    raw_values = value if isinstance(value, list) else [value]
    result = []

    for raw_value in raw_values:
        normalized = str(raw_value).strip()
        if normalized:
            result.append(normalized)

    return result


def _is_color_attribute(definition: dict[str, Any]) -> bool:
    """Whether this OTTO category attribute represents a product color."""

    name = str(definition.get("name", "")).casefold()
    return "farbe" in name or "color" in name


def _get_product_color_name(product) -> str | None:
    """Resolve the internal HEX only when the product has exactly one variant."""

    if product.variants.count() != 1:
        return None

    try:
        return german_color_name(product.variants.get().color_hex)
    except (AttributeError, ValueError):
        return None


def _build_category_attributes(product, errors: dict[str, str]) -> list[dict[str, Any]]:
    if not product.otto_category_group_id:
        return []

    try:
        catalog = get_otto_catalog()
    except OttoCatalogError as exc:
        errors["catalog"] = str(exc)
        return []

    definitions = catalog["attributes_by_id_by_group_id"].get(
        product.otto_category_group_id,
        {},
    )
    attributes = []
    product_color_name = _get_product_color_name(product)

    for raw_id, raw_value in (product.otto_attributes or {}).items():
        try:
            attribute_id = int(raw_id)
        except (TypeError, ValueError):
            errors["otto_attributes"] = "OTTO attribute keys must be numeric IDs."
            continue

        definition = definitions.get(attribute_id)
        if definition is None:
            errors["otto_attributes"] = (
                f"Attribute {attribute_id} does not belong to the selected OTTO category group."
            )
            continue

        values = _normalize_attribute_values(raw_value)

        # OTTO does not have one universal `color` field.  It is a category
        # attribute, therefore replace its value only when that category has
        # an explicitly selected color attribute.
        if product_color_name and _is_color_attribute(definition):
            values = [product_color_name]
        if values:
            attributes.append(
                {
                    "name": definition["name"],
                    "values": values,
                }
            )

    return attributes


def build_otto_payload(*, product, account: str, configuration: dict[str, Any]) -> list[dict[str, Any]]:
    """Build one OTTO product variation for one account.

    The current domain model assigns exactly one EAN per account to a product.
    Therefore publishing several internal variants as one external OTTO product
    would be unsafe; this builder stops it rather than reusing an EAN silently.
    """

    configuration = configuration or {}
    errors: dict[str, str] = {}
    ean = _get_account_ean(product, account)

    if account not in {"jv", "xl"}:
        errors["account"] = "Account must be 'jv' or 'xl'."
    elif not ean:
        errors["ean"] = "The product has no EAN for this account yet."
    elif not ean.isdigit() or not 8 <= len(ean) <= 13:
        errors["ean"] = "OTTO accepts an EAN/SKU containing 8 to 13 digits."

    if not product.otto_category_name:
        errors["otto_category"] = "Select an OTTO category on the product first."

    variant_count = product.variants.count()
    if variant_count != 1:
        errors["variants"] = (
            "OTTO publication currently requires exactly one product variant; "
            "one product has one EAN per account."
        )

    product_line = str(configuration.get("product_line", "")).strip()
    if not product_line:
        errors["product_line"] = "Enter the German product name/product line."
    elif len(product_line) > 100:
        errors["product_line"] = "Product name/product line may not exceed 100 characters."

    standard_price = _as_positive_decimal(configuration.get("standard_price"))
    if standard_price is None:
        errors["standard_price"] = "Enter a positive OTTO selling price in EUR."

    vat = configuration.get("vat")
    if vat not in OTTO_VAT_VALUES:
        errors["vat"] = "Select VAT: FULL, REDUCED, or FREE."

    shipping_profile_id = str(
        configuration.get("shipping_profile_id", "")
    ).strip()
    shipping_profile = None

    if not shipping_profile_id:
        errors["shipping_profile_id"] = (
            "Select the OTTO shipping profile."
        )
    else:
        try:
            shipping_profile = get_otto_shipping_profile(
                account=account,
                shipping_profile_id=shipping_profile_id,
            )
        except OttoShippingProfilesError as exc:
            errors["shipping_profile_id"] = str(exc)
        else:
            if shipping_profile is None:
                errors["shipping_profile_id"] = (
                    "The selected shipping profile does not belong "
                    "to this OTTO account."
                )
            elif (
                shipping_profile["deliveryType"]
                not in OTTO_DELIVERY_TYPES
            ):
                errors["shipping_profile_id"] = (
                    "The selected profile has an unsupported delivery type."
                )
            elif not isinstance(shipping_profile["transportTime"], int) or (
                shipping_profile["transportTime"] < 1
            ):
                errors["shipping_profile_id"] = (
                    "The selected profile has an invalid transport time."
                )

    media_urls = configuration.get("media_urls", [])
    if not isinstance(media_urls, list) or not media_urls:
        errors["media_urls"] = "Select at least one publicly accessible image."
        cleaned_media_urls: list[str] = []
    else:
        cleaned_media_urls = [str(url).strip() for url in media_urls]
        if any(not _is_public_http_url(url) for url in cleaned_media_urls):
            errors["media_urls"] = "Every selected image must be a public HTTP(S) URL."

    bullet_points = configuration.get("bullet_points", [])
    if not isinstance(bullet_points, list):
        errors["bullet_points"] = "Bullet points must be a list."
        cleaned_bullet_points: list[str] = []
    else:
        cleaned_bullet_points = [str(item).strip() for item in bullet_points if str(item).strip()]
        if len(cleaned_bullet_points) > 5:
            errors["bullet_points"] = "OTTO accepts at most five bullet points."

    attributes = _build_category_attributes(product, errors)

    product_description: dict[str, Any] = {
        "category": product.otto_category_name,
        "productLine": product_line,
        "bulletPoints": cleaned_bullet_points,
        "attributes": attributes,
    }

    optional_description_fields = {
        "brand_id": "brandId",
        "manufacturer": "manufacturer",
        "description": "description",
        "product_url": "productUrl",
    }
    for source_field, target_field in optional_description_fields.items():
        value = configuration.get(source_field)
        if value is not None and str(value).strip():
            product_description[target_field] = str(value).strip()

    for field, target_field in (
        ("bundle", "bundle"),
        ("multi_pack", "multiPack"),
        ("fsc_certified", "fscCertified"),
        ("disposal", "disposal"),
    ):
        if field in configuration and configuration[field] is not None:
            product_description[target_field] = bool(configuration[field])
    offering_start_date = _as_optional_datetime(
        configuration.get("offering_start_date")
    )
    if (
        configuration.get("offering_start_date") not in (None, "")
        and offering_start_date is None
    ):
        errors["offering_start_date"] = (
            "Use an ISO 8601 date-time, for example 2026-08-17T12:00:00Z."
        )

    release_date = _as_optional_datetime(
        configuration.get("release_date")
    )
    if (
        configuration.get("release_date") not in (None, "")
        and release_date is None
    ):
        errors["release_date"] = (
            "Use an ISO 8601 date-time, for example 2026-08-17T12:00:00Z."
        )

    raw_max_quantity = configuration.get("order_max_quantity")
    raw_period_in_days = configuration.get("order_period_in_days")

    max_quantity = _as_positive_int(raw_max_quantity)
    period_in_days = _as_positive_int(raw_period_in_days)

    if raw_max_quantity is not None or raw_period_in_days is not None:
        if max_quantity is None or period_in_days is None:
            errors["order"] = (
                "For an order limit, both order_max_quantity and "
                "order_period_in_days must be positive integers."
            )

    if errors:
        raise OttoPayloadValidationError(errors)

    variation = {
        "productReference": f"product-{product.pk}",
        "sku": ean,
        "ean": ean,
        "shippingProfileId": shipping_profile_id,
        "productDescription": product_description,
        "mediaAssets": [
            {"type": "IMAGE", "location": url}
            for url in cleaned_media_urls
        ],
        "delivery": {
            "type": shipping_profile["deliveryType"],
            "deliveryTime": shipping_profile["transportTime"],
        },
        "pricing": {
            "standardPrice": {
                "amount": float(standard_price),
                "currency": "EUR",
            },
            "msrp": {
                "amount": float(calculate_uvp(standard_price)),
                "currency": "EUR",
            },
            "vat": vat,
        },
    }

    for field, payload_field in (
        ("isbn", "isbn"),
        ("upc", "upc"),
        ("pzn", "pzn"),
        ("mpn", "mpn"),
        ("moin", "moin"),
    ):
        value = configuration.get(field)
        if value is not None and str(value).strip():
            variation[payload_field] = str(value).strip()

    if offering_start_date is not None:
        variation["offeringStartDate"] = offering_start_date

    if release_date is not None:
        variation["releaseDate"] = release_date

    if max_quantity is not None and period_in_days is not None:
        variation["order"] = {
            "maxOrderQuantity": {
                "quantity": max_quantity,
                "periodInDays": period_in_days,
            }
        }

    return [variation]
