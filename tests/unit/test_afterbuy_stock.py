import pytest

from apps.afterbuy.stock import (
    parse_hood_quantity,
    parse_kaufland_quantity,
    parse_otto_quantity,
    read_quantities_for_sale,
    sibling_marketplaces,
    sync_stock_after_sale,
)


@pytest.mark.unit
def test_parse_otto_quantity_from_sku_payload():
    assert (
        parse_otto_quantity(
            {
                "sku": "4062292574733",
                "lastModified": "2025-02-17T09:56:31.157145Z",
                "quantity": 20,
            }
        )
        == 20
    )


@pytest.mark.unit
def test_parse_kaufland_quantity_from_units_amount():
    payload = {
        "response_data": {
            "ean": ["4062292268427"],
            "units": [{"condition": "NEW", "amount": 19, "id_unit": 1}],
        }
    }
    assert parse_kaufland_quantity(payload) == 19


@pytest.mark.unit
def test_parse_hood_quantity_from_quantity_field():
    assert parse_hood_quantity({"quantity": 15, "title": "Stuhl"}) == 15


@pytest.mark.integration
@pytest.mark.django_db
def test_sale_quantities_use_live_channel_when_available(
    seller, product_factory, monkeypatch
):
    from apps.afterbuy.models import AfterbuyOrder, AfterbuyOrderItem
    from apps.products.models import Product

    product = product_factory(
        owner=seller,
        status=Product.Status.APPROVED,
        ean_jv="4006381333931",
    )
    order = AfterbuyOrder.objects.create(
        account=AfterbuyOrder.Account.JV,
        afterbuy_order_id="q-1",
        marketplace=AfterbuyOrder.Marketplace.OTTO,
    )
    item = AfterbuyOrderItem.objects.create(
        order=order,
        item_key="1",
        sku="4006381333931",
        ean="4006381333931",
        quantity=2,
        marketplace=AfterbuyOrder.Marketplace.OTTO,
        matched_product=product,
    )
    monkeypatch.setattr(
        "apps.afterbuy.stock.fetch_selling_channel_quantity",
        lambda **kwargs: 18,
    )

    snapshot = read_quantities_for_sale(item)
    assert snapshot.qty_after == 18
    assert snapshot.qty_before == 20
    assert snapshot.from_marketplace is True
    assert product.variants.get().quantity == 3


@pytest.mark.unit
def test_siblings_exclude_the_sold_channel():
    assert sibling_marketplaces("otto") == ("hood", "kaufland")
    assert sibling_marketplaces("hood") == ("otto", "kaufland")


@pytest.mark.integration
@pytest.mark.django_db
def test_sync_writes_siblings_not_sold_channel(seller, product_factory, monkeypatch):
    from apps.afterbuy.models import AfterbuyOrder, AfterbuyOrderItem
    from apps.afterbuy.stock import SaleStockSnapshot
    from apps.products.models import Product

    product = product_factory(
        owner=seller,
        status=Product.Status.APPROVED,
        ean_jv="4006381333931",
    )
    order = AfterbuyOrder.objects.create(
        account=AfterbuyOrder.Account.JV,
        afterbuy_order_id="q-2",
        marketplace=AfterbuyOrder.Marketplace.OTTO,
    )
    item = AfterbuyOrderItem.objects.create(
        order=order,
        item_key="1",
        sku="4006381333931",
        ean="4006381333931",
        quantity=2,
        marketplace=AfterbuyOrder.Marketplace.OTTO,
        matched_product=product,
    )
    written = []

    def fake_write(**kwargs):
        written.append(kwargs["marketplace"])
        return True

    monkeypatch.setattr("apps.afterbuy.stock.write_channel_quantity", fake_write)

    sync_stock_after_sale(
        item,
        SaleStockSnapshot(qty_before=20, qty_after=18, from_marketplace=True),
    )

    assert written == ["hood", "kaufland"]
    product.variants.get().refresh_from_db()
    assert product.variants.get().quantity == 18


