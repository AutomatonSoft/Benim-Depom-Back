from datetime import date
from decimal import Decimal

import requests
from celery import shared_task

from .models import ExchangeRate

FRANKFURTER_URL = "https://api.frankfurter.dev/v2/rates"


def store_latest_exchange_rates() -> ExchangeRate:
    response = requests.get(
        FRANKFURTER_URL,
        params={"base": "EUR", "quotes": "USD,TRY"},
        timeout=15,
    )
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list):
        raise ValueError("Unexpected Frankfurter rates payload.")
    by_quote = {row["quote"]: row for row in rows}
    usd = by_quote["USD"]
    try_row = by_quote["TRY"]
    return ExchangeRate.objects.create(
        as_of=date.fromisoformat(str(usd["date"])),
        eur_to_usd=Decimal(str(usd["rate"])),
        eur_to_try=Decimal(str(try_row["rate"])),
        source="frankfurter",
    )


@shared_task
def fetch_exchange_rates() -> str:
    try:
        store_latest_exchange_rates()
        return "ok"
    except (requests.RequestException, KeyError, ValueError):
        if ExchangeRate.objects.exists():
            return "fallback"
        raise
