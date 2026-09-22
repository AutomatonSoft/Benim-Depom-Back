from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

from apps.marketplace.colors import listing_color
from apps.marketplace.kaufland.gpsr import gpsr_payload_for_account
from apps.marketplace.listing_title import with_listing_brand_mark
from apps.marketplace.materials import listing_material_composition, listing_materials
from apps.products.listing_images import (
    MISSING_LISTING_IMAGES,
    public_generated_listing_urls,
)
from apps.products.pricing import fill_marketplace_price

DEFAULT_KAUFLAND_DELIVERY_DAYS = 32

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


def _parts_of_animal_origin(material: str) -> str | None:
    """Kaufland GPSR flag for leather vs textile upholstery."""
    token = material.casefold()
    if token in {"leder", "echtleder"}:
        return "Yes"
    if token in {"kunstleder", "textil"}:
        return "No"
    return None


def _get_single_variant(product, errors: dict[str, str]):
    if product.variants.count() != 1:
        errors["variants"] = (
            "Kaufland publication currently requires exactly one "
            "product variant per EAN."
        )
        return None

    return product.variants.get()


def _get_listing_image_urls(product, errors: dict[str, str]) -> list[str]:
    image_urls = public_generated_listing_urls(product)
    if not image_urls:
        errors["image_urls"] = MISSING_LISTING_IMAGES
        return []
    if any(not _is_public_http_url(url) for url in image_urls):
        errors["image_urls"] = (
            "Every generated listing image must be a public HTTP(S) URL."
        )
    return image_urls


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
    else:
        title = with_listing_brand_mark(title, max_length=255)

    if not description:
        errors["description"] = "Enter the Kaufland product description."

    price = _as_positive_decimal(
        fill_marketplace_price(configuration, product, field="price").get("price")
    )
    if price is None:
        errors["price"] = "Enter a positive Kaufland price."

    delivery = _as_non_negative_int(configuration.get("delivery"))
    if delivery is None:
        delivery = DEFAULT_KAUFLAND_DELIVERY_DAYS

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

    image_urls = _get_listing_image_urls(product, errors)

    materials: list[str] = []
    color = ""
    composition = ""
    if variant is not None:
        color, color_error = listing_color(
            configuration=configuration,
            variant_color=variant.color,
        )
        if color_error:
            errors["color"] = color_error
        materials, material_error = listing_materials(
            configuration=configuration,
            variant_materials=variant.materials,
        )
        if material_error:
            errors["material"] = material_error
        composition, composition_error = listing_material_composition(
            configuration=configuration,
            variant_composition=getattr(variant, "material_composition", ""),
        )
        if composition_error:
            errors["material_composition"] = composition_error

    gpsr = gpsr_payload_for_account(account) if account in {"jv", "xl"} else None
    if account in {"jv", "xl"} and gpsr is None:
        errors["product_safety_contact"] = (
            "Kaufland GPSR contact is missing for this account. "
            "Set name, EU address, phone and email in kaufland_gpsr.json."
        )

    if errors:
        raise KauflandPayloadValidationError(errors)

    # The Kaufland API accepts one material. Remaining listing materials stay
    # on Hood, OTTO configuration and the manager listing form.
    primary_material = materials[0]
    payload = {
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
        "color": color,
        "material": primary_material,
        "delivery": delivery,
        "height": float(variant.height_cm),
        "length": float(variant.length_cm),
        "width": float(variant.width_cm),
        "amount": variant.quantity,
        "id_offer": str(configuration.get("id_offer") or ean).strip(),
        "storefronts": cleaned_storefronts,
        **gpsr,
    }
    if composition:
        payload["material_composition"] = composition
    origin = _parts_of_animal_origin(primary_material)
    if origin:
        payload["parts_of_animal_origin"] = origin
    return payload


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

    variant = product.variants.get() if product.variants.count() == 1 else None

    payload: dict[str, Any] = {
        "ean": ean,
        "controller": account,
        "storefront": storefront,
    }

    if variant is not None:
        payload["amount"] = int(variant.quantity)
        listing_composition, composition_error = listing_material_composition(
            configuration=configuration,
            variant_composition=getattr(variant, "material_composition", ""),
        )
        if composition_error:
            errors["material_composition"] = composition_error
        elif listing_composition:
            payload["material_composition"] = listing_composition

    title = str(configuration.get("title", "")).strip()
    if title:
        payload["title"] = with_listing_brand_mark(title, max_length=255)

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

    image_urls = _get_listing_image_urls(product, errors)
    if image_urls:
        payload["picture_urls"] = image_urls

    unit_id = str(configuration.get("unit_id", "")).strip()
    if unit_id:
        payload["unit_id"] = unit_id

    gpsr = gpsr_payload_for_account(account) if account in {"jv", "xl"} else None
    if account in {"jv", "xl"} and gpsr is None:
        errors["product_safety_contact"] = (
            "Kaufland GPSR contact is missing for this account. "
            "Set name, EU address, phone and email in kaufland_gpsr.json."
        )
    elif gpsr is not None:
        payload.update(gpsr)

    if errors:
        raise KauflandPayloadValidationError(errors)

    changed_fields = set(payload) - {"ean", "controller", "storefront"}

    if not changed_fields:
        raise KauflandPayloadValidationError(
            {
                "detail": (
                    "Select at least one field to update: title, "
                    "description, price, images, amount, or unit_id."
                )
            }
        )

    return payload
