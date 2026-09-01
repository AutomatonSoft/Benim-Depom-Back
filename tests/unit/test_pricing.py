from decimal import Decimal

import pytest

from apps.products.pricing import round_to_49_or_99


@pytest.mark.unit
def test_round_example_from_formula():
    assert round_to_49_or_99(Decimal("2193.90")) == Decimal("2199")
