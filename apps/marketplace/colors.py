"""German listing colour from AI draft / manager edits.

Sellers enter a colour name in any language. Marketplace payloads use only
the listing field after AI translation (or a manager edit). There is no
local dictionary and no HEX mapping at publish time.
"""

from __future__ import annotations

from apps.marketplace.materials import contains_source_language


def clean_color_name(value) -> str:
    return str(value or "").strip()


def seller_color_from_snapshot(snapshot: dict | None) -> str:
    for variant in (snapshot or {}).get("variants") or []:
        if not isinstance(variant, dict):
            continue
        color = clean_color_name(variant.get("color"))
        if color:
            return color
    return ""


def listing_color(
    *,
    configuration: dict | None,
    variant_color,
) -> tuple[str, str | None]:
    """German colour for marketplace payloads.

    Only the listing field (AI draft / manager edit) is sent.
    """

    chosen = clean_color_name((configuration or {}).get("color"))
    seller = clean_color_name(variant_color)

    if (
        seller
        and chosen.casefold() == seller.casefold()
        and contains_source_language(seller)
    ):
        return chosen, "Translate the product color to German."

    if contains_source_language(chosen):
        return chosen, "Color must be in German."

    if seller and not chosen:
        return "", "Translate the product color to German."

    if not chosen:
        return "", "Enter a German color."

    return chosen, None
