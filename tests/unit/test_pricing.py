from decimal import Decimal

import pytest

from apps.products.models import ExchangeRate, ProductSetPart
from apps.products.pricing import (
    listing_price_eur,
    packed_cbm,
    percent_factor,
    round_to_49_or_99,
)
from apps.products.pricing_catalog import get_pricing_catalog


@pytest.mark.unit
def test_round_example_from_formula():
    assert round_to_49_or_99(Decimal("2193.90")) == Decimal("2199")


@pytest.mark.unit
def test_percent_factor_matches_catalog_defaults():
    assert percent_factor(get_pricing_catalog()) == Decimal("2.13")


@pytest.mark.unit
@pytest.mark.django_db
def test_manual_listing_override_does_not_change_other_products(
    seller, product_factory
):
    ExchangeRate.objects.create(
        as_of="2026-09-01",
        eur_to_usd="1.16",
        eur_to_try="50",
        source="test",
    )
    first = product_factory(owner=seller, unit_price=Decimal("100.00"))
    second = product_factory(
        owner=seller, title="Other chair", unit_price=Decimal("100.00")
    )
    first.refresh_from_db()
    second.refresh_from_db()
    computed = listing_price_eur(first)
    first.listing_price_eur_override = Decimal("1149.00")
    first.save(update_fields=["listing_price_eur_override"])
    assert listing_price_eur(first) == Decimal("1149.00")
    assert listing_price_eur(second) == computed


@pytest.mark.unit
@pytest.mark.django_db
def test_product_formula_override_recalculates_only_that_product(
    seller, product_factory
):
    ExchangeRate.objects.create(
        as_of="2026-09-01",
        eur_to_usd="1.16",
        eur_to_try="50",
        source="test",
    )
    first = product_factory(owner=seller, unit_price=Decimal("100.00"))
    second = product_factory(
        owner=seller, title="Other chair", unit_price=Decimal("100.00")
    )
    first.refresh_from_db()
    second.refresh_from_db()
    default_price = listing_price_eur(first)
    first.pricing_overrides = {"margin": "0"}
    first.save(update_fields=["pricing_overrides"])
    first.refresh_from_db()
    assert listing_price_eur(first) != default_price
    assert listing_price_eur(second) == default_price


@pytest.mark.unit
@pytest.mark.django_db
def test_set_parts_add_their_volume_to_packed_cbm_and_listing_price(
    seller, product_factory
):
    ExchangeRate.objects.create(
        as_of="2026-09-01",
        eur_to_usd="1.16",
        eur_to_try="50",
        source="test",
    )
    product = product_factory(owner=seller, unit_price=Decimal("100.00"))
    assert packed_cbm(product) == Decimal("0.2475")
    without_parts = listing_price_eur(product)
    ProductSetPart.objects.create(
        product=product,
        position=0,
        description="Nightstand",
        width_cm="50.00",
        height_cm="50.00",
        length_cm="40.00",
    )
    assert packed_cbm(product) == Decimal("0.3475")
    assert listing_price_eur(product) > without_parts
