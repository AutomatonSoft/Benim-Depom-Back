from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

from apps.marketplace.colors import german_color_name
from apps.products.pricing import fill_marketplace_price

KAUFLAND_STOREFRONTS = (
    "de",
    "cz",
    "sk",
    "pl",
    "at",
    "fr",
    "it",
)


class KauflandPayloadValidationError(ValueError):
    """Ошибки подготовки Kaufland payload для web-панели менеджера."""

    def __init__(self, errors: dict[str, str]):
        self.errors = errors
        super().__init__("Kaufland payload preflight failed")


def _as_positive_decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None

    if not result.is_finite() or result <= 0:
        return None

    return result.quantize(Decimal("0.01"))


def _as_non_negative_int(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None

    if result < 0 or str(result) != str(value).strip():
        return None

    return result


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


def _format_number(value: Decimal) -> str:
    result = format(value, "f")

    if "." in result:
        result = result.rstrip("0").rstrip(".")

    return result


def _get_single_variant(product, errors: dict[str, str]):
    if product.variants.count() != 1:
        errors["variants"] = (
            "Kaufland publication currently requires exactly one "
            "product variant per EAN."
        )
        return None

    return product.variants.get()


def _get_image_urls(
    configuration: dict[str, Any],
    errors: dict[str, str],
) -> list[str]:
    image_urls = configuration.get("image_urls", [])

    if not isinstance(image_urls, list) or not image_urls:
        errors["image_urls"] = "Select at least one public image."
        return []

    cleaned_urls = [str(url).strip() for url in image_urls if str(url).strip()]

    if not cleaned_urls or any(not _is_public_http_url(url) for url in cleaned_urls):
        errors["image_urls"] = "Every image URL must be a public HTTP(S) URL."

    return cleaned_urls


def build_kaufland_create_payload(
    *,
    product,
    account: str,
    configuration: dict[str, Any],
) -> dict[str, Any]:
    """Builds the exact body for PUT /api/products/upload/."""

    configuration = configuration or {}
    errors: dict[str, str] = {}

    ean = _get_account_ean(product, account)

    if account not in {"jv", "xl"}:
        errors["account"] = "Account must be 'jv' or 'xl'."
    elif not ean:
        errors["ean"] = "The product has no EAN for this account."

    variant = _get_single_variant(product, errors)

    title = str(configuration.get("title", "")).strip()
    description = str(configuration.get("description", "")).strip()

    if not title:
        errors["title"] = "Enter the Kaufland product title."

    if not description:
        errors["description"] = "Enter the Kaufland product description."

    price = _as_positive_decimal(
        fill_marketplace_price(configuration, product, field="price").get("price")
    )
    if price is None:
        errors["price"] = "Enter a positive Kaufland price."

    delivery = _as_non_negative_int(configuration.get("delivery"))
    if delivery is None:
        errors["delivery"] = "Enter delivery time as a whole number of days."

    storefronts = configuration.get("storefronts") or ["de"]

    if not isinstance(storefronts, list) or any(
        str(value).strip() not in KAUFLAND_STOREFRONTS for value in storefronts
    ):
        errors["storefronts"] = (
            f"Storefronts may contain only: {', '.join(KAUFLAND_STOREFRONTS)}."
        )
        cleaned_storefronts: list[str] = []
    else:
        cleaned_storefronts = [str(value).strip() for value in storefronts]

    image_urls = _get_image_urls(configuration, errors)

    if variant is not None:
        materials = [
            str(material).strip()
            for material in variant.materials
            if str(material).strip()
        ]

        if not materials:
            errors["material"] = "Enter at least one material."

    if errors:
        raise KauflandPayloadValidationError(errors)

    # The Kaufland API accepts one material.  The mobile contract keeps the
    # first list item as the seller's primary material; remaining entries are
    # still preserved for OTTO, Hood and internal manager review.
    primary_material = materials[0]

    return {
        "ean": ean,
        "controller": account,
        "title": title,
        "description": description,
        "picture": image_urls,
        "price": float(price),
        "size": (
            f"{_format_number(variant.width_cm)} x "
            f"{_format_number(variant.height_cm)} x "
            f"{_format_number(variant.length_cm)} cm"
        ),
        "color": german_color_name(variant.color_hex),
        "material": primary_material,
        "delivery": delivery,
        "height": float(variant.height_cm),
        "length": float(variant.length_cm),
        "width": float(variant.width_cm),
        "amount": variant.quantity,
        "id_offer": str(configuration.get("id_offer") or ean).strip(),
        "storefronts": cleaned_storefronts,
    }


def build_kaufland_update_payload(
    *,
    product,
    account: str,
    configuration: dict[str, Any],
) -> dict[str, Any]:
    """Builds the exact body for PATCH /api/products/{ean}/change/."""

    configuration = configuration or {}
    errors: dict[str, str] = {}

    ean = _get_account_ean(product, account)

    if account not in {"jv", "xl"}:
        errors["account"] = "Account must be 'jv' or 'xl'."
    elif not ean:
        errors["ean"] = "The product has no EAN for this account."

    storefront = str(
        configuration.get("storefront")
        or (configuration.get("storefronts") or ["de"])[0]
    ).strip()

    if storefront not in KAUFLAND_STOREFRONTS:
        errors["storefront"] = (
            f"Storefront must be one of: {', '.join(KAUFLAND_STOREFRONTS)}."
        )

    if not storefront:
        errors["storefront"] = "Select the Kaufland storefront to update."

    payload: dict[str, Any] = {
        "ean": ean,
        "controller": account,
        "storefront": storefront,
    }

    title = str(configuration.get("title", "")).strip()
    if title:
        payload["title"] = title

    description = str(configuration.get("description", "")).strip()
    if description:
        payload["description"] = description

    filled = fill_marketplace_price(configuration, product, field="price")
    if "price" in filled:
        price = _as_positive_decimal(filled["price"])

        if price is None:
            errors["price"] = "Enter a positive Kaufland price."
        else:
            payload["price"] = float(price)

    if "image_urls" in configuration:
        image_urls = _get_image_urls(configuration, errors)

        if image_urls:
            payload["picture_urls"] = image_urls

    unit_id = str(configuration.get("unit_id", "")).strip()
    if unit_id:
        payload["unit_id"] = unit_id

    if errors:
        raise KauflandPayloadValidationError(errors)

    changed_fields = set(payload) - {"ean", "controller", "storefront"}

    if not changed_fields:
        raise KauflandPayloadValidationError(
            {
                "detail": (
                    "Select at least one field to update: title, "
                    "description, price, images, or unit_id."
                )
            }
        )

    return payload
