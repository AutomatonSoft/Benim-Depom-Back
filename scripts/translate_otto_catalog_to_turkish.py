"""Create and collect a one-off OpenAI Batch translation of the OTTO catalog.

The script never changes the German source files. ``submit`` uploads a batch to
OpenAI and stores its ID in a local work directory. OpenAI can process that
batch while this computer is switched off. ``collect`` must be run later after
the batch is completed; it validates every response and writes static Turkish
translation overlays into ``data/otto/translations/tr``.

Examples
--------
python scripts/translate_otto_catalog_to_turkish.py dry-run
python scripts/translate_otto_catalog_to_turkish.py submit
python scripts/translate_otto_catalog_to_turkish.py collect

``OPENAI_API_KEY`` must be present in the process environment. The script does
not print, persist, or otherwise expose that value.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openai import OpenAI

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_DIR = ROOT_DIR / "data" / "otto"
DEFAULT_OUTPUT_DIR = DEFAULT_SOURCE_DIR / "translations" / "tr"
DEFAULT_WORK_DIR = ROOT_DIR / ".translation-work" / "otto-tr"
MODEL = "gpt-5-mini"
LANGUAGE = "tr"
LANGUAGE_NAME = "Turkish"
BATCH_PREFIX = "otto-tr"
TRANSLATION_SCHEMA_NAME = "otto_turkish_catalog_translations"
RETRY_SCHEMA_NAME = "otto_turkish_catalog_retry"
TRANSLATION_PURPOSE = "otto_catalog_tr_translation"
MAX_ENTRIES_PER_REQUEST = 35
MAX_SOURCE_CHARS_PER_REQUEST = 10_000


class TranslationError(Exception):
    """The translation batch or its local files are invalid."""


def read_json(path: Path) -> dict[str, Any]:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TranslationError(f"Required source file is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise TranslationError(f"Invalid JSON in source file: {path}") from exc

    if not isinstance(result, dict):
        raise TranslationError(f"Expected a JSON object in {path}")
    return result


def json_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json_atomically(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)


def non_empty_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def make_entry(*, key: str, text: str, context: str) -> dict[str, str]:
    return {"key": key, "text": text, "context": context}


def build_entries(
    categories_payload: dict[str, Any],
    attributes_payload: dict[str, Any],
) -> list[dict[str, str]]:
    """Extract only human-facing German strings, each with a stable key."""

    categories = categories_payload.get("categories")
    groups = attributes_payload.get("groups")
    if not isinstance(categories, list) or not isinstance(groups, dict):
        raise TranslationError("The OTTO source JSON has an unexpected structure.")

    entries: list[dict[str, str]] = []
    seen_group_ids: set[int] = set()

    for category in categories:
        if not isinstance(category, dict):
            raise TranslationError("A category entry is not an object.")

        category_id = int(category["categoryId"])
        group_id = int(category["category_group_id"])
        group_name = non_empty_string(category.get("category_group"))
        category_name = non_empty_string(category.get("name"))

        if group_name and group_id not in seen_group_ids:
            entries.append(
                make_entry(
                    key=f"group:{group_id}",
                    text=group_name,
                    context="OTTO marketplace category group name",
                )
            )
            seen_group_ids.add(group_id)

        if category_name:
            entries.append(
                make_entry(
                    key=f"category:{category_id}",
                    text=category_name,
                    context=(
                        "OTTO marketplace selectable category name; preserve "
                        "numbers, product codes, and meaningful punctuation"
                    ),
                )
            )

    for raw_group_id, group in groups.items():
        if not isinstance(group, dict):
            raise TranslationError("An attribute group entry is not an object.")

        group_id = int(raw_group_id)
        group_name = non_empty_string(group.get("category_group"))
        if group_name and group_id not in seen_group_ids:
            entries.append(
                make_entry(
                    key=f"group:{group_id}",
                    text=group_name,
                    context="OTTO marketplace category group name",
                )
            )
            seen_group_ids.add(group_id)

        attributes = group.get("attributes")
        if not isinstance(attributes, list):
            raise TranslationError(f"Attributes for group {group_id} are not a list.")

        for attribute in attributes:
            if not isinstance(attribute, dict):
                raise TranslationError("An attribute entry is not an object.")

            attribute_id = int(attribute["attributeId"])
            prefix = f"attribute:{group_id}:{attribute_id}"
            fields = (
                ("name", "OTTO product attribute name"),
                ("attributeGroup", "OTTO product attribute section name"),
                ("description", "OTTO product attribute help text"),
                ("unitDisplayName", "human-readable measurement unit"),
            )
            for field, context in fields:
                value = non_empty_string(attribute.get(field))
                if value:
                    entries.append(
                        make_entry(
                            key=f"{prefix}:{field}",
                            text=value,
                            context=context,
                        )
                    )

            allowed_values = attribute.get("allowedValues") or []
            if not isinstance(allowed_values, list):
                raise TranslationError(
                    f"allowedValues for attribute {attribute_id} are not a list."
                )

            for index, value in enumerate(allowed_values):
                text = non_empty_string(value)
                if text:
                    entries.append(
                        make_entry(
                            key=f"{prefix}:allowedValues:{index}",
                            text=text,
                            context="allowed selectable value for an OTTO product attribute",
                        )
                    )

    keys = [entry["key"] for entry in entries]
    if len(keys) != len(set(keys)):
        raise TranslationError("The translation input contains duplicate stable keys.")

    return entries


def chunk_entries(entries: list[dict[str, str]]) -> list[list[dict[str, str]]]:
    chunks: list[list[dict[str, str]]] = []
    current: list[dict[str, str]] = []
    current_size = 0

    for entry in entries:
        entry_size = len(entry["text"]) + len(entry["context"]) + len(entry["key"])
        if current and (
            len(current) >= MAX_ENTRIES_PER_REQUEST
            or current_size + entry_size > MAX_SOURCE_CHARS_PER_REQUEST
        ):
            chunks.append(current)
            current = []
            current_size = 0

        current.append(entry)
        current_size += entry_size

    if current:
        chunks.append(current)

    return chunks


def deduplicate_entries(
    entries: list[dict[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Translate an identical source string once, then reuse it by stable ID."""

    translation_entries: list[dict[str, str]] = []
    resolved_entries: list[dict[str, str]] = []
    keys_by_source: dict[tuple[str, str], str] = {}

    for entry in entries:
        source = (entry["text"], entry["context"])
        translation_key = keys_by_source.get(source)
        if translation_key is None:
            source_digest = hashlib.sha256(
                f"{entry['context']}\0{entry['text']}".encode("utf-8")
            ).hexdigest()
            translation_key = f"source:{source_digest}"
            keys_by_source[source] = translation_key
            translation_entries.append(
                make_entry(
                    key=translation_key,
                    text=entry["text"],
                    context=entry["context"],
                )
            )

        resolved_entries.append({**entry, "translation_key": translation_key})

    return resolved_entries, translation_entries


