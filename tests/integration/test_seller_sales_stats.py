from datetime import UTC, datetime
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.afterbuy.models import AfterbuyOrder, AfterbuyOrderItem
from apps.products.models import Product


def _paid_item(
    *,
    product,
    quantity,
    settled_price,
    paid_at,
    order_id="1",
    account=AfterbuyOrder.Account.JV,
    marketplace=AfterbuyOrder.Marketplace.OTTO,
):
    order = AfterbuyOrder.objects.create(
        account=account,
        afterbuy_order_id=order_id,
        marketplace=marketplace,
        paid_at=paid_at,
        total_amount="19.90",
        currency="EUR",
    )
    return AfterbuyOrderItem.objects.create(
        order=order,
        item_key=order_id,
        quantity=quantity,
        unit_price="9.95",
        marketplace=marketplace,
        matched_product=product,
        settled_unit_price=settled_price,
        settled_currency=product.currency,
    )


@pytest.mark.integration
@pytest.mark.django_db
def test_seller_sales_stats_uses_frozen_card_prices(
    api_client: APIClient, seller, second_seller, product_factory
):
    old_price_product = product_factory(
        owner=seller,
        status=Product.Status.APPROVED,
        unit_price="200.00",
    )
    new_price_product = old_price_product
    other = product_factory(
        owner=second_seller,
        status=Product.Status.APPROVED,
        unit_price="999.00",
    )
    _paid_item(
        product=new_price_product,
        quantity=1,
        settled_price="200.00",
        paid_at=datetime(2026, 9, 2, 10, 0, tzinfo=UTC),
        order_id="a",
    )
    _paid_item(
        product=new_price_product,
        quantity=1,
        settled_price="200.00",
        paid_at=datetime(2026, 9, 3, 10, 0, tzinfo=UTC),
        order_id="b",
    )
    _paid_item(
        product=new_price_product,
        quantity=1,
        settled_price="250.00",
        paid_at=datetime(2026, 9, 10, 10, 0, tzinfo=UTC),
        order_id="c",
    )
    _paid_item(
        product=new_price_product,
        quantity=2,
        settled_price="250.00",
        paid_at=datetime(2026, 9, 11, 10, 0, tzinfo=UTC),
        order_id="d",
    )
    _paid_item(
        product=other,
        quantity=8,
        settled_price="999.00",
        paid_at=datetime(2026, 9, 10, 10, 0, tzinfo=UTC),
        order_id="other",
    )
    AfterbuyOrderItem.objects.create(
        order=AfterbuyOrder.objects.create(
            account=AfterbuyOrder.Account.JV,
            afterbuy_order_id="no-snap",
            marketplace=AfterbuyOrder.Marketplace.OTTO,
            paid_at=datetime(2026, 9, 10, 10, 0, tzinfo=UTC),
        ),
        item_key="no-snap",
        quantity=4,
        matched_product=new_price_product,
        marketplace=AfterbuyOrder.Marketplace.OTTO,
    )
    new_price_product.unit_price = Decimal("250.00")
    new_price_product.save(update_fields=["unit_price"])

    api_client.force_authenticate(user=seller)
    response = api_client.get("/api/v1/products/sales-stats/")
    assert response.status_code == 200, response.data
    assert response.data["quantity_sold"] == 5
    assert response.data["orders_count"] == 4
    assert response.data["amount_eur"] == "49.75"
    assert response.data["amount_try"] == "1150.00"
    assert "amount" not in response.data
    assert "currency" not in response.data
    assert "currency_try" not in response.data
    assert "scope" not in response.data
    assert "product_id" not in response.data
    assert "product_title" not in response.data
    assert "ean" not in response.data
    assert "seller_id" not in response.data
    assert "seller_email" not in response.data
    assert "seller_name" not in response.data
    assert "by_channel" not in response.data

    period = api_client.get(
        "/api/v1/products/sales-stats/?from=2026-09-10&to=2026-09-11"
    )
    assert period.status_code == 200
    assert period.data["quantity_sold"] == 3
    assert period.data["orders_count"] == 2
    assert period.data["amount_eur"] == "29.85"
    assert period.data["amount_try"] == "750.00"
    assert period.data["from"] == "2026-09-10"
    assert period.data["to"] == "2026-09-11"

    one_product = api_client.get(
        f"/api/v1/products/sales-stats/?product_id={new_price_product.id}"
    )
    assert one_product.data["quantity_sold"] == 5

    api_client.force_authenticate(user=second_seller)
    foreign = api_client.get(
        f"/api/v1/products/sales-stats/?product_id={new_price_product.id}"
    )
    assert foreign.status_code == 400


@pytest.mark.integration
@pytest.mark.django_db
def test_manager_sales_stats_filters_by_ean_and_seller_email(
    api_client: APIClient, seller, second_seller, manager, product_factory
):
    product = product_factory(
        owner=seller,
        status=Product.Status.APPROVED,
        unit_price="200.00",
        ean_jv="4006381333931",
    )
    other = product_factory(
        owner=second_seller,
        status=Product.Status.APPROVED,
        unit_price="50.00",
        ean_xl="4006381333999",
    )
    _paid_item(
        product=product,
        quantity=2,
        settled_price="200.00",
        paid_at=datetime(2026, 9, 10, 10, 0, tzinfo=UTC),
        order_id="m1",
    )
    _paid_item(
        product=other,
        quantity=1,
        settled_price="50.00",
        paid_at=datetime(2026, 9, 10, 10, 0, tzinfo=UTC),
        order_id="m2",
        account=AfterbuyOrder.Account.XL,
        marketplace=AfterbuyOrder.Marketplace.KAUFLAND,
    )

    api_client.force_authenticate(user=seller)
    assert api_client.get("/api/v1/manager/sales-stats/").status_code == 403

    api_client.force_authenticate(user=manager)
    overall = api_client.get("/api/v1/manager/sales-stats/")
    assert overall.status_code == 200, overall.data
    assert overall.data["quantity_sold"] == 3
    assert overall.data["orders_count"] == 2
    assert overall.data["amount_eur"] == "29.85"
    assert overall.data["scope"] == "all"
    channels = {
        (item["marketplace"], item["account"]): item["quantity_sold"]
        for item in overall.data["by_channel"]
    }
    assert channels[("otto", "jv")] == 2
    assert channels[("kaufland", "xl")] == 1
    assert channels[("hood", "jv")] == 0

    by_ean = api_client.get("/api/v1/manager/sales-stats/?ean=4006381333931")
    assert by_ean.data["quantity_sold"] == 2
    assert by_ean.data["amount_eur"] == "19.90"
    assert by_ean.data["scope"] == "product"
    assert by_ean.data["product_id"] == product.id

    by_email = api_client.get(
        f"/api/v1/manager/sales-stats/?seller_email={seller.email}"
    )
    assert by_email.status_code == 200
    assert by_email.data["quantity_sold"] == 2
    assert by_email.data["seller_email"] == seller.email
    assert by_email.data["scope"] == "seller"

    missing = api_client.get("/api/v1/manager/sales-stats/?ean=0000000000000")
    assert missing.status_code == 400
