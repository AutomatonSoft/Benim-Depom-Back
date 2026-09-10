"""Brand mark appended to every marketplace listing title."""

from __future__ import annotations

import re

LISTING_BRAND_SUFFIX = " (BD)"
_EXISTING_MARK_RE = re.compile(r"\s*\(bd\)\s*$", re.IGNORECASE)


def with_listing_brand_mark(title: str, *, max_length: int) -> str:
    """Append `` (BD)`` once, keeping the result within ``max_length``."""

    text = str(title or "").strip()
    if not text:
        return text

    base = _EXISTING_MARK_RE.sub("", text).rstrip()
    suffix = LISTING_BRAND_SUFFIX
    available = max_length - len(suffix)
    if available < 1:
        return suffix.strip()[:max_length]
    if len(base) > available:
        base = base[:available].rstrip()
    return f"{base}{suffix}"
