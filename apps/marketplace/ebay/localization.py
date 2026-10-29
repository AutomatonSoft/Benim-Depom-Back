"""Display-only catalog labels. German eBay keys and values remain unchanged."""

import json
from copy import deepcopy
from functools import lru_cache

from django.conf import settings

from apps.catalog.otto_catalog import (
    OttoCatalogError,
    get_otto_catalog,
    get_otto_catalog_translation,
)


@lru_cache(maxsize=3)
def _labels(language):
    labels = {}
    try:
        base = get_otto_catalog()
        translation = get_otto_catalog_translation(language)
        for category in base["categories_by_id"].values():
            labels.setdefault(
                category["name"],
                translation["categories"].get(
                    str(category["categoryId"]), category["name"]
                ),
            )
        for group_id, attributes in base["attributes_by_group_id"].items():
            translated = (
                translation["attribute_groups"]
                .get(str(group_id), {})
                .get("attributes", {})
            )
            for attribute in attributes:
                overlay = translated.get(str(attribute["attributeId"]), {})
                if overlay.get("name"):
                    labels.setdefault(attribute["name"], overlay["name"])
                for original, label in zip(
                    attribute.get("allowedValues", []), overlay.get("allowedValues", [])
                ):
                    labels.setdefault(original, label)
    except OttoCatalogError:
        pass  # eBay-specific labels remain usable if the independent OTTO overlay is unavailable.
    translation_file = (
        settings.BASE_DIR / "data" / "ebay" / "translations" / f"{language}.json"
    )
    payload = json.loads(translation_file.read_text(encoding="utf-8"))
    if payload.get("language") != language or not isinstance(
        payload.get("labels"), dict
    ):
        raise ValueError("eBay catalog translation data is invalid.")
    labels.update(payload["labels"])
    return labels


def localize_catalog(payload, language):
    if language == "de":
        return payload
    result = deepcopy(payload)
    labels = _labels(language)
    for category in [
        *result.get("categories", []),
        result.get("category"),
        result.get("parent"),
    ]:
        if category:
            category["label"] = labels.get(category["name"], category["name"])
    for aspect in result.get("aspects", []):
        aspect["label"] = labels.get(
            aspect["localizedAspectName"], aspect["localizedAspectName"]
        )
        for value in aspect.get("aspectValues", []):
            value["label"] = labels.get(
                value["localizedValue"], value["localizedValue"]
            )
    return result
