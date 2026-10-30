from datetime import date
from decimal import Decimal
from smtplib import SMTPException

import requests
from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

from apps.common.tasks import enqueue_email_task

from .media_tasks import (  # noqa: F401 -- Celery discovers tasks through this module.
    cleanup_product_local_photos,
    recover_product_photo_uploads,
    transfer_product_photos,
)
from .models import ExchangeRate

FRANKFURTER_URL = "https://api.frankfurter.dev/v2/rates"


@shared_task(
    autoretry_for=(OSError, SMTPException),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
    ignore_result=True,
)
def send_product_created_email(
    *, seller_name: str, seller_email: str, title: str, product_id: int | None = None
):
    recipient = settings.PRODUCT_CREATE_EMAIL
    if not recipient:
        return

    # Older queued jobs do not include a product ID.
    manager_url = (
        f"{settings.PUBLIC_BASE_URL}/manager/products/{product_id}"
        if product_id is not None
        else f"{settings.PUBLIC_BASE_URL}/manager/"
    )
    message = EmailMultiAlternatives(
        subject="Benim Depom — a seller created a new product",
        body=(
            f"Seller {seller_name} ({seller_email}) created a new product.\n\n"
            f"Product name: {title}\n\n"
            "Please view the product in the manager panel:\n"
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


def enqueue_product_created_email(
    *, seller_name: str, seller_email: str, title: str, product_id: int
):
    if not settings.PRODUCT_CREATE_EMAIL:
        return
    enqueue_email_task(
        send_product_created_email,
        seller_name=seller_name,
        seller_email=seller_email,
        title=title,
        product_id=product_id,
    )


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
