from __future__ import annotations

from apps.products.models import Product

from .models import AfterbuyOrderItem


def apply_card_price_snapshot(
    *, item: AfterbuyOrderItem, product: Product | None
) -> AfterbuyOrderItem:
    """Freeze Product.unit_price once, so later card edits do not rewrite history."""
    if product is None or item.settled_unit_price is not None:
        return item
    item.settled_unit_price = product.unit_price
    item.settled_currency = product.currency
    item.save(update_fields=["settled_unit_price", "settled_currency", "updated_at"])
    return item