TRANSLATION_SCHEMA = {
    "type": "object",
    "properties": {
        "translations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["key", "text"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["translations"],
    "additionalProperties": False,
}

INSTRUCTIONS = """
Translate German OTTO marketplace catalog labels to clear, natural Turkish.

Rules:
- Translate only the supplied `text` field and return every supplied `key` once.
- Keep each `key` byte-for-byte unchanged.
- Preserve numbers, model codes, dimensions, units, brand names, and technical
  identifiers when they must not be translated.
- Do not add facts, explanations, markdown, quotes, or labels.
- For product attributes, use concise terminology that a Turkish seller can
  understand in a product form.
- Return only JSON matching the supplied schema.
""".strip()


def batch_line(custom_id: str, entries: list[dict[str, str]], model: str) -> dict[str, Any]:
    return {
        "custom_id": custom_id,
        "method": "POST",
        "url": "/v1/responses",
        "body": {
            "model": model,
            "instructions": INSTRUCTIONS,
            "input": json.dumps({"target_language": LANGUAGE, "items": entries}),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": TRANSLATION_SCHEMA_NAME,
                    "strict": True,
                    "schema": TRANSLATION_SCHEMA,
                }
            },
        },
    }


def make_state(
    *,
    batch_id: str,
    model: str,
    entries: list[dict[str, str]],
    unique_entries: list[dict[str, str]],
    chunks: list[list[dict[str, str]]],
    categories_path: Path,
    attributes_path: Path,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "batch_id": batch_id,
        "language": LANGUAGE,
        "model": model,
        "created_at": datetime.now(UTC).isoformat(),
        "destination_entry_count": len(entries),
        "unique_translation_count": len(unique_entries),
        "request_count": len(chunks),
        "source": {
            "categories_sha256": json_sha256(categories_path),
            "attributes_by_group_sha256": json_sha256(attributes_path),
        },
    }


