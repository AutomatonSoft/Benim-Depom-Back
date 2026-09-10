from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

from apps.marketplace.colors import german_color_name
from apps.marketplace.listing_title import with_listing_brand_mark
from apps.marketplace.materials import listing_materials
from apps.products.listing_images import (
    MISSING_LISTING_IMAGES,
    public_generated_listing_urls,
)
from apps.products.pricing import fill_marketplace_price

DEFAULT_HOOD_CATEGORY_ID = "2412"


class HoodPayloadValidationError(ValueError):
    """Structured validation errors for the manager web panel"""

    def __init__(self, errors: dict[str, str]):
        self.errors = errors
        super().__init__("Hood payload preflight failed")


def _as_positive_decimal(value: Any) -> Decimal | None:
    try:
        price = Decimal(str(value))

    except (InvalidOperation, TypeError, ValueError):
        return None

    if not price.is_finite() or price <= 0:
        return None

    return price.quantize(Decimal("0.01"))


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


def _format_cm(value: Decimal) -> str:
    formatted_value = format(value, "f")

    if "." in formatted_value:
        formatted_value = formatted_value.rstrip("0").rstrip(".")

    return f"{formatted_value} cm"


def build_hood_payload(
    *,
    product,
    account: str,
    configuration: dict[str, Any],
) -> dict[str, Any]:
    """
    Build one Hood item payload

    Hood has one listing per Ean. Therefore publishing more than one internal
    variant with one EAN would be unsafe and is blocked explicitly
    """

    configuration = configuration or {}
    errors: dict[str, str] = {}

    ean = _get_account_ean(product, account)

    if account not in {"jv", "xl"}:
        errors["account"] = "Account must be 'jv' or 'xl'"
    elif not ean:
        errors["ean"] = "The product has no EAN for this account yet"

    if product.variants.count() != 1:
        errors["variants"] = (
            "Hood publication currently requires exactly one product variant"
            "because one listing has one EAN"
        )

    title = str(configuration.get("title", "")).strip()

    if not title:
        errors["title"] = "Enter the Hood product title"
    else:
        title = with_listing_brand_mark(title, max_length=255)

    description = str(configuration.get("description", "")).strip()
    if not description:
        errors["description"] = "Enter the Hood HTML description."

    price = _as_positive_decimal(
        fill_marketplace_price(configuration, product, field="price").get("price")
    )
    if price is None:
        errors["price"] = "Enter a positive Hood price."

    category_id = (
        str(configuration.get("category_id", "")).strip() or DEFAULT_HOOD_CATEGORY_ID
    )

    image_urls = public_generated_listing_urls(product)
    if not image_urls:
        errors["image_urls"] = MISSING_LISTING_IMAGES
    elif any(not _is_public_http_url(url) for url in image_urls):
        errors["image_urls"] = (
            "Every generated listing image must be a public HTTP(S) URL."
        )

    property_overrides = configuration.get("property_overrides", {})

    if not isinstance(property_overrides, dict):
        errors["property_overrides"] = (
            "Property overrides must be an object: name → value."
        )
        property_overrides = {}

    normalized_overrides: dict[str, str] = {}

    for raw_name, raw_value in property_overrides.items():
        name = str(raw_name).strip()
        value = str(raw_value).strip()

        if not name or not value:
            errors["property_overrides"] = (
                "Every property override must have a non-empty name and value."
            )
            continue

        normalized_overrides[name] = value

    if errors:
        raise HoodPayloadValidationError(errors)

    variant = product.variants.get()

    automatic_properties = {
        "Farbe": german_color_name(variant.color_hex),
        "Breite": _format_cm(variant.width_cm),
        "Höhe": _format_cm(variant.height_cm),
        "Länge": _format_cm(variant.length_cm),
    }

    materials, material_error = listing_materials(
        configuration=configuration,
        variant_materials=variant.materials,
    )
    if material_error:
        errors["materials"] = material_error
        raise HoodPayloadValidationError(errors)
    if materials:
        automatic_properties["Material"] = ", ".join(materials)

    # Manager can correct automatic values or add Hood-specific properties.
    automatic_properties.update(normalized_overrides)

    return {
        "title": title,
        "description": description,
        "price": float(price),
        "quantity": variant.quantity,
        "categoryID": category_id,
        "image_urls": image_urls,
        "product_properties": [
            {
                "name": name,
                "value": value,
            }
            for name, value in automatic_properties.items()
        ],
    }
