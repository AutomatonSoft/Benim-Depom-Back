from __future__ import annotations

import json
from copy import deepcopy
from functools import lru_cache
from typing import Any

from django.conf import settings

PRICING_FILE = settings.BASE_DIR / "data" / "pricing.json"


@lru_cache(maxsize=1)
def get_pricing_catalog() -> dict[str, Any]:
    payload = json.loads(PRICING_FILE.read_text(encoding="utf-8"))
    return payload


def catalog_for_product(product) -> dict[str, Any]:
    catalog = deepcopy(get_pricing_catalog())
    overrides = product.pricing_overrides or {}
    for key in ("margin", "adv_fee", "vat"):
        if key in overrides:
            catalog[key] = overrides[key]
    if overrides.get("city_tariffs_eur_per_cbm"):
        catalog["city_tariffs_eur_per_cbm"].update(
            overrides["city_tariffs_eur_per_cbm"]
        )
    if overrides.get("de_size_tiers"):
        catalog["de_size_tiers"] = deepcopy(overrides["de_size_tiers"])
    return catalog

