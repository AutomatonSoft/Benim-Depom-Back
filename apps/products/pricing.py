from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from .models import ExchangeRate, Product
from .pricing_catalog import get_pricing_catalog

CM3_PER_M3 = Decimal("1000000")
PERCENT_FACTOR = Decimal("2.13")  # 75% + 19% + 19%


def packed_cbm(product: Product) -> Decimal:
    volumes = [
        (variant.width_cm * variant.height_cm * variant.length_cm) / CM3_PER_M3
        for variant in product.variants.all()
    ]
    if not volumes:
        raise ValueError("Product has no variants to compute CBM.")
    return max(volumes)


def purchase_eur(product: Product, rate: ExchangeRate) -> Decimal:
    amount = product.unit_price
    if product.currency == Product.Currency.EUR:
        return amount
    if product.currency == Product.Currency.TRY:
        return (amount / rate.eur_to_try).quantize(Decimal("0.01"), ROUND_HALF_UP)
    if product.currency == Product.Currency.USD:
        return (amount / rate.eur_to_usd).quantize(Decimal("0.01"), ROUND_HALF_UP)
    raise ValueError(f"Unsupported currency: {product.currency}")


def de_delivery_eur(cbm: Decimal) -> Decimal:
    catalog = get_pricing_catalog()
    for tier in catalog["de_size_tiers"]:
        if Decimal(str(tier["min_cbm"])) <= cbm <= Decimal(str(tier["max_cbm"])):
            return Decimal(str(tier["price_eur"]))
    raise ValueError(f"No DE size tier for cbm={cbm}")


def round_to_49_or_99(amount: Decimal) -> Decimal:
    if amount <= 0:
        return Decimal("0.99")
    base = (int(amount) // 100) * 100
    candidates: list[Decimal] = []
    for hundreds in (base - 100, base, base + 100, base + 200):
        if hundreds < 0:
            continue
        for ending in (49, 99):
            candidate = Decimal(hundreds + ending)
            if candidate > 0:
                candidates.append(candidate)
    return min(candidates, key=lambda value: (abs(value - amount), -value))


def latest_rate() -> ExchangeRate | None:
    return ExchangeRate.objects.order_by("-fetched_at").first()


def listing_price_eur(product: Product) -> Decimal | None:
    rate = latest_rate()
    if rate is None:
        return None
    catalog = get_pricing_catalog()
    cbm = packed_cbm(product)
    tariff = Decimal(str(catalog["city_tariffs_eur_per_cbm"][product.warehouse_city]))
    cost = purchase_eur(product, rate) + (tariff * cbm) + de_delivery_eur(cbm)
    with_percent = (cost * PERCENT_FACTOR).quantize(Decimal("0.01"), ROUND_HALF_UP)
    return round_to_49_or_99(with_percent)


def safe_listing_price_eur(product: Product) -> Decimal | None:
    try:
        return listing_price_eur(product)
    except (ValueError, KeyError):
        return None


def fill_marketplace_price(
    configuration: dict, product: Product, *, field: str
) -> dict:
    data = dict(configuration or {})
    if data.get(field) not in (None, ""):
        return data
    price = safe_listing_price_eur(product)
    if price is not None:
        data[field] = format(price, "f")
    return data