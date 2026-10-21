import logging
from datetime import date
from decimal import Decimal
from smtplib import SMTPException

import requests
from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

from .models import ExchangeRate

FRANKFURTER_URL = "https://api.frankfurter.dev/v2/rates"
logger = logging.getLogger(__name__)


@shared_task(
    autoretry_for=(OSError, SMTPException),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
    ignore_result=True,
)
def send_product_created_email(*, seller_name: str, seller_email: str, title: str):
    recipient = settings.PRODUCT_CREATE_EMAIL
    if not recipient:
        return

    manager_url = "https://benimdepom.info/manager/"
    message = EmailMultiAlternatives(
        subject="Benim Depom — продавец создал новый товар",
        body=(
            f"Продавец {seller_name} ({seller_email}) создал новый товар.\n\n"
            f"Название товара: {title}\n\n"
            "Пожалуйста, зайдите в менеджерскую панель и посмотрите товар:\n"
            f"{manager_url}"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
    )
    message.attach_alternative(
        render_to_string(
            "emails/product_created.html",
            {
                "seller_name": seller_name,
                "seller_email": seller_email,
                "title": title,
                "manager_url": manager_url,
            },
        ),
        "text/html",
    )
    message.send(fail_silently=False)


def enqueue_product_created_email(*, seller_name: str, seller_email: str, title: str):
    if not settings.PRODUCT_CREATE_EMAIL:
        return
    try:
        # Bound Redis publishing too: SMTP runs only in the notifications worker.
        with send_product_created_email.app.connection_for_write(
            connect_timeout=1,
            transport_options={
                "max_retries": 0,
                "socket_connect_timeout": 1,
                "socket_timeout": 1,
                "retry_on_timeout": False,
            },
        ) as connection:
            send_product_created_email.apply_async(
                kwargs={
                    "seller_name": seller_name,
                    "seller_email": seller_email,
                    "title": title,
                },
                connection=connection,
                retry=False,
            )
    except Exception:
        # shortcut: failed enqueue is logged only; use an outbox if delivery must be guaranteed.
        logger.exception("Could not enqueue product-created email")


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
