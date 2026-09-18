from __future__ import annotations

import json
import re
import tempfile
import threading
from pathlib import Path

from django.conf import settings

_LOCK = threading.Lock()
_PHONE_MAX_LEN = 32
_DIGIT_RE = re.compile(r"\D+")


class InvalidContactPhone(ValueError):
    pass


def contact_json_path() -> Path:
    return Path(settings.CONTACT_JSON_PATH)


def whatsapp_url_for(phone: str) -> str | None:
    digits = _digits(phone)
    if not digits:
        return None
    return f"https://wa.me/{digits}"


def contact_payload(phone: str | None = None) -> dict[str, str | None]:
    if phone is None:
        phone = load_contact_phone()
    return {
        "phone": phone,
        "whatsapp_url": whatsapp_url_for(phone),
    }


def load_contact_phone() -> str:
    path = contact_json_path()
    if not path.exists():
        return ""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""

    if not isinstance(payload, dict):
        return ""

    phone = payload.get("phone", "")
    return phone.strip() if isinstance(phone, str) else ""


def save_contact_phone(raw: str) -> str:
    phone = normalize_contact_phone(raw)
    path = contact_json_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"phone": phone}, ensure_ascii=False, indent=2) + "\n"

    with _LOCK:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".contact-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(payload)
            temp_name = handle.name
        Path(temp_name).replace(path)

    return phone


def normalize_contact_phone(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        return ""

    if len(value) > _PHONE_MAX_LEN:
        raise InvalidContactPhone("Phone number is too long.")

    digits = _digits(value)
    if len(digits) < 8 or len(digits) > 15:
        raise InvalidContactPhone(
            "Enter a valid international WhatsApp number with country code."
        )
    if digits.startswith("0"):
        raise InvalidContactPhone(
            "Enter a valid international WhatsApp number with country code."
        )

    return f"+{digits}"


def _digits(value: str) -> str:
    compact = value.strip()
    if compact.startswith("00"):
        compact = compact[2:]
    return _DIGIT_RE.sub("", compact)
