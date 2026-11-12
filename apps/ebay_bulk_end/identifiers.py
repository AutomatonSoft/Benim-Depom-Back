import re
from datetime import timedelta

import requests
from django.utils import timezone
from urllib3.util.retry import Retry

from apps.common.marketplace_http import build_marketplace_session
from apps.marketplace.ebay.catalog import _application_token
from apps.marketplace.ebay.client import api_base_url

from .domain import MAX_MATCHES, item_identifiers, text
from .errors import BulkEndError, EBayRateLimitError, EBayTransportError
from .models import EbayListingIdentifiers
from .storage import save_job

SEARCHES_PER_TASK = 5
SEARCH_PAGE_SIZE = 200
IDENTIFIER_TTL = timedelta(hours=24)


def identifier_index(job):
    return EbayListingIdentifiers.objects.filter(
        environment=job["environment"], seller_id=job["seller_id"]
    )


def cache_item_identifiers(job, item):
    identifier_index(job).update_or_create(
        item_id=text(item, "ItemID"),
        defaults={
            "environment": job["environment"],
            "seller_id": job["seller_id"],
            "identifiers": item_identifiers(item),
            "checked_at": timezone.now(),
        },
    )


def search_identifier_page(ean, seller_username, mode, offset):
    if not seller_username or any(c in seller_username for c in "{}|,:"):
        raise BulkEndError("eBay returned an unsupported seller username.")
    try:
        with build_marketplace_session() as session:
            session.adapters["https://"].max_retries = Retry(total=0)
            response = session.get(
                api_base_url() + "/buy/browse/v1/item_summary/search",
                headers={
                    "Authorization": "Bearer " + _application_token(),
                    "X-EBAY-C-MARKETPLACE-ID": "EBAY_DE",
                },
                params={
                    mode: ean,
                    "filter": "sellers:{"
                    + seller_username
                    + "},buyingOptions:{FIXED_PRICE|AUCTION}",
                    "limit": SEARCH_PAGE_SIZE,
                    "offset": offset,
                },
                timeout=(5, 15),
            )
        if response.status_code == 429:
            raise EBayRateLimitError(30)
        if response.status_code >= 500:
            raise EBayTransportError("eBay search is temporarily unavailable.")
        if not response.ok:
            raise BulkEndError(
                f"eBay Browse search failed (HTTP {response.status_code})."
            )
        payload = response.json()
    except (requests.RequestException, ValueError) as error:
        raise EBayTransportError("eBay search could not be read.") from error
    if not isinstance(payload, dict) or payload.get("errors"):
        raise BulkEndError("eBay returned an invalid search response.")
    total = payload.get("total")
    rows = payload.get("itemSummaries", [])
    if type(total) is not int or total < 0 or not isinstance(rows, list):
        raise BulkEndError("eBay returned invalid search pagination.")
    if total > MAX_MATCHES:
        raise BulkEndError("Too many eBay search matches; reduce the EAN list.")
    ids = set()
    for row in rows:
        value = row.get("itemId", "") if isinstance(row, dict) else ""
        match = (
            re.fullmatch(r"v1\|([0-9]{1,32})\|[0-9]+", value)
            if isinstance(value, str)
            else None
        )
        if not match:
            raise BulkEndError("eBay returned an invalid search ItemID.")
        ids.add(match[1])
    next_offset = offset + len(rows)
    if next_offset < total and not rows:
        raise BulkEndError("eBay returned an incomplete search page.")
    return ids, next_offset if next_offset < total else None


def discover_identifier_candidates(job, seller_username):
    cursor = int(job.get("identifier_search_cursor", 0))
    offset = int(job.get("identifier_search_offset", 0))
    ids = set(job.get("identifier_search_ids", []))
    total = len(job["eans"]) * 2
    if cursor >= total:
        return True
    for _ in range(SEARCHES_PER_TASK):
        if cursor >= total:
            break
        mode = "q" if cursor % 2 == 0 else "gtin"
        found, next_offset = search_identifier_page(
            job["eans"][cursor // 2], seller_username, mode, offset
        )
        ids.update(found)
        if len(ids) > MAX_MATCHES:
            raise BulkEndError("Too many eBay search matches; reduce the EAN list.")
        if next_offset is None:
            cursor += 1
            offset = 0
        else:
            offset = next_offset
        job.update(
            identifier_search_cursor=cursor,
            identifier_search_offset=offset,
            identifier_search_ids=sorted(ids),
        )
        job["progress"] = {
            "stage": "searching_identifiers",
            "current": cursor,
            "total": total,
        }
        save_job(job)
    return False