def prepare(
    source_dir: Path,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    list[dict[str, str]],
    list[dict[str, str]],
    list[list[dict[str, str]]],
]:
    categories_path = source_dir / "categories.json"
    attributes_path = source_dir / "attributes_by_group.json"
    categories_payload = read_json(categories_path)
    attributes_payload = read_json(attributes_path)
    entries = build_entries(categories_payload, attributes_payload)
    resolved_entries, unique_entries = deduplicate_entries(entries)
    chunks = chunk_entries(unique_entries)
    return categories_payload, attributes_payload, resolved_entries, unique_entries, chunks


def command_dry_run(args: argparse.Namespace) -> int:
    _, _, entries, unique_entries, chunks = prepare(args.source_dir)
    category_entries = sum(entry["key"].startswith("category:") for entry in entries)
    group_entries = sum(entry["key"].startswith("group:") for entry in entries)
    attribute_entries = len(entries) - category_entries - group_entries
    print(
        f"Prepared {LANGUAGE_NAME} translation input: "
        f"{len(entries)} catalog fields "
        f"({group_entries} groups, {category_entries} categories, "
        f"{attribute_entries} attribute fields), reduced to "
        f"{len(unique_entries)} unique strings in {len(chunks)} Batch requests."
    )
    return 0


def command_submit(args: argparse.Namespace) -> int:
    if not os.environ.get("OPENAI_API_KEY"):
        raise TranslationError("OPENAI_API_KEY is not configured in this process.")

    _, _, entries, unique_entries, chunks = prepare(args.source_dir)
    args.work_dir.mkdir(parents=True, exist_ok=True)
    state_path = args.work_dir / "batch-state.json"
    input_path = args.work_dir / "batch-input.jsonl"

    if state_path.exists() and not args.replace_state:
        raise TranslationError(
            f"A batch state already exists at {state_path}. "
            "Use collect, or pass --replace-state only after deliberately "
            "abandoning the previous batch."
        )

    with input_path.open("w", encoding="utf-8", newline="\n") as file:
        for index, chunk in enumerate(chunks, start=1):
            file.write(
                json.dumps(
                    batch_line(f"{BATCH_PREFIX}-{index:05d}", chunk, args.model),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=120, max_retries=2)
    with input_path.open("rb") as input_file:
        uploaded_file = client.files.create(file=input_file, purpose="batch")

    batch = client.batches.create(
        input_file_id=uploaded_file.id,
        endpoint="/v1/responses",
        completion_window="24h",
        metadata={"purpose": TRANSLATION_PURPOSE, "language": LANGUAGE},
    )
    state = make_state(
        batch_id=batch.id,
        model=args.model,
        entries=entries,
        unique_entries=unique_entries,
        chunks=chunks,
        categories_path=args.source_dir / "categories.json",
        attributes_path=args.source_dir / "attributes_by_group.json",
    )
    write_json_atomically(state_path, state)
    print(
        f"Batch submitted: {batch.id}. "
        f"Requests: {len(chunks)}; unique strings: {len(unique_entries)}. "
        f"Run `collect` later; OpenAI continues processing independently."
    )
    return 0


def command_status(args: argparse.Namespace) -> int:
    """Print concise provider progress for the saved Batch without changing it."""

    if not os.environ.get("OPENAI_API_KEY"):
        raise TranslationError("OPENAI_API_KEY is not configured in this process.")

    state = read_json(args.work_dir / "batch-state.json")
    batch_id = state.get("batch_id")
    if state.get("language") != LANGUAGE or not isinstance(batch_id, str):
        raise TranslationError(
            f"The saved Batch state is not a {LANGUAGE_NAME} catalog Batch."
        )

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=60, max_retries=2)
    batch = client.batches.retrieve(batch_id)
    counts = batch.request_counts
    print(
        f"Batch {batch.id}: {batch.status}; "
        f"completed={counts.completed}, failed={counts.failed}, total={counts.total}."
    )
    return 0


