import json

import pytest
from django.test import override_settings

from apps.common.external_json import compact_external_json


@pytest.mark.unit
@override_settings(
    EXTERNAL_JSON_MAX_BYTES=10_000,
    EXTERNAL_JSON_MAX_DEPTH=3,
    EXTERNAL_JSON_MAX_ITEMS_PER_CONTAINER=2,
    EXTERNAL_JSON_MAX_STRING_CHARS=10,
)
def test_compacts_long_strings_and_container_items():
    result = compact_external_json(
        {
            "first": "12345678901",
            "second": "ok",
            "third": "must not be persisted",
        }
    )

    assert result["first"] == "1234567890...[truncated]"
    assert result["second"] == "ok"
    assert result["_truncated_items"] is True
    assert result["_truncated"] is True
    assert "third" not in result


@pytest.mark.unit
@override_settings(
    EXTERNAL_JSON_MAX_BYTES=10_000,
    EXTERNAL_JSON_MAX_DEPTH=2,
    EXTERNAL_JSON_MAX_ITEMS_PER_CONTAINER=10,
    EXTERNAL_JSON_MAX_STRING_CHARS=100,
)
def test_compacts_excessive_nesting():
    result = compact_external_json({"one": {"two": {"three": "value"}}})

    assert result["one"]["two"]["_truncated"] is True
    assert result["_truncated"] is True


@pytest.mark.unit
@override_settings(
    EXTERNAL_JSON_MAX_BYTES=300,
    EXTERNAL_JSON_MAX_DEPTH=10,
    EXTERNAL_JSON_MAX_ITEMS_PER_CONTAINER=10,
    EXTERNAL_JSON_MAX_STRING_CHARS=1_000,
)
def test_replaces_snapshot_exceeding_total_size_limit():
    result = compact_external_json({"provider_response": "x" * 2_000})

    assert result["_truncated"] is True
    assert result["_reason"] == "maximum JSON snapshot size exceeded"
    assert result["_original_size_bytes"] > 300
    assert result["_preview"]
    assert len(json.dumps(result).encode("utf-8")) <= 300
