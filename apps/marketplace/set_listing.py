from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


def _format_cm(value: Any) -> str:
    """Format a centimetre value without trailing zeros."""
    if value is None or value == "":
        return ""
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return str(value).strip()
    formatted = format(number, "f")
    if "." in formatted:
        formatted = formatted.rstrip("0").rstrip(".")
    return formatted


def iter_set_parts(product) -> list[Any]:
    """Return extra set pieces in stored order."""
    relation = getattr(product, "set_parts", None)
    if relation is None:
        return []
    if hasattr(relation, "all"):
        parts = list(relation.all())
    elif isinstance(relation, (list, tuple)):
        parts = list(relation)
    else:
        return []
    return sorted(
        parts,
        key=lambda part: (
            getattr(part, "position", 0),
            getattr(part, "pk", 0) or 0,
        ),
    )


def snapshot_set_parts(product) -> list[dict[str, str]]:
    """JSON-safe extra pieces for the AI snapshot."""
    return [
        {
            "description": str(getattr(part, "description", "") or "").strip(),
            "width_cm": _format_cm(getattr(part, "width_cm", None)),
            "height_cm": _format_cm(getattr(part, "height_cm", None)),
            "length_cm": _format_cm(getattr(part, "length_cm", None)),
        }
        for part in iter_set_parts(product)
        if str(getattr(part, "description", "") or "").strip()
    ]


def snapshot_has_set_parts(product_snapshot: dict[str, Any] | None) -> bool:
    """Whether the content snapshot describes a multi-piece set."""
    if not product_snapshot:
        return False
    parts = product_snapshot.get("set_parts") or []
    return bool(parts)


def set_piece_count_from_snapshot(product_snapshot: dict[str, Any]) -> int:
    """Main item plus extra pieces."""
    return 1 + len(product_snapshot.get("set_parts") or [])


def dimensions_block_from_snapshot(product_snapshot: dict[str, Any]) -> str:
    """German size list for the main item and every extra piece."""
    parts = product_snapshot.get("set_parts") or []
    if not parts:
        return ""

    variants = product_snapshot.get("variants") or []
    main = variants[0] if variants else {}
    main_label = str(product_snapshot.get("seller_product_type") or "").strip()
    if not main_label:
        main_label = "Hauptartikel"

    lines = ["Lieferumfang & Maße:"]
    lines.append(
        "1. "
        f"{main_label}: B/H/T ca. "
        f"{main.get('width_cm', '')} x "
        f"{main.get('height_cm', '')} x "
        f"{main.get('length_cm', '')} cm"
    )
    for index, part in enumerate(parts, start=2):
        label = str(part.get("description") or "").strip() or f"Teil {index}"
        lines.append(
            f"{index}. {label}: B/H/T ca. "
            f"{part.get('width_cm', '')} x "
            f"{part.get('height_cm', '')} x "
            f"{part.get('length_cm', '')} cm"
        )
    return "\n".join(lines)


def with_set_dimensions(description: str, product_snapshot: dict[str, Any]) -> str:
    """Append the factual size list when the product is a set."""
    block = dimensions_block_from_snapshot(product_snapshot)
    if not block:
        return description
    cleaned = description.strip()
    if block in cleaned:
        return cleaned
    return f"{cleaned}\n\n{block}" if cleaned else block
