from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.conf import settings

_CONTACT_FIELDS = ("name", "address", "phone_number", "email_address")


def kaufland_gpsr_path() -> Path:
    """JSON file with EU responsible-person contacts per JV/XL account."""
    return Path(settings.KAUFLAND_GPSR_JSON_PATH)


def gpsr_payload_for_account(account: str) -> dict[str, Any] | None:
    """Build manufacturer + product_safety_contact for Kaufland product-data."""
    contact = _contact_for_account(account)
    if contact is None:
        return None
    return {
        "manufacturer": [contact["name"]],
        "product_safety_contact": {
            "name": contact["name"],
            "address": contact["address"],
            "phone_number": contact["phone_number"],
            "email_address": contact["email_address"],
        },
    }


def _contact_for_account(account: str) -> dict[str, str] | None:
    path = kaufland_gpsr_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    raw = payload.get(account)
    if not isinstance(raw, dict):
        return None
    contact: dict[str, str] = {}
    for field in _CONTACT_FIELDS:
        value = str(raw.get(field) or "").strip()
        if not value:
            return None
        contact[field] = value
    return contact