@pytest.mark.integration
@pytest.mark.django_db
def test_sync_skips_when_marketplace_qty_unknown(seller, product_factory, monkeypatch):
    from apps.afterbuy.models import AfterbuyOrder, AfterbuyOrderItem
    from apps.afterbuy.stock import SaleStockSnapshot
    from apps.products.models import Product

    product = product_factory(
        owner=seller,
        status=Product.Status.APPROVED,
        ean_jv="4006381333931",
    )
    order = AfterbuyOrder.objects.create(
        account=AfterbuyOrder.Account.JV,
        afterbuy_order_id="q-3",
        marketplace=AfterbuyOrder.Marketplace.OTTO,
    )
    item = AfterbuyOrderItem.objects.create(
        order=order,
        item_key="1",
        quantity=2,
        marketplace=AfterbuyOrder.Marketplace.OTTO,
        matched_product=product,
    )
    monkeypatch.setattr(
        "apps.afterbuy.stock.write_channel_quantity",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not write")),
    )
    sync_stock_after_sale(
        item,
        SaleStockSnapshot(qty_before=3, qty_after=3, from_marketplace=False),
    )
    assert product.variants.get().quantity == 3


@pytest.mark.unit
def test_cancel_restore_when_one_channel_grows_by_sold_qty():
    from apps.afterbuy.reconcile import decide_stock_action

    action, channel, qty = decide_stock_action(
        previous={"otto": 18, "hood": 18, "kaufland": 18},
        live={"otto": 20, "hood": 18, "kaufland": 18},
        last_sold_qty={"otto": 2},
    )
    assert action == "cancel_restore"
    assert channel == "otto"
    assert qty == 20


@pytest.mark.unit
def test_dirty_increase_without_matching_sale_is_skipped():
    from apps.afterbuy.reconcile import decide_stock_action

    action, _, _ = decide_stock_action(
        previous={"otto": 18, "hood": 18, "kaufland": 18},
        live={"otto": 25, "hood": 18, "kaufland": 18},
        last_sold_qty={"otto": 2},
    )
    assert action == "skip_dirty"


@pytest.mark.integration
@pytest.mark.django_db
def test_reconcile_restores_siblings_on_cancel(seller, product_factory, monkeypatch):
    from datetime import UTC, datetime

    from apps.afterbuy.models import (
        AfterbuyChannelStock,
        AfterbuyOrder,
        AfterbuyOrderItem,
    )
    from apps.afterbuy.reconcile import reconcile_product_account
    from apps.products.models import Product

    product = product_factory(
        owner=seller,
        status=Product.Status.APPROVED,
        ean_jv="4006381333931",
    )
    order = AfterbuyOrder.objects.create(
        account=AfterbuyOrder.Account.JV,
        afterbuy_order_id="q-4",
        marketplace=AfterbuyOrder.Marketplace.OTTO,
        paid_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    AfterbuyOrderItem.objects.create(
        order=order,
        item_key="1",
        quantity=2,
        marketplace=AfterbuyOrder.Marketplace.OTTO,
        matched_product=product,
    )
    for channel, qty in (("otto", 18), ("hood", 18), ("kaufland", 18)):
        AfterbuyChannelStock.objects.create(
            product=product,
            account="jv",
            marketplace=channel,
            quantity=qty,
            observed_at=datetime(2026, 9, 1, tzinfo=UTC),
        )
    live = {"otto": 20, "hood": 18, "kaufland": 18}
    monkeypatch.setattr(
        "apps.afterbuy.reconcile.fetch_selling_channel_quantity",
        lambda **kwargs: live[kwargs["marketplace"]],
    )
    written = []
    monkeypatch.setattr(
        "apps.afterbuy.stock.write_channel_quantity",
        lambda **kwargs: written.append(kwargs["marketplace"]) or True,
    )

    assert (
        reconcile_product_account(product=product, account="jv", request_id="t")
        == "cancel_restore"
    )
    assert written == ["hood", "kaufland"]
    product.variants.get().refresh_from_db()
    assert product.variants.get().quantity == 20


@pytest.mark.integration
@pytest.mark.django_db
def test_canonical_qty_can_be_zero(seller, product_factory):
    from apps.afterbuy.stock import apply_canonical_qty_to_product
    from apps.products.models import Product

    product = product_factory(owner=seller, status=Product.Status.APPROVED)
    apply_canonical_qty_to_product(product, 0)
    assert product.variants.get().quantity == 0

