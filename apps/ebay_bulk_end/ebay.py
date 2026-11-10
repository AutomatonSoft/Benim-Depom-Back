from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

import requests
from urllib3.util.retry import Retry

from apps.common.marketplace_http import build_marketplace_session
from apps.marketplace.ebay.client import api_base_url

from .domain import (
    TRADING_NS,
    child,
    descendants,
    is_ack_success,
    local_name,
    text,
    trading_element,
)
from .errors import BulkEndError, EBayRateLimitError, EBayTransportError

TRADING_VERSION = "1489"
SITE_ID = "77"
MAX_RESPONSE_BYTES = 20 * 1024 * 1024


def _append(parent, name, value):
    node = ET.SubElement(parent, "{" + TRADING_NS + "}" + name)
    if value is not None:
        node.text = str(value)
    return node


def _call(token, method, root, timeout=(5, 15), allow_failure=False):
    body = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    try:
        with build_marketplace_session() as session:
            # The job layer applies the bounded Retry-After policy for read-only scans.
            session.adapters["https://"].max_retries = Retry(total=0)
            session.adapters["http://"].max_retries = Retry(total=0)
            response = session.post(
                api_base_url() + "/ws/api.dll",
                data=body,
                headers={
                    "Content-Type": "text/xml",
                    "X-EBAY-API-CALL-NAME": method,
                    "X-EBAY-API-SITEID": SITE_ID,
                    "X-EBAY-API-COMPATIBILITY-LEVEL": TRADING_VERSION,
                    "X-EBAY-API-IAF-TOKEN": token,
                },
                timeout=timeout,
            )
    except requests.RequestException as error:
        raise EBayTransportError("Связь с eBay прервалась.") from error
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise BulkEndError("Ответ eBay превысил допустимый размер.")
    if response.status_code == 429:
        try:
            retry_after = min(
                60, max(1, int(response.headers.get("Retry-After", "30")))
            )
        except (TypeError, ValueError):
            retry_after = 30
        raise EBayRateLimitError(retry_after)
    if response.status_code in {401, 403}:
        raise BulkEndError(
            "eBay отклонил авторизацию или права выбранного аккаунта. Повторно авторизуйте его и проверьте доступ Trading API."
        )
    if response.status_code >= 500:
        raise EBayTransportError("eBay временно не обработал запрос.")
    if not response.ok:
        raise BulkEndError("eBay отклонил запрос.")
    if b"<!DOCTYPE" in response.content or b"<!ENTITY" in response.content:
        raise BulkEndError("eBay вернул XML с запрещённым объявлением сущностей.")
    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as error:
        raise EBayTransportError("eBay вернул некорректный ответ.") from error
    if not allow_failure:
        is_ack_success(root)
    return root


def get_active_page(token, page):
    request = trading_element("GetMyeBaySellingRequest")
    _append(request, "DetailLevel", "ReturnAll")
    _append(request, "HideVariations", "false")
    active = _append(request, "ActiveList", None)
    _append(active, "Include", "true")
    _append(active, "Sort", "ItemID")
    pagination = _append(active, "Pagination", None)
    # 15 sequential GetItem requests fit below the global 300s Celery soft limit.
    _append(pagination, "EntriesPerPage", "15")
    _append(pagination, "PageNumber", page)
    root = _call(token, "GetMyeBaySelling", request, timeout=(5, 25))
    active_response = next(iter(descendants(root, "ActiveList")), None)
    if active_response is None:
        raise BulkEndError("eBay не вернул раздел активных объявлений.")
    item_array = child(active_response, "ItemArray")
    item_nodes = [
        node
        for node in (list(item_array) if item_array is not None else [])
        if local_name(node.tag) == "Item"
    ]
    pagination_result = next(
        iter(descendants(active_response, "PaginationResult")), None
    )
    if pagination_result is None:
        raise BulkEndError("eBay не вернул данные пагинации.")
    try:
        pages = int(text(pagination_result, "TotalNumberOfPages", "1"))
        total_entries = int(text(pagination_result, "TotalNumberOfEntries", "0"))
    except ValueError as error:
        raise BulkEndError("eBay вернул некорректную пагинацию.") from error
    if total_entries and item_array is None:
        raise BulkEndError("eBay вернул неполный список объявлений.")
    view_url_by_id = {}
    for item in item_nodes:
        url = text(child(item, "ListingDetails"), "ViewItemURL")
        host = urlsplit(url).hostname or ""
        if host == "www.ebay.de":
            view_url_by_id[text(item, "ItemID")] = url
    return item_nodes, max(pages, 1), total_entries, view_url_by_id


def get_item(token, item_id):
    request = trading_element("GetItemRequest")
    _append(request, "DetailLevel", "ReturnAll")
    _append(request, "IncludeItemSpecifics", "true")
    _append(request, "ItemID", item_id)
    root = _call(token, "GetItem", request, timeout=(5, 15))
    return next(iter(descendants(root, "Item")), None)


def end_items(token, item_ids):
    if not item_ids or len(item_ids) > 10 or len(set(item_ids)) != len(item_ids):
        raise BulkEndError("EndItems принимает от 1 до 10 уникальных объявлений.")
    request = trading_element("EndItemsRequest")
    for item_id in item_ids:
        container = _append(request, "EndItemRequestContainer", None)
        _append(container, "MessageID", item_id)
        _append(container, "ItemID", item_id)
        _append(container, "EndingReason", "NotAvailable")
    root = _call(token, "EndItems", request, timeout=(5, 20), allow_failure=True)
    responses = {}
    for container in descendants(root, "EndItemResponseContainer"):
        correlation = text(container, "CorrelationID")
        ack = text(container, "Ack")
        error = next(iter(descendants(container, "Errors")), None)
        responses[correlation] = {
            "ack": ack,
            "message": text(error, "ShortMessage")[:180],
            "classification": text(error, "ErrorClassification"),
            "error_code": text(error, "ErrorCode"),
            "severity_code": text(error, "SeverityCode"),
            "end_time": text(container, "EndTime"),
        }
    if text(root, "Ack") not in {"Success", "Warning"}:
        error = next(iter(descendants(root, "Errors")), None)
        for item_id in set(item_ids) - responses.keys():
            responses[item_id] = {
                "ack": text(root, "Ack"),
                "message": text(error, "ShortMessage", "eBay не завершил объявление.")[
                    :180
                ],
                "classification": text(error, "ErrorClassification"),
                "error_code": text(error, "ErrorCode"),
                "severity_code": text(error, "SeverityCode"),
                "end_time": "",
            }
    return responses


def get_item_state(token, item_id):
    item = get_item(token, item_id)
    if item is None:
        return None
    return text(item, "ListingStatus", "Unknown")
