import json
from typing import Any

from django.conf import settings


def compact_external_json(value: Any) -> Any:
    """
    Produces a JSON-safe diagnostic snapshot with bounded size.

    It is intended for responses/errors from external marketplace providers,
    not for business data that must be stored in full.
    """
    was_truncated = False

    def compact(item: Any, depth: int) -> Any:
        nonlocal was_truncated

        if depth >= settings.EXTERNAL_JSON_MAX_DEPTH:
            was_truncated = True
            return {
                "_truncated": True,
                "_reason": "maximum nesting depth reached",
            }

        if isinstance(item, dict):
            result = {}

            for index, (key, nested_value) in enumerate(item.items()):
                if index >= settings.EXTERNAL_JSON_MAX_ITEMS_PER_CONTAINER:
                    was_truncated = True
                    result["_truncated_items"] = True
                    break

                safe_key = str(key)[
                    : settings.EXTERNAL_JSON_MAX_STRING_CHARS
                ]
                result[safe_key] = compact(nested_value, depth + 1)

            return result

        if isinstance(item, (list, tuple)):
            result = []

            for index, nested_value in enumerate(item):
                if index >= settings.EXTERNAL_JSON_MAX_ITEMS_PER_CONTAINER:
                    was_truncated = True
                    result.append(
                        {
                            "_truncated": True,
                            "_reason": "maximum list length reached",
                        }
                    )
                    break

                result.append(compact(nested_value, depth + 1))

            return result

        if isinstance(item, str):
            if len(item) > settings.EXTERNAL_JSON_MAX_STRING_CHARS:
                was_truncated = True
                return (
                    item[: settings.EXTERNAL_JSON_MAX_STRING_CHARS]
                    + "...[truncated]"
                )

            return item

        if item is None or isinstance(item, (bool, int, float)):
            return item

        text = str(item)

        if len(text) > settings.EXTERNAL_JSON_MAX_STRING_CHARS:
            was_truncated = True
            return (
                text[: settings.EXTERNAL_JSON_MAX_STRING_CHARS]
                + "...[truncated]"
            )

        return text

    compacted = compact(value, depth=0)

    serialized = json.dumps(
        compacted,
        ensure_ascii=False,
    )
    serialized_bytes = serialized.encode("utf-8")

    if len(serialized_bytes) <= settings.EXTERNAL_JSON_MAX_BYTES:
        if was_truncated and isinstance(compacted, dict):
            compacted.setdefault("_truncated", True)

        return compacted

    result = {
        "_truncated": True,
        "_reason": "maximum JSON snapshot size exceeded",
        "_original_size_bytes": len(serialized_bytes),
    }
    result_size = len(
        json.dumps(
            {**result, "_preview": ""},
            ensure_ascii=False,
        ).encode("utf-8")
    )
    preview_size = min(8192, max(0, settings.EXTERNAL_JSON_MAX_BYTES - result_size))
    preview = serialized_bytes[:preview_size].decode(
        "utf-8",
        errors="ignore",
    )

    while preview:
        candidate = {**result, "_preview": preview}
        candidate_size = len(
            json.dumps(
                candidate,
                ensure_ascii=False,
            ).encode("utf-8")
        )
        if candidate_size <= settings.EXTERNAL_JSON_MAX_BYTES:
            return candidate

        preview = preview[: -(candidate_size - settings.EXTERNAL_JSON_MAX_BYTES)]

    if result_size <= settings.EXTERNAL_JSON_MAX_BYTES:
        return result

    return {"_truncated": True}
