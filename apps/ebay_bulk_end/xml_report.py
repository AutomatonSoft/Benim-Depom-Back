from xml.etree import ElementTree as ET

from .domain import child, descendants, local_name, text
from .errors import BulkEndError


def iter_xml_inventory_rows(stream, marketplace_id, heartbeat=None):
    """Read LMS XML one listing at a time, rejecting DTDs and entity declarations."""
    parser = ET.XMLPullParser(events=("start", "end"))
    stack = []
    reports = 0
    ack = None
    tail = b""
    try:
        while chunk := stream.read(64 * 1024):
            # LMS uses UTF-8. Reject alternate encodings that could hide XML declarations.
            guarded = tail + chunk
            if (
                b"\x00" in chunk
                or b"<!DOCTYPE" in guarded.upper()
                or b"<!ENTITY" in guarded.upper()
            ):
                raise BulkEndError(
                    "XML-отчёт eBay содержит запрещённые объявления или кодировку."
                )
            tail = guarded[-16:]
            parser.feed(chunk)
            for event, element in parser.read_events():
                name = local_name(element.tag)
                if event == "start":
                    stack.append(element)
                    if name == "ActiveInventoryReport":
                        reports += 1
                        if reports != 1:
                            raise BulkEndError("XML содержит несколько отчётов eBay.")
                    continue
                parent_name = local_name(stack[-2].tag) if len(stack) > 1 else ""
                if name == "SKUDetails" and parent_name == "ActiveInventoryReport":
                    item_id = text(element, "ItemID")
                    variations = descendants(child(element, "Variations"), "Variation")
                    skus = [text(element, "SKU")]
                    skus.extend(text(variation, "SKU") for variation in variations)
                    for sku in skus:
                        if sku or not variations:
                            yield {
                                "sku": sku,
                                "item_id": item_id,
                                "marketplace_id": marketplace_id,
                            }
                    stack[-2].remove(element)
                    element.clear()
                elif name == "Ack" and parent_name == "ActiveInventoryReport":
                    ack = (element.text or "").strip()
                elif name == "Errors":
                    if text(element, "SeverityCode") == "Error":
                        raise BulkEndError("XML-отчёт eBay содержит ошибку.")
                stack.pop()
            if heartbeat:
                heartbeat()
        parser.close()
    except ET.ParseError as error:
        raise BulkEndError("eBay вернул повреждённый XML-отчёт.") from error
    if reports != 1 or ack not in {"Success", "Warning"}:
        raise BulkEndError("eBay не подтвердил полноту XML-отчёта.")
