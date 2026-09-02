from datetime import date
from decimal import Decimal
from unittest.mock import Mock

import pytest

from apps.products.models import ExchangeRate
from apps.products.tasks import store_latest_exchange_rates


@pytest.mark.unit
@pytest.mark.django_db
def test_store_latest_exchange_rates_parses_frankfurter_v2_payload(monkeypatch):
    response = Mock()
    response.json.return_value = [
        {"date": "2026-09-01", "base": "EUR", "quote": "TRY", "rate": 56.035},
        {"date": "2026-09-01", "base": "EUR", "quote": "USD", "rate": 1.1615},
    ]
    get = Mock(return_value=response)
    monkeypatch.setattr("apps.products.tasks.requests.get", get)

    stored = store_latest_exchange_rates()

    get.assert_called_once()
    assert get.call_args.args[0] == "https://api.frankfurter.dev/v2/rates"
    assert get.call_args.kwargs["params"] == {"base": "EUR", "quotes": "USD,TRY"}
    assert stored.as_of == date(2026, 9, 1)
    assert stored.eur_to_usd == Decimal("1.1615")
    assert stored.eur_to_try == Decimal("56.035")
    assert ExchangeRate.objects.count() == 1
