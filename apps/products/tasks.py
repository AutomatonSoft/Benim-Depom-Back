from datetime import date
from decimal import Decimal

import requests
from celery import shared_task

from .models import ExchangeRate

FRANKFURTER_URL = "https://api.frankfurter.dev/v2/latest"


@shared_task
def fetch_exchange_rates() -> str:
    try:
        response = requests.get(
            FRANKFURTER_URL,
            params={"from": "EUR", "to": "USD,TRY"},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        rates = payload["rates"]
        ExchangeRate.objects.create(
            as_of=date.fromisoformat(payload["date"]),
            eur_to_usd=Decimal(str(rates["USD"])),
            eur_to_try=Decimal(str(rates["TRY"])),
            source="frankfurter",
        )
        return "ok"
    except (requests.RequestException, KeyError, ValueError):
        if ExchangeRate.objects.exists():
            return "fallback"
        raise