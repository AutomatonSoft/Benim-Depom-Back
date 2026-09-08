"""German material labels for marketplace AI copy.

Sellers type materials in Russian, Turkish, English or German.  The AI draft
must use German names.  This map covers common furniture terms; unknown values
stay in the raw snapshot so the model can translate or omit them.
"""

from __future__ import annotations

# Keys are lowercase, ё folded to е, extra spaces collapsed.
MATERIAL_ALIASES: dict[str, str] = {
    "хлопок": "Baumwolle",
    "cotton": "Baumwolle",
    "pamuk": "Baumwolle",
    "baumwolle": "Baumwolle",
    "лен": "Leinen",
    "лён": "Leinen",
    "linen": "Leinen",
    "keten": "Leinen",
    "leinen": "Leinen",
    "шерсть": "Wolle",
    "wool": "Wolle",
    "yun": "Wolle",
    "yün": "Wolle",
    "wolle": "Wolle",
    "ткань": "Stoff",
    "fabric": "Stoff",
    "kumas": "Stoff",
    "kumaş": "Stoff",
    "stoff": "Stoff",
    "велюр": "Velours",
    "velour": "Velours",
    "velours": "Velours",
    "бархат": "Samt",
    "velvet": "Samt",
    "kadife": "Samt",
    "samt": "Samt",
    "микрофибра": "Mikrofaser",
    "microfiber": "Mikrofaser",
    "microfibre": "Mikrofaser",
    "mikrofiber": "Mikrofaser",
    "mikrofaser": "Mikrofaser",
    "полиэстер": "Polyester",
    "polyester": "Polyester",
    "кожа": "Leder",
    "leather": "Leder",
    "deri": "Leder",
    "leder": "Leder",
    "экокожа": "Kunstleder",
    "эко-кожа": "Kunstleder",
    "эко кожа": "Kunstleder",
    "искусственная кожа": "Kunstleder",
    "eco leather": "Kunstleder",
    "faux leather": "Kunstleder",
    "suni deri": "Kunstleder",
    "kunstleder": "Kunstleder",
    "дерево": "Holz",
    "wood": "Holz",
    "ahsap": "Holz",
    "ahşap": "Holz",
    "holz": "Holz",
    "массив": "Massivholz",
    "массив дерева": "Massivholz",
    "solid wood": "Massivholz",
    "masif": "Massivholz",
    "massivholz": "Massivholz",
    "мдф": "MDF",
    "mdf": "MDF",
    "дсп": "Spanplatte",
    "chipboard": "Spanplatte",
    "particle board": "Spanplatte",
    "sunta": "Spanplatte",
    "spanplatte": "Spanplatte",
    "металл": "Metall",
    "metal": "Metall",
    "metall": "Metall",
    "пластик": "Kunststoff",
    "plastic": "Kunststoff",
    "plastik": "Kunststoff",
    "kunststoff": "Kunststoff",
    "стекло": "Glas",
    "glass": "Glas",
    "cam": "Glas",
    "glas": "Glas",
    "ротанг": "Rattan",
    "rattan": "Rattan",
}


def _normalize_material(value: str) -> str:
    compact = " ".join(str(value).strip().casefold().replace("ё", "е").split())
    return compact.replace("ı", "i")


def german_material_name(value: str) -> str | None:
    """Return a German material name, or None when the source term is unknown."""

    normalized = _normalize_material(value)
    if not normalized:
        return None
    return MATERIAL_ALIASES.get(normalized)
