from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import NamedTuple

from .models import ExchangeRate, Product
from .pricing_catalog import catalog_for_product, get_pricing_catalog

_UNSET = object()
CM3_PER_M3 = Decimal("1000000")


class FxRate(NamedTuple):
    eur_to_usd: Decimal
    eur_to_try: Decimal


def _box_cbm(width, height, length) -> Decimal:
    """Convert one packed box in centimetres to cubic metres."""
    return (
        Decimal(str(width)) * Decimal(str(height)) * Decimal(str(length))
    ) / CM3_PER_M3


def calculated_cbm(product: Product) -> Decimal:
    """Main variant CBM plus every extra set piece. Colors are alternatives."""
    volumes = [
        _box_cbm(variant.width_cm, variant.height_cm, variant.length_cm)
        for variant in product.variants.all()
    ]
    if not volumes:
        raise ValueError("Product has no variants to compute CBM.")
    total = max(volumes)
    for part in product.set_parts.all():
        total += _box_cbm(part.width_cm, part.height_cm, part.length_cm)
    return total


def packed_cbm(product: Product) -> Decimal:
    """Manager-entered volume if set, otherwise the volume from dimensions."""
    if product.cbm_override is not None:
        return Decimal(str(product.cbm_override))
    return calculated_cbm(product)


def safe_cbm(product: Product, *, calculated: bool = False) -> str | None:
    """CBM rounded to 3 decimals for display, or None without dimensions."""
    try:
        value = calculated_cbm(product) if calculated else packed_cbm(product)
    except (ValueError, TypeError, ArithmeticError):
        return None
    return f"{value.quantize(Decimal('0.001'), ROUND_HALF_UP):f}"


def percent_factor(catalog: dict) -> Decimal:
    return (
        Decimal("1")
        + Decimal(str(catalog["margin"]))
        + Decimal(str(catalog["adv_fee"]))
        + Decimal(str(catalog["vat"]))
    )


def purchase_eur(product: Product, rate: FxRate | ExchangeRate) -> Decimal:
    amount = Decimal(str(product.unit_price))
    if product.currency == Product.Currency.EUR:
        return amount
    if product.currency == Product.Currency.TRY:
        return (amount / Decimal(str(rate.eur_to_try))).quantize(
            Decimal("0.01"), ROUND_HALF_UP
        )
    if product.currency == Product.Currency.USD:
        return (amount / Decimal(str(rate.eur_to_usd))).quantize(
            Decimal("0.01"), ROUND_HALF_UP
        )
    raise ValueError(f"Unsupported currency: {product.currency}")


def de_delivery_eur(cbm: Decimal, catalog: dict | None = None) -> Decimal:
    catalog = catalog or get_pricing_catalog()
    tiers = catalog["de_size_tiers"]
    for tier in tiers:
        if Decimal(str(tier["min_cbm"])) <= cbm <= Decimal(str(tier["max_cbm"])):
            return Decimal(str(tier["price_eur"]))
    boundaries = [
        (Decimal(str(tier[bound])), tier)
        for tier in tiers
        for bound in ("min_cbm", "max_cbm")
    ]
    if boundaries and min(bound for bound, _ in boundaries) <= cbm <= max(
        bound for bound, _ in boundaries
    ):
        # In a gap choose the nearest boundary; ties use the larger volume tier.
        _, nearest = min(boundaries, key=lambda entry: (abs(entry[0] - cbm), -entry[0]))
        return Decimal(str(nearest["price_eur"]))
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


MIN_SET_LISTING_PRICE_EUR = Decimal("1500")


def _listing_with_unit_price(product: Product, amount: Decimal) -> Decimal | None:
    saved = product.unit_price, product.listing_price_eur_override
    product.unit_price, product.listing_price_eur_override = amount, None
    try:
        return safe_listing_price_eur(product)
    finally:
        product.unit_price, product.listing_price_eur_override = saved


def min_set_unit_price(product: Product) -> Decimal | None:
    """Smallest whole card price (product currency) giving a set listing >= minimum."""
    fx = resolved_rate(product)
    if fx is None:
        return None
    catalog = catalog_for_product(product)
    cbm = packed_cbm(product)
    tariff = Decimal(str(catalog["city_tariffs_eur_per_cbm"][product.warehouse_city]))
    # Listing is rounded to the nearest xx49/xx99: aim at the midpoint below
    # the first such price that is not under the minimum.
    hundreds = (int(MIN_SET_LISTING_PRICE_EUR) // 100) * 100
    first_ok = next(
        Decimal(hundreds + step + ending)
        for step in (0, 100)
        for ending in (49, 99)
        if hundreds + step + ending >= MIN_SET_LISTING_PRICE_EUR
    )
    threshold = first_ok - 25
    needed_eur = (
        threshold / percent_factor(catalog)
        - tariff * cbm
        - de_delivery_eur(cbm, catalog)
    )
    currency_rate = {
        Product.Currency.EUR: Decimal("1"),
        Product.Currency.TRY: fx.eur_to_try,
        Product.Currency.USD: fx.eur_to_usd,
    }[product.currency]
    amount = max(needed_eur * currency_rate, Decimal("1")).quantize(
        Decimal("1"), ROUND_CEILING
    )
    for _ in range(100):
        price = _listing_with_unit_price(product, amount)
        if price is None:
            return None
        if price >= MIN_SET_LISTING_PRICE_EUR:
            return amount
        amount += 1
    return None


def ensure_set_min_listing_price(product: Product) -> None:
    """Reject a set (product with extra pieces) listed below the minimum."""
    from rest_framework.exceptions import ValidationError

    if not product.set_parts.exists():
        return
    price = safe_listing_price_eur(product)
    if price is None or price >= MIN_SET_LISTING_PRICE_EUR:
        return
    try:
        minimum = min_set_unit_price(product)
    except (ValueError, KeyError, TypeError, ArithmeticError):
        minimum = None
    message = "Set price is too low."
    if minimum is not None:
        message += f" Minimum price for this set: {minimum} {product.currency}."
    raise ValidationError({"unit_price": message})


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
