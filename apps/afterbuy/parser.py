import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from .types import NormalizedSoldItem, NormalizedSoldOrder

BERLIN = ZoneInfo("Europe/Berlin")
TRACKED_MARKETPLACES = frozenset({"otto", "hood", "kaufland"})
_AFTERBUY_DATETIME = re.compile(
    r"^(?P<d>\d{1,2})\.(?P<m>\d{1,2})\.(?P<y>\d{4})"
    r"(?:\s+(?P<H>\d{1,2}):(?P<M>\d{2})(?::(?P<S>\d{2}))?)?$"
)


class AfterbuyParseError(ValueError):
    pass


def _local(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _child(element: ElementTree.Element | None, *names: str):
    if element is None:
        return None
    wanted = {name.casefold() for name in names}
    for child in element:
        if _local(child.tag).casefold() in wanted:
            return child
    return None


def _text(element: ElementTree.Element | None, *names: str) -> str:
    if not names:
        return (element.text or "").strip() if element is not None else ""
    found = _child(element, *names)
    if found is None:
        return ""
    return (found.text or "").strip()


def _first_text(
    element: ElementTree.Element | None,
    groups: tuple[tuple[str, ...], ...],
) -> str:
    for names in groups:
        value = _text(element, *names)
        if value:
            return value
    return ""


def parse_afterbuy_datetime(value: str) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    matched = _AFTERBUY_DATETIME.match(raw)
    if matched is None:
        return None
    parsed = datetime(
        int(matched.group("y")),
        int(matched.group("m")),
        int(matched.group("d")),
        int(matched.group("H") or 0),
        int(matched.group("M") or 0),
        int(matched.group("S") or 0),
        tzinfo=BERLIN,
    )
    return parsed.astimezone(UTC)


def parse_afterbuy_decimal(value: str) -> Decimal | None:
    raw = (value or "").strip().replace(" ", "").replace(",", ".")
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _email_domain(email: str) -> str:
    parts = (email or "").strip().casefold().split()
    if not parts:
        return ""
    local = parts[0]
    if "@" not in local:
        return ""
    return local.rsplit("@", 1)[-1].rstrip(".")


def _compact(value: str) -> str:
    return "".join((value or "").casefold().split())


def detect_marketplace(
    *,
    platform_name: str,
    buyer_platform_user_id: str,
    email: str,
    payment_method: str = "",
) -> str:
    platform = (platform_name or "").casefold()
    user_id = (buyer_platform_user_id or "").casefold()
    method = _compact(payment_method)
    domain = _email_domain(email)

    if "otto" in platform or user_id.startswith("otto-") or "otto" in domain:
        return "otto"
    if (
        platform == "hood"
        or platform.startswith("hood.")
        or "hoodpay" in method
        or domain == "hood.de"
        or domain.endswith(".hood.de")
    ):
        return "hood"
    if (
        "kaufland" in platform
        or "real.de" in platform
        or "kaufland" in method
        or domain == "kaufland-marktplatz.de"
        or domain.endswith(".kaufland-marktplatz.de")
        or domain.startswith("kaufland-marktplatz")
    ):
        return "kaufland"
    return ""


def _parse_quantity(value: str) -> int:
    parsed = parse_afterbuy_decimal(value)
    if parsed is None:
        return 1
    return max(int(parsed), 1)


def _item_platform_order_number(
    item: ElementTree.Element, order: ElementTree.Element
) -> str:
    return _first_text(
        item,
        (
            ("AlternativeItemNumber1",),
            ("AlternativeItemNumber2",),
            ("PlatformSpecificOrderId",),
        ),
    ) or _first_text(
        order,
        (
            ("AlternativeItemNumber1",),
            ("AlternativeItemNumber2",),
        ),
    )


def _buyer_platform_order_number(buyer_platform_user_id: str) -> str:
    prefix = "otto-"
    value = buyer_platform_user_id.strip()
    if value.casefold().startswith(prefix):
        return value[len(prefix) :].strip()
    return ""


def _parse_item(
    item: ElementTree.Element,
    *,
    order: ElementTree.Element,
    buyer_platform_user_id: str,
    buyer_email: str,
    payment_method: str,
) -> NormalizedSoldItem:
    platform_name = _text(item, "ItemPlatformName", "Plattform")
    marketplace = detect_marketplace(
        platform_name=platform_name,
        buyer_platform_user_id=buyer_platform_user_id,
        email=buyer_email,
        payment_method=payment_method,
    )
    platform_order_number = _item_platform_order_number(
        item, order
    ) or _buyer_platform_order_number(buyer_platform_user_id)
    extra: dict[str, str] = {}
    for child in item:
        name = _local(child.tag)
        if name.casefold().startswith("alternative") or "ean" in name.casefold():
            text = (child.text or "").strip()
            if text:
                extra[name] = text
    return NormalizedSoldItem(
        item_id=_text(item, "ItemID", "ID"),
        anr=_text(item, "Anr"),
        product_id=_text(item, "ProductID"),
        sku=_text(item, "SKU"),
        alternative_item_number=_text(item, "AlternativeItemNumber"),
        ean=_text(item, "EAN", "Ean"),
        title=_text(item, "ItemTitle", "Title"),
        quantity=_parse_quantity(_text(item, "ItemQuantity", "Quantity")),
        unit_price=parse_afterbuy_decimal(_text(item, "ItemPrice", "Price")),
        platform_name=platform_name,
        marketplace=marketplace,
        platform_order_number=platform_order_number,
        extra_tags=extra,
    )


def _parse_order(order: ElementTree.Element) -> NormalizedSoldOrder | None:
    afterbuy_order_id = _text(order, "OrderID", "AfterbuyOrderID")
    if not afterbuy_order_id:
        return None

    payment = _child(order, "PaymentInfo")
    buyer_info = _child(order, "BuyerInfo")
    billing = _child(buyer_info, "BillingAddress")
    if billing is None:
        billing = buyer_info
    sold_items = _child(order, "SoldItems")

    buyer_platform_user_id = _text(billing, "UserIDPlattform", "UserIDPlatform")
    buyer_email = _text(billing, "Mail", "Email").split(" OTTO", 1)[0].strip()
    payment_method = _text(payment, "PaymentMethod", "PaymentID")
    buyer_name = " ".join(
        part
        for part in (
            _text(billing, "FirstName"),
            _text(billing, "LastName"),
        )
        if part
    ) or _text(billing, "FullName", "Company")

    items: list[NormalizedSoldItem] = []
    if sold_items is not None:
        for item in sold_items:
            if _local(item.tag).casefold() != "solditem":
                continue
            items.append(
                _parse_item(
                    item,
                    order=order,
                    buyer_platform_user_id=buyer_platform_user_id,
                    buyer_email=buyer_email,
                    payment_method=payment_method,
                )
            )

    marketplaces = {item.marketplace for item in items if item.marketplace}
    marketplace = next(iter(marketplaces), "")
    if not marketplace:
        marketplace = detect_marketplace(
            platform_name="",
            buyer_platform_user_id=buyer_platform_user_id,
            email=buyer_email,
            payment_method=payment_method,
        )

    platform_order_number = next(
        (item.platform_order_number for item in items if item.platform_order_number),
        _buyer_platform_order_number(buyer_platform_user_id),
    )

    total_amount = parse_afterbuy_decimal(
        _first_text(
            payment,
            (
                ("FullAmount",),
                ("AlreadyPaid",),
                ("PaymentTotalAmount",),
            ),
        )
        or _text(order, "Total")
    )

    return NormalizedSoldOrder(
        afterbuy_order_id=afterbuy_order_id,
        paid_at=parse_afterbuy_datetime(_text(payment, "PaymentDate")),
        total_amount=total_amount,
        currency=_text(payment, "PaymentCurrency") or "EUR",
        payment_method=payment_method,
        platform_order_number=platform_order_number,
        marketplace=marketplace,
        buyer_name=buyer_name,
        buyer_email=buyer_email,
        buyer_platform_user_id=buyer_platform_user_id,
        items=tuple(items),
    )


def parse_sold_items_xml(
    payload: str | bytes,
) -> tuple[list[NormalizedSoldOrder], bool, str]:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise AfterbuyParseError("Afterbuy returned invalid XML.") from exc

    error = _child(root, "Error", "Errors")
    if error is not None:
        description = _text(error, "ErrorDescription", "Description") or _text(error)
        if description:
            raise AfterbuyParseError(description)

    result = _child(root, "Result") or root
    orders: list[NormalizedSoldOrder] = []
    for order in result.iter():
        if _local(order.tag).casefold() != "order":
            continue
        parsed = _parse_order(order)
        if parsed is not None:
            orders.append(parsed)

    has_more_raw = _text(result, "HasMoreItems", "HasMorePages").casefold()
    has_more = has_more_raw in {"1", "true", "yes"}
    last_order_id = _text(result, "LastOrderID")
    if not last_order_id and orders:
        last_order_id = max(order.afterbuy_order_id for order in orders)
    return orders, has_more, last_order_id


def is_tracked_marketplace(marketplace: str) -> bool:
    return marketplace.casefold() in TRACKED_MARKETPLACES
