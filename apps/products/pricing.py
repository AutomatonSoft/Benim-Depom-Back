from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import NamedTuple

from .models import ExchangeRate, Product
from .pricing_catalog import catalog_for_product, get_pricing_catalog

_UNSET = object()
CM3_PER_M3 = Decimal("1000000")


class FxRate(NamedTuple):
    eur_to_usd: Decimal
    eur_to_try: Decimal


def packed_cbm(product: Product) -> Decimal:
    volumes = [
        (variant.width_cm * variant.height_cm * variant.length_cm) / CM3_PER_M3
        for variant in product.variants.all()
    ]
    if not volumes:
        raise ValueError("Product has no variants to compute CBM.")
    return max(volumes)


def percent_factor(catalog: dict) -> Decimal:
    return (
        Decimal("1")
        + Decimal(str(catalog["margin"]))
        + Decimal(str(catalog["adv_fee"]))
        + Decimal(str(catalog["vat"]))
    )


def purchase_eur(product: Product, rate: FxRate | ExchangeRate) -> Decimal:
    amount = product.unit_price
    if product.currency == Product.Currency.EUR:
        return amount
    if product.currency == Product.Currency.TRY:
        return (amount / rate.eur_to_try).quantize(Decimal("0.01"), ROUND_HALF_UP)
    if product.currency == Product.Currency.USD:
        return (amount / rate.eur_to_usd).quantize(Decimal("0.01"), ROUND_HALF_UP)
    raise ValueError(f"Unsupported currency: {product.currency}")


def de_delivery_eur(cbm: Decimal, catalog: dict | None = None) -> Decimal:
    catalog = catalog or get_pricing_catalog()
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


def resolved_rate(
    product: Product, rate: ExchangeRate | FxRate | None | object = _UNSET
) -> FxRate | None:
    overrides = product.pricing_overrides or {}
    live = latest_rate() if rate is _UNSET else rate
    eur_to_try = overrides.get("eur_to_try")
    eur_to_usd = overrides.get("eur_to_usd")
    if eur_to_try is None and live is not None:
        eur_to_try = live.eur_to_try
    if eur_to_usd is None and live is not None:
        eur_to_usd = live.eur_to_usd
    if product.currency == Product.Currency.EUR:
        return FxRate(
            eur_to_usd=Decimal(str(eur_to_usd or 1)),
            eur_to_try=Decimal(str(eur_to_try or 1)),
        )
    if product.currency == Product.Currency.TRY and eur_to_try is None:
        return None
    if product.currency == Product.Currency.USD and eur_to_usd is None:
        return None
    if eur_to_try is None or eur_to_usd is None:
        return None
    return FxRate(Decimal(str(eur_to_usd)), Decimal(str(eur_to_try)))


def listing_price_eur(
    product: Product, rate: ExchangeRate | FxRate | None | object = _UNSET
) -> Decimal | None:
    if product.listing_price_eur_override is not None:
        return product.listing_price_eur_override
    fx = resolved_rate(product, rate)
    if fx is None:
        return None
    catalog = catalog_for_product(product)
    cbm = packed_cbm(product)
    tariff = Decimal(str(catalog["city_tariffs_eur_per_cbm"][product.warehouse_city]))
    cost = purchase_eur(product, fx) + (tariff * cbm) + de_delivery_eur(cbm, catalog)
    with_percent = (cost * percent_factor(catalog)).quantize(
        Decimal("0.01"), ROUND_HALF_UP
    )
    return round_to_49_or_99(with_percent)


def safe_listing_price_eur(
    product: Product, rate: ExchangeRate | FxRate | None | object = _UNSET
) -> Decimal | None:
    try:
        return listing_price_eur(product, rate=rate)
    except (ValueError, KeyError, TypeError, ArithmeticError):
        return None


def effective_pricing_formula(
    product: Product, rate: ExchangeRate | FxRate | None | object = _UNSET
) -> dict:
    catalog = catalog_for_product(product)
    fx = resolved_rate(product, rate)
    return {
        "margin": catalog["margin"],
        "adv_fee": catalog["adv_fee"],
        "vat": catalog["vat"],
        "percent_factor": str(percent_factor(catalog)),
        "city_tariffs_eur_per_cbm": catalog["city_tariffs_eur_per_cbm"],
        "de_size_tiers": catalog["de_size_tiers"],
        "eur_to_try": str(fx.eur_to_try) if fx else None,
        "eur_to_usd": str(fx.eur_to_usd) if fx else None,
        "uses_product_formula": bool(product.pricing_overrides),
        "uses_manual_listing": product.listing_price_eur_override is not None,
    }


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
