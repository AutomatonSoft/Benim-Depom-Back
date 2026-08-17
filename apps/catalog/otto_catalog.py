from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from django.conf import settings


CATALOG_DIR = settings.BASE_DIR / "data" / "otto"
CATEGORIES_FILE = CATALOG_DIR / "categories.json"
ATTRIBUTES_FILE = CATALOG_DIR / "attributes_by_group.json"


class OttoCatalogError(Exception):
    """The local OTTO catalog is unavailable or malformed."""


@lru_cache(maxsize=1)
def get_otto_catalog() -> dict[str, Any]:
    """
    Loads JSON files only once per Django process and builds hash indexes.

    All access after the first call happens from RAM.
    """
    try:
        categories_payload = json.loads(
            CATEGORIES_FILE.read_text(encoding="utf-8")
        )
        attributes_payload = json.loads(
            ATTRIBUTES_FILE.read_text(encoding="utf-8")
        )
    except FileNotFoundError as exc:
        raise OttoCatalogError(
            "OTTO catalog JSON files were not found."
        ) from exc
    except json.JSONDecodeError as exc:
        raise OttoCatalogError(
            "OTTO catalog JSON contains invalid JSON."
        ) from exc

    categories = categories_payload.get("categories")
    groups = attributes_payload.get("groups")

    if not isinstance(categories, list) or not isinstance(groups, dict):
        raise OttoCatalogError("OTTO catalog has an unexpected structure.")

    categories_by_id: dict[int, dict[str, Any]] = {}
    categories_by_group_id: dict[int, list[dict[str, Any]]] = {}
    groups_by_id: dict[int, dict[str, Any]] = {}
    attributes_by_group_id: dict[int, list[dict[str, Any]]] = {}
    attributes_by_id_by_group_id: dict[int, dict[int, dict[str, Any]]] = {}

    for category in categories:
        category_id = int(category["categoryId"])
        group_id = int(category["category_group_id"])

        categories_by_id[category_id] = category
        categories_by_group_id.setdefault(group_id, []).append(category)

    for raw_group_id, group_payload in groups.items():
        group_id = int(raw_group_id)
        attributes = group_payload.get("attributes", [])

        groups_by_id[group_id] = {
            "category_group_id": group_id,
            "category_group": group_payload["category_group"],
            "representative_category_id": group_payload[
                "representative_category_id"
            ],
        }
        attributes_by_group_id[group_id] = attributes
        attributes_by_id_by_group_id[group_id] = {
            int(attribute["attributeId"]): attribute
            for attribute in attributes
        }

    for group_categories in categories_by_group_id.values():
        group_categories.sort(key=lambda category: category["name"].casefold())

    return {
        "categories_by_id": categories_by_id,
        "categories_by_group_id": categories_by_group_id,
        "groups_by_id": groups_by_id,
        "attributes_by_group_id": attributes_by_group_id,
        "attributes_by_id_by_group_id": attributes_by_id_by_group_id,
    }


def clear_otto_catalog_cache() -> None:
    """
    Needed only after manually replacing JSON files without restarting Django.
    """
    get_otto_catalog.cache_clear()