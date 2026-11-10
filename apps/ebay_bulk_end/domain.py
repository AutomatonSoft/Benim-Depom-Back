from xml.etree import ElementTree as ET

from django.conf import settings

from .errors import BulkEndError

MAX_EANS = int(getattr(settings, "EBAY_BULK_END_MAX_EANS", 500))
MAX_MATCHES = 2000
TRADING_NS = "urn:ebay:apis:eBLBaseComponents"


def valid_ean(value):
    if (
        not isinstance(value, str)
        or len(value) not in (8, 13)
        or not value.isascii()
        or not value.isdigit()
    ):
        return False
    body, check_digit = value[:-1], int(value[-1])
    weighted_sum = sum(
        int(digit) * (3 if offset % 2 == 0 else 1)
        for offset, digit in enumerate(reversed(body))
    )
    return (-weighted_sum) % 10 == check_digit


def normalize_eans(values):
    if not isinstance(values, list) or not values or len(values) > MAX_EANS:
        raise BulkEndError(f"Введите от 1 до {MAX_EANS} EAN.")
    normalized = []
    for value in values:
        ean = value.strip() if isinstance(value, str) else ""
        if not valid_ean(ean):
            raise BulkEndError(
                "Каждый EAN должен содержать 8 или 13 цифр с правильной контрольной цифрой."
            )
        if ean not in normalized:
            normalized.append(ean)
    return normalized


def local_name(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def child(node, name):
    if node is None:
        return None
    return next((element for element in node if local_name(element.tag) == name), None)


def descendants(node, name):
    if node is None:
        return []
    return [element for element in node.iter() if local_name(element.tag) == name]


def text(node, name, default=""):
    element = child(node, name)
    return (element.text or "").strip() if element is not None else default


def exact_listing_matches(item, requested_eans):
    matches = {}
    listing_details = child(item, "ProductListingDetails")
    listing_eans = {
        text(listing_details, "EAN"),
        *_specific_eans(listing_details),
        *_specific_eans(child(item, "ItemSpecifics")),
    }
    for ean in listing_eans.intersection(requested_eans):
        matches[ean] = "listing"
    for variation in descendants(child(item, "Variations"), "Variation"):
        for ean in _variation_eans(variation).intersection(requested_eans):
            matches[ean] = "variation"
    return matches


def _specific_eans(container):
    for pair in descendants(container, "NameValueList"):
        if text(pair, "Name").casefold() == "ean":
            for value in pair:
                if local_name(value.tag) == "Value" and value.text:
                    yield value.text.strip()


def _variation_eans(variation):
    product_details = child(variation, "VariationProductListingDetails")
    return {
        text(product_details, "EAN"),
        *_specific_eans(product_details),
        *_specific_eans(child(variation, "VariationSpecifics")),
    }


def _variation_label(variation):
    labels = []
    for pair in descendants(child(variation, "VariationSpecifics"), "NameValueList"):
        name = text(pair, "Name")
        if not name or name.casefold() == "ean":
            continue
        values = [
            value.text.strip()
            for value in pair
            if local_name(value.tag) == "Value" and value.text
        ]
        if values:
            labels.append(f"{name}: {', '.join(values)}")
    return ", ".join(labels)


def auction_state(item):
    listing_type = text(item, "ListingType")
    bid_count_text = text(child(item, "SellingStatus"), "BidCount")
    try:
        bid_count = int(bid_count_text) if bid_count_text else None
    except ValueError:
        bid_count = None
    auction_risky = listing_type.casefold() != "fixedpriceitem" and (
        bid_count is None or bid_count > 0
    )
    return listing_type, bid_count, auction_risky


def item_summary(item, requested_eans, view_url=""):
    item_id = text(item, "ItemID")
    if not item_id or not item_id.isdigit():
        return None
    matches = exact_listing_matches(item, requested_eans)
    if not matches:
        return None
    seller_id = text(child(item, "Seller"), "UserID")
    listing_type, bid_count, auction_risky = auction_state(item)
    variation_labels = {}
    for variation in descendants(child(item, "Variations"), "Variation"):
        label = _variation_label(variation)
        if not label:
            continue
        for ean in _variation_eans(variation).intersection(requested_eans):
            variation_labels.setdefault(ean, set()).add(label)
    return {
        "item_id": item_id,
        "title": text(item, "Title")[:500],
        "seller_id": seller_id,
        "listing_status": text(item, "ListingStatus"),
        "listing_type": listing_type,
        "bid_count": bid_count,
        "auction_risky": auction_risky,
        "variation_count": len(descendants(child(item, "Variations"), "Variation")),
        "view_url": view_url if view_url.startswith("https://www.ebay.de/") else "",
        "matches": [
            {
                "ean": ean,
                "type": kind,
                "variation": " / ".join(sorted(variation_labels.get(ean, set()))),
            }
            for ean, kind in sorted(matches.items())
        ],
        "risky": any(kind == "variation" for kind in matches.values())
        or child(item, "Variations") is not None,
    }


def is_ack_success(root):
    ack = text(root, "Ack")
    if ack not in {"Success", "Warning"}:
        error = next(iter(descendants(root, "Errors")), None)
        code = text(error, "ErrorCode")
        if code == "21916984":
            raise BulkEndError(
                "eBay не принял токен выбранного аккаунта (код 21916984). Повторно авторизуйте аккаунт."
            )
        if code:
            raise BulkEndError(f"eBay не принял запрос (код {code}).")
        raise BulkEndError("eBay не принял запрос. Повторите сканирование позже.")


def safe_csv_cell(value):
    value = str(value or "")
    if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def trading_element(name):
    return ET.Element("{" + TRADING_NS + "}" + name)