def extract_output_text(response_body: dict[str, Any]) -> str:
    output_text = response_body.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    for output in response_body.get("output", []):
        if not isinstance(output, dict):
            continue
        for content in output.get("content", []):
            if not isinstance(content, dict):
                continue
            if content.get("type") == "output_text" and isinstance(
                content.get("text"), str
            ):
                return content["text"]
    raise TranslationError("A successful Batch response has no output text.")


def read_batch_translations(
    *, client: OpenAI,
    batch_id: str,
    expected_keys_by_request: dict[str, set[str]],
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    batch = client.batches.retrieve(batch_id)
    if batch.status != "completed":
        raise TranslationError(
            f"Batch {batch_id} is {batch.status}; it is not ready to collect yet."
        )
    if not batch.output_file_id:
        raise TranslationError("The completed Batch has no output file.")

    output_text = client.files.content(batch.output_file_id).text
    translations: dict[str, str] = {}
    failures: list[dict[str, Any]] = []
    received_request_ids: set[str] = set()

    for line in output_text.splitlines():
        record = json.loads(line)
        custom_id = record.get("custom_id")
        response = record.get("response")
        if custom_id not in expected_keys_by_request:
            failures.append(
                {"custom_id": custom_id, "reason": "unknown request ID"}
            )
            continue
        received_request_ids.add(custom_id)
        expected_keys = expected_keys_by_request[custom_id]
        if not isinstance(response, dict) or response.get("status_code") != 200:
            failures.append(
                {
                    "custom_id": custom_id,
                    "reason": "request did not return HTTP 200",
                    "retry_keys": sorted(expected_keys),
                }
            )
            continue

        body = response.get("body")
        if not isinstance(body, dict):
            failures.append(
                {
                    "custom_id": custom_id,
                    "reason": "response body is not an object",
                    "retry_keys": sorted(expected_keys),
                }
            )
            continue

        try:
            parsed = json.loads(extract_output_text(body))
            items = parsed["translations"]
        except (KeyError, TypeError, json.JSONDecodeError, TranslationError):
            failures.append(
                {
                    "custom_id": custom_id,
                    "reason": "invalid structured translation output",
                    "retry_keys": sorted(expected_keys),
                }
            )
            continue

        received: dict[str, str] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            text = non_empty_string(item.get("text"))
            if isinstance(key, str) and text:
                received[key] = text

        if set(received) != expected_keys:
            translations.update(
                {
                    key: text
                    for key, text in received.items()
                    if key in expected_keys
                }
            )
            failures.append(
                {
                    "custom_id": custom_id,
                    "reason": "response keys do not match input keys",
                    "missing_keys": sorted(expected_keys - set(received)),
                    "unexpected_keys": sorted(set(received) - expected_keys),
                    "retry_keys": sorted(expected_keys - set(received)),
                }
            )
            continue
        translations.update(received)

    for custom_id, expected_keys in expected_keys_by_request.items():
        if custom_id not in received_request_ids:
            failures.append(
                {
                    "custom_id": custom_id,
                    "reason": "request is missing from Batch output",
                    "retry_keys": sorted(expected_keys),
                }
            )
    return translations, failures


def retry_missing_translations(
    *,
    client: OpenAI,
    model: str,
    unique_entries: list[dict[str, str]],
    failures: list[dict[str, Any]],
) -> dict[str, str]:
    """Recover only missing entries with short IDs that the model cannot mistype."""

    retry_keys = sorted(
        {
            key
            for failure in failures
            for key in failure.get("retry_keys", [])
            if isinstance(key, str)
        }
    )
    if not retry_keys:
        raise TranslationError("Batch output has a non-retryable validation error.")

    entries_by_key = {entry["key"]: entry for entry in unique_entries}
    try:
        retry_entries = [entries_by_key[key] for key in retry_keys]
    except KeyError as exc:
        raise TranslationError("A retry key is absent from the translation input.") from exc

    recovered: dict[str, str] = {}
    for offset in range(0, len(retry_entries), 5):
        source_chunk = retry_entries[offset : offset + 5]
        alias_to_source_key = {
            f"retry-{offset + index + 1:04d}": entry["key"]
            for index, entry in enumerate(source_chunk)
        }
        request_entries = [
            {
                "key": alias,
                "text": entries_by_key[source_key]["text"],
                "context": entries_by_key[source_key]["context"],
            }
            for alias, source_key in alias_to_source_key.items()
        ]
        response = client.responses.create(
            model=model,
            instructions=INSTRUCTIONS,
            input=json.dumps({"target_language": LANGUAGE, "items": request_entries}),
            text={
                "format": {
                    "type": "json_schema",
                    "name": RETRY_SCHEMA_NAME,
                    "strict": True,
                    "schema": TRANSLATION_SCHEMA,
                }
            },
        )
        try:
            parsed = json.loads((response.output_text or "").strip())
            received = {
                item["key"]: non_empty_string(item["text"])
                for item in parsed["translations"]
                if isinstance(item, dict)
                and isinstance(item.get("key"), str)
                and non_empty_string(item.get("text"))
            }
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise TranslationError("Retry returned invalid structured JSON.") from exc

        if set(received) != set(alias_to_source_key):
            raise TranslationError("Retry response keys do not match its input keys.")
        recovered.update(
            {
                alias_to_source_key[alias]: text
                for alias, text in received.items()
                if text is not None
            }
        )

    print(f"Recovered {len(recovered)} missing translations with direct retries.")
    return recovered


def build_output_payloads(
    *,
    categories_payload: dict[str, Any],
    attributes_payload: dict[str, Any],
    translations: dict[str, str],
    source_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    localized_categories = {"groups": {}, "categories": {}}
    for category in categories_payload["categories"]:
        group_id = str(int(category["category_group_id"]))
        category_id = str(int(category["categoryId"]))
        localized_categories["groups"].setdefault(
            group_id,
            translations[f"group:{group_id}"],
        )
        localized_categories["categories"][category_id] = translations[
            f"category:{category_id}"
        ]

    localized_attributes: dict[str, Any] = {"groups": {}}
    for raw_group_id, group in attributes_payload["groups"].items():
        group_id = str(int(raw_group_id))
        localized_group = {
            "category_group": translations[f"group:{group_id}"],
            "attributes": {},
        }
        for attribute in group["attributes"]:
            attribute_id = str(int(attribute["attributeId"]))
            prefix = f"attribute:{group_id}:{attribute_id}"
            translated_attribute: dict[str, Any] = {}
            for field in ("name", "attributeGroup", "description", "unitDisplayName"):
                if non_empty_string(attribute.get(field)):
                    translated_attribute[field] = translations[f"{prefix}:{field}"]
            allowed_values = attribute.get("allowedValues") or []
            if allowed_values:
                translated_attribute["allowedValues"] = [
                    (
                        translations[f"{prefix}:allowedValues:{index}"]
                        if non_empty_string(value)
                        else value
                    )
                    for index, value in enumerate(allowed_values)
                ]
            localized_group["attributes"][attribute_id] = translated_attribute
        localized_attributes["groups"][group_id] = localized_group

    metadata = {
        "schema_version": 1,
        "language": LANGUAGE,
        "generated_at": datetime.now(UTC).isoformat(),
        "source": {
            "categories_sha256": json_sha256(source_dir / "categories.json"),
            "attributes_by_group_sha256": json_sha256(
                source_dir / "attributes_by_group.json"
            ),
        },
    }
    return (
        {**metadata, **localized_categories},
        {**metadata, **localized_attributes},
    )


def command_collect(args: argparse.Namespace) -> int:
    if not os.environ.get("OPENAI_API_KEY"):
        raise TranslationError("OPENAI_API_KEY is not configured in this process.")

    state_path = args.work_dir / "batch-state.json"
    state = read_json(state_path)
    if state.get("language") != LANGUAGE or not isinstance(state.get("batch_id"), str):
        raise TranslationError(
            f"The saved Batch state is not a {LANGUAGE_NAME} catalog Batch."
        )

    (
        categories_payload,
        attributes_payload,
        entries,
        unique_entries,
        chunks,
    ) = prepare(args.source_dir)
    source = state.get("source", {})
    if source.get("categories_sha256") != json_sha256(args.source_dir / "categories.json") or (
        source.get("attributes_by_group_sha256")
        != json_sha256(args.source_dir / "attributes_by_group.json")
    ):
        raise TranslationError(
            "Source catalog changed after submission. Refusing to combine "
            "translations with different source data."
        )

    expected_keys_by_request = {
        f"{BATCH_PREFIX}-{index:05d}": {entry["key"] for entry in chunk}
        for index, chunk in enumerate(chunks, start=1)
    }
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=120, max_retries=2)
    unique_translations, failures = read_batch_translations(
        client=client,
        batch_id=state["batch_id"],
        expected_keys_by_request=expected_keys_by_request,
    )
    if failures and not args.repair_missing:
        errors_path = args.work_dir / "collection-errors.json"
        write_json_atomically(
            errors_path,
            {
                "batch_id": state["batch_id"],
                "failed_request_count": len(failures),
                "failures": failures,
            },
        )
        raise TranslationError(
            f"Collection validation failed for {len(failures)} requests. "
            f"Details were written to {errors_path}."
        )
    if failures:
        model = state.get("model")
        if not isinstance(model, str) or not model:
            raise TranslationError("The saved Batch state does not contain a model.")
        unique_translations.update(
            retry_missing_translations(
                client=client,
                model=model,
                unique_entries=unique_entries,
                failures=failures,
            )
        )

    if len(unique_translations) != len(unique_entries):
        raise TranslationError("Collection is still missing one or more translations.")
    translations = {
        entry["key"]: unique_translations[entry["translation_key"]]
        for entry in entries
    }
    categories_output, attributes_output = build_output_payloads(
        categories_payload=categories_payload,
        attributes_payload=attributes_payload,
        translations=translations,
        source_dir=args.source_dir,
    )

    categories_path = args.output_dir / "categories.json"
    attributes_path = args.output_dir / "attributes_by_group.json"
    if (categories_path.exists() or attributes_path.exists()) and not args.overwrite:
        raise TranslationError(
            f"{LANGUAGE_NAME} translation files already exist. Pass --overwrite only "
            "after reviewing the current files."
        )

    write_json_atomically(categories_path, categories_output)
    write_json_atomically(attributes_path, attributes_output)
    print(
        f"{LANGUAGE_NAME} translation files written successfully: "
        f"{categories_path} and {attributes_path}. "
        f"Validated translations: {len(translations)}."
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("dry-run", "submit", "status", "collect"))
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument(
        "--replace-state",
        action="store_true",
        help="Allow submit to replace a saved Batch ID after deliberate cancellation.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow collect to replace already reviewed translation JSON files.",
    )
    parser.add_argument(
        "--repair-missing",
        action="store_true",
        help="Retry only incomplete Batch entries with short technical IDs.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "dry-run":
            return command_dry_run(args)
        if args.command == "submit":
            return command_submit(args)
        if args.command == "status":
            return command_status(args)
        return command_collect(args)
    except TranslationError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
