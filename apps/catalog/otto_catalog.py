from __future__ import annotations

import json
from functools import lru_cache
from hashlib import sha256
from typing import Any

from django.conf import settings

CATALOG_DIR = settings.BASE_DIR / "data" / "otto"
CATEGORIES_FILE = CATALOG_DIR / "categories.json"
ATTRIBUTES_FILE = CATALOG_DIR / "attributes_by_group.json"
TRANSLATIONS_DIR = CATALOG_DIR / "translations"
SUPPORTED_OTTO_CATALOG_LANGUAGES = frozenset({"de", "en", "tr"})


class OttoCatalogError(Exception):
    """The local OTTO catalog is unavailable or malformed."""


class UnsupportedOttoCatalogLanguage(OttoCatalogError):
    """The requested OTTO catalog translation is not available."""


@lru_cache(maxsize=1)
def get_otto_catalog() -> dict[str, Any]:
    """
    Loads JSON files only once per Django process and builds hash indexes.

    All access after the first call happens from RAM.
    """
    try:
        categories_payload = json.loads(CATEGORIES_FILE.read_text(encoding="utf-8"))
        attributes_payload = json.loads(ATTRIBUTES_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise OttoCatalogError("OTTO catalog JSON files were not found.") from exc
    except json.JSONDecodeError as exc:
        raise OttoCatalogError("OTTO catalog JSON contains invalid JSON.") from exc

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
            "representative_category_id": group_payload["representative_category_id"],
        }
        attributes_by_group_id[group_id] = attributes
        attributes_by_id_by_group_id[group_id] = {
            int(attribute["attributeId"]): attribute for attribute in attributes
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


def _file_sha256(path) -> str:
    return sha256(
        path.read_text(encoding="utf-8").replace("\r\n", "\n").encode("utf-8")
    ).hexdigest()


@lru_cache(maxsize=3)
def get_otto_catalog_translation(language: str) -> dict[str, Any] | None:
    """
    Load one translated catalog overlay once per Django process.

    The base German catalog stays the source of truth for IDs and technical
    fields. A translation contains visible strings only and is looked up by
    existing IDs, so API requests do not read JSON files from disk.
    """
    normalized_language = language.casefold()
    if normalized_language not in SUPPORTED_OTTO_CATALOG_LANGUAGES:
        raise UnsupportedOttoCatalogLanguage(
            f"Unsupported OTTO catalog language: {language}."
        )

    if normalized_language == "de":
        return None

    translation_dir = TRANSLATIONS_DIR / normalized_language
    categories_translation_file = translation_dir / "categories.json"
    attributes_translation_file = translation_dir / "attributes_by_group.json"

    try:
        categories_translation = json.loads(
            categories_translation_file.read_text(encoding="utf-8")
        )
        attributes_translation = json.loads(
            attributes_translation_file.read_text(encoding="utf-8")
        )
    except FileNotFoundError as exc:
        raise UnsupportedOttoCatalogLanguage(
            f"OTTO catalog translation '{normalized_language}' is unavailable."
        ) from exc
    except json.JSONDecodeError as exc:
        raise OttoCatalogError(
            "OTTO catalog translation contains invalid JSON."
        ) from exc

    source = categories_translation.get("source")
    if not isinstance(source, dict):
        raise OttoCatalogError("OTTO catalog translation has no source metadata.")

    if source.get("categories_sha256") != _file_sha256(CATEGORIES_FILE):
        raise OttoCatalogError("OTTO category translation is out of date.")

    if source.get("attributes_by_group_sha256") != _file_sha256(ATTRIBUTES_FILE):
        raise OttoCatalogError("OTTO attribute translation is out of date.")

    if (
        categories_translation.get("language") != normalized_language
        or attributes_translation.get("language") != normalized_language
    ):
        raise OttoCatalogError("OTTO catalog translation language metadata is invalid.")

    categories = categories_translation.get("categories")
    groups = categories_translation.get("groups")
    attribute_groups = attributes_translation.get("groups")
    if not all(
        isinstance(value, dict) for value in (categories, groups, attribute_groups)
    ):
        raise OttoCatalogError("OTTO catalog translation has an unexpected structure.")

    return {
        "categories": categories,
        "groups": groups,
        "attribute_groups": attribute_groups,
    }


def clear_otto_catalog_cache() -> None:
    """
    Needed only after manually replacing JSON files without restarting Django.
    """
    get_otto_catalog.cache_clear()
    get_otto_catalog_translation.cache_clear()
