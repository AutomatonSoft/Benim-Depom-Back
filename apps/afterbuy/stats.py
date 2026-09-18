from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from django.db.models import (
    Case,
    Count,
    DecimalField,
    F,
    IntegerField,
    Q,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from rest_framework.exceptions import ValidationError

from apps.accounts.models import User
from apps.products.models import Product
from apps.products.pricing import latest_rate

from .models import AfterbuyOrder, AfterbuyOrderItem

BERLIN = ZoneInfo("Europe/Berlin")
SALES_CHANNELS = (
    (AfterbuyOrder.Marketplace.OTTO, AfterbuyOrder.Account.JV),
    (AfterbuyOrder.Marketplace.KAUFLAND, AfterbuyOrder.Account.JV),
    (AfterbuyOrder.Marketplace.HOOD, AfterbuyOrder.Account.JV),
    (AfterbuyOrder.Marketplace.OTTO, AfterbuyOrder.Account.XL),
    (AfterbuyOrder.Marketplace.KAUFLAND, AfterbuyOrder.Account.XL),
    (AfterbuyOrder.Marketplace.HOOD, AfterbuyOrder.Account.XL),
)


def _parse_query_date(value: str | None, *, field: str) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError({field: "Use YYYY-MM-DD."}) from exc


def _berlin_day_start(value: date) -> datetime:
    return datetime(value.year, value.month, value.day, tzinfo=BERLIN)


def _digits(value: str) -> str:
    return "".join(character for character in value if character.isdigit())


def _period(
    *, date_from: date | None, date_to: date | None
) -> tuple[date | None, date | None]:
    if date_from and date_to and date_from > date_to:
        raise ValidationError({"from": "Must be on or before to."})
    return date_from, date_to


def _apply_period(queryset, *, date_from: date | None, date_to: date | None):
    if date_from is not None:
        queryset = queryset.filter(order__paid_at__gte=_berlin_day_start(date_from))
    if date_to is not None:
        queryset = queryset.filter(
            order__paid_at__lt=_berlin_day_start(date_to) + timedelta(days=1)
        )
    return queryset


def _aggregate_sales(queryset) -> dict:
    aggregated = queryset.aggregate(
        quantity_sold=Coalesce(
            Sum("quantity"),
            Value(0),
            output_field=IntegerField(),
        ),
        orders_count=Coalesce(
            Count("order_id", distinct=True),
            Value(0),
            output_field=IntegerField(),
        ),
        amount_eur=Coalesce(
            Sum(
                F("quantity") * F("unit_price"),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            ),
            Value(Decimal("0.00")),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        ),
        amount_try=Coalesce(
            Sum(
                Case(
                    When(
                        settled_currency__in=("TRY", ""),
                        then=F("quantity") * F("settled_unit_price"),
                    ),
                    default=Value(Decimal("0.00")),
                    output_field=DecimalField(max_digits=14, decimal_places=2),
                )
            ),
            Value(Decimal("0.00")),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        ),
    )
    amount_eur = aggregated["amount_eur"]
    amount_try = aggregated["amount_try"]
    amount_usd = None
    rate = latest_rate()
    if rate is not None and amount_eur is not None:
        amount_usd = (
            Decimal(str(amount_eur)) * Decimal(str(rate.eur_to_usd))
        ).quantize(Decimal("0.01"), ROUND_HALF_UP)
    return {
        "quantity_sold": int(aggregated["quantity_sold"] or 0),
        "orders_count": int(aggregated["orders_count"] or 0),
        "amount_eur": amount_eur,
        "amount_try": amount_try,
        "amount_usd": amount_usd,
    }


def _channel_quantities(queryset) -> list[dict]:
    rows = queryset.values("marketplace", "order__account").annotate(
        quantity_sold=Coalesce(
            Sum("quantity"),
            Value(0),
            output_field=IntegerField(),
        ),
        orders_count=Coalesce(
            Count("order_id", distinct=True),
            Value(0),
            output_field=IntegerField(),
        ),
    )
    lookup = {
        (row["marketplace"], row["order__account"]): (
            int(row["quantity_sold"] or 0),
            int(row["orders_count"] or 0),
        )
        for row in rows
    }
    return [
        {
            "marketplace": marketplace,
            "account": account,
            "quantity_sold": lookup.get((marketplace, account), (0, 0))[0],
            "orders_count": lookup.get((marketplace, account), (0, 0))[1],
        }
        for marketplace, account in SALES_CHANNELS
    ]


def sales_stats(
    *,
    seller=None,
    product: Product | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    include_channels: bool = False,
    include_seller: bool = False,
) -> dict:
    date_from, date_to = _period(date_from=date_from, date_to=date_to)
    queryset = AfterbuyOrderItem.objects.filter(
        matched_product__isnull=False,
        settled_unit_price__isnull=False,
    )
    if seller is not None:
        queryset = queryset.filter(matched_product__owner=seller)
    if product is not None:
        queryset = queryset.filter(matched_product=product)
    queryset = _apply_period(queryset, date_from=date_from, date_to=date_to)

    if product is not None:
        scope = "product"
    elif seller is not None:
        scope = "seller"
    else:
        scope = "all"

    ean = ""
    if product is not None:
        ean = product.ean_jv or product.ean_xl or ""

    payload = {
        **_aggregate_sales(queryset),
        "scope": scope,
        "product_id": product.pk if product is not None else None,
        "product_title": product.title if product is not None else None,
        "ean": ean,
        "from": date_from.isoformat() if date_from else None,
        "to": date_to.isoformat() if date_to else None,
    }
    if include_channels:
        payload["by_channel"] = _channel_quantities(queryset)
    if include_seller:
        owner = product.owner if product is not None else seller
        payload["seller_id"] = owner.pk if owner is not None else None
        payload["seller_email"] = owner.email if owner is not None else ""
        payload["seller_name"] = (
            (owner.first_name or owner.username) if owner is not None else ""
        )
    return payload


def seller_sales_stats(
    *,
    seller,
    product_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    product = None
    if product_id is not None:
        product = Product.objects.filter(pk=product_id, owner=seller).first()
        if product is None:
            raise ValidationError({"product_id": "Product not found."})
    return sales_stats(
        seller=seller,
        product=product,
        date_from=date_from,
        date_to=date_to,
    )


def _parse_product_id(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    try:
        product_id = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError({"product_id": "Must be an integer."}) from exc
    if product_id < 1:
        raise ValidationError({"product_id": "Must be a positive integer."})
    return product_id


def seller_sales_stats_from_query(*, seller, query_params) -> dict:
    return seller_sales_stats(
        seller=seller,
        product_id=_parse_product_id(query_params.get("product_id")),
        date_from=_parse_query_date(query_params.get("from"), field="from"),
        date_to=_parse_query_date(query_params.get("to"), field="to"),
    )


def _resolve_seller_email(value: str | None):
    email = (value or "").strip()
    if not email:
        return None
    seller = User.objects.filter(
        email__iexact=email,
        role=User.Role.SELLER,
    ).first()
    if seller is None:
        raise ValidationError({"seller_email": "Seller not found."})
    return seller


def _resolve_ean(*, value: str | None, seller=None) -> Product | None:
    raw = (value or "").strip()
    if not raw:
        return None
    ean = _digits(raw)
    if not ean:
        raise ValidationError({"ean": "Enter a numeric EAN."})
    queryset = Product.objects.filter(Q(ean_jv=ean) | Q(ean_xl=ean))
    if seller is not None:
        queryset = queryset.filter(owner=seller)
    matches = list(queryset.order_by("id")[:2])
    if not matches:
        raise ValidationError({"ean": "Product not found."})
    if len(matches) > 1:
        raise ValidationError({"ean": "Several products share this EAN."})
    return matches[0]


def manager_sales_stats_from_query(*, query_params) -> dict:
    seller = _resolve_seller_email(query_params.get("seller_email"))
    product = _resolve_ean(value=query_params.get("ean"), seller=seller)
    return sales_stats(
        seller=seller,
        product=product,
        date_from=_parse_query_date(query_params.get("from"), field="from"),
        date_to=_parse_query_date(query_params.get("to"), field="to"),
        include_channels=True,
        include_seller=True,
    )
