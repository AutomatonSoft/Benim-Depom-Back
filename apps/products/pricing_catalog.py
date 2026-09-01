from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from django.conf import settings

PRICING_FILE = settings.BASE_DIR / "data" / "pricing.json"


@lru_cache(maxsize=1)
def get_pricing_catalog() -> dict[str, Any]:
    payload = json.loads(PRICING_FILE.read_text(encoding="utf-8"))
    return payload
    