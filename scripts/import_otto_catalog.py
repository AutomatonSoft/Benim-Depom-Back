"""Download the immutable OTTO category catalog into project JSON files.

The script deliberately writes temporary files first and replaces the catalog
only after all external requests have completed successfully.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_BASE_URL = "https://okb.automatonsoft.de/extermal"
PAGE_SIZE = 2000
ATTRIBUTE_REQUEST_DELAY_SECONDS = 0.1
ATTRIBUTE_REQUEST_WORKERS = 8
REQUEST_TIMEOUT_SECONDS = 60
ROOT_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT_DIR / "data" / "otto"


def request_json(path: str, **params: object) -> dict:
    query = urlencode(params)
    url = f"{API_BASE_URL}/{path}" + (f"?{query}" if query else "")
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return json.load(response)
    except (HTTPError, URLError) as exc:
        raise RuntimeError(f"Request failed: {url}: {exc}") from exc


def load_all_categories() -> list[dict]:
    categories: list[dict] = []
    page = 0
    while True:
        response = request_json("categories", page=page, limit=PAGE_SIZE)
        batch = response.get("categories", [])
        if not isinstance(batch, list):
            raise RuntimeError(f"Unexpected categories response on page {page}.")
        categories.extend(batch)
        print(f"Categories page {page}: {len(batch)}; total: {len(categories)}")
        if len(batch) < PAGE_SIZE:
            return categories
        page += 1


def canonical_hash(attributes: list[dict]) -> str:
    payload = json.dumps(attributes, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_attributes_by_group(categories: list[dict]) -> dict[str, dict]:
    groups: dict[int, list[dict]] = defaultdict(list)
    for category in categories:
        groups[int(category["category_group_id"])].append(category)

    def load_one(group_id: int, group_categories: list[dict]) -> tuple[int, dict]:
        representative = group_categories[0]
        response = request_json("attributes", categoryId=representative["categoryId"])
        attributes = response.get("attributes", [])
        if not isinstance(attributes, list):
            raise RuntimeError(f"Unexpected attributes response for group {group_id}.")
        time.sleep(ATTRIBUTE_REQUEST_DELAY_SECONDS)
        return group_id, {
            "category_group": representative["category_group"],
            "category_group_id": group_id,
            "representative_category_id": representative["categoryId"],
            "attribute_count": len(attributes),
            "attributes_hash": canonical_hash(attributes),
            "attributes": attributes,
        }

    completed: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=ATTRIBUTE_REQUEST_WORKERS) as executor:
        futures = [
            executor.submit(load_one, group_id, group_categories)
            for group_id, group_categories in groups.items()
        ]
        for position, future in enumerate(as_completed(futures), start=1):
            group_id, payload = future.result()
            completed[group_id] = payload
            if position % 25 == 0 or position == len(futures):
                print(f"Attributes loaded: {position}/{len(futures)}")

    return {str(group_id): completed[group_id] for group_id in sorted(completed)}


def write_json_atomically(path: Path, payload: object) -> None:
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    categories = load_all_categories()
    attributes_by_group = load_attributes_by_group(categories)

    categories_payload = {
        "source": f"{API_BASE_URL}/categories",
        "category_count": len(categories),
        "categories": categories,
    }
    attributes_payload = {
        "source": f"{API_BASE_URL}/attributes?categoryId={{categoryId}}",
        "group_count": len(attributes_by_group),
        "groups": attributes_by_group,
    }
    write_json_atomically(OUTPUT_DIR / "categories.json", categories_payload)
    write_json_atomically(OUTPUT_DIR / "attributes_by_group.json", attributes_payload)
    print(f"Done: {len(categories)} categories, {len(attributes_by_group)} category groups.")


if __name__ == "__main__":
    main()
