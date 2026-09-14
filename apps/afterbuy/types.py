from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class NormalizedSoldItem:
    item_id: str
    anr: str
    product_id: str
    sku: str
    alternative_item_number: str
    ean: str
    title: str
    quantity: int
    unit_price: Decimal | None
    platform_name: str
    marketplace: str
    platform_order_number: str
    extra_tags: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class NormalizedSoldOrder:
    afterbuy_order_id: str
    paid_at: datetime | None
    total_amount: Decimal | None
    currency: str
    payment_method: str
    platform_order_number: str
    marketplace: str
    buyer_name: str
    buyer_email: str
    buyer_platform_user_id: str
    items: tuple[NormalizedSoldItem, ...] = ()
    has_more: bool = False
    last_order_id: str = ""
