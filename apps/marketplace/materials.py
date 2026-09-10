"""German listing materials from AI draft / manager edits.

Sellers enter materials in any language. Marketplace payloads use only the
listing field after AI translation (or a manager edit). There is no local
dictionary.
"""

from __future__ import annotations

import re

_SOURCE_LANGUAGE_RE = re.compile(r"[\u0400-\u04FF]|[ğĞşŞıİçÇ]")


def contains_source_language(value: str) -> bool:
    """True when a label still looks Russian or Turkish, not German."""

    return bool(_SOURCE_LANGUAGE_RE.search(str(value or "")))


def clean_material_names(values) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for item in values or []:
        name = str(item).strip()
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        names.append(name)
        if len(names) == 2:
            break
    return names


def seller_materials_from_snapshot(snapshot: dict | None) -> list[str]:
    names: list[str] = []
    for variant in (snapshot or {}).get("variants") or []:
        if not isinstance(variant, dict):
            continue
        names.extend(variant.get("materials") or [])
    return clean_material_names(names)


def listing_materials(
    *,
    configuration: dict | None,
    variant_materials,
) -> tuple[list[str], str | None]:
    """German materials for marketplace payloads.

    Only the listing field (AI draft / manager edit) is sent. Seller source
    words are never substituted from a dictionary.
    """

    configuration = configuration or {}
    chosen = clean_material_names(configuration.get("materials"))
    seller = clean_material_names(variant_materials)

    if any(contains_source_language(name) for name in chosen):
        return chosen, "Materials must be in German."

    if seller and not chosen:
        return [], "Translate product materials to German."

    if not chosen:
        return [], "Enter at least one German material."

    return chosen, None
