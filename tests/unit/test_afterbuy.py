from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.afterbuy.client import (
    AfterbuyAccountCredentials,
    build_get_sold_items_xml,
    format_afterbuy_datetime,
)
from apps.afterbuy.matching import catalog_ean_candidates
from apps.afterbuy.parser import (
    detect_marketplace,
    parse_afterbuy_datetime,
    parse_sold_items_xml,
)
from apps.afterbuy.types import NormalizedSoldItem

SAMPLE_XML = """
<Result>
  <HasMoreItems>1</HasMoreItems>
  <LastOrderID>9002</LastOrderID>
  <Orders>
    <Order>
      <OrderID>9002</OrderID>
      <PaymentInfo>
        <FullAmount>19,90</FullAmount>
        <PaymentCurrency>EUR</PaymentCurrency>
        <PaymentMethod>OTTO Payments</PaymentMethod>
        <PaymentDate>1.9.2026 9:05:00</PaymentDate>
      </PaymentInfo>
      <BuyerInfo>
        <BillingAddress>
          <FirstName>Ada</FirstName>
          <LastName>Buyer</LastName>
          <Mail>ada@example.com OTTO</Mail>
          <UserIDPlattform>Otto-778899</UserIDPlattform>
        </BillingAddress>
      </BuyerInfo>
      <SoldItems>
        <SoldItem>
          <ItemID>55</ItemID>
          <Anr>BD-100</Anr>
          <SKU>SKU-100</SKU>
          <EAN>4006381333931</EAN>
          <ItemTitle>Stuhl</ItemTitle>
          <ItemQuantity>2</ItemQuantity>
          <ItemPrice>9,95</ItemPrice>
          <ItemPlatformName>OTTO Market</ItemPlatformName>
          <AlternativeItemNumber1>AB-123</AlternativeItemNumber1>
        </SoldItem>
      </SoldItems>
    </Order>
  </Orders>
</Result>
"""


@pytest.mark.unit
def test_build_get_sold_items_xml_uses_paydate_and_range_id():
    credentials = AfterbuyAccountCredentials(
        account="jv",
        partner_token="pt",
        account_token="at",
    )
    berlin = ZoneInfo("Europe/Berlin")
    xml = build_get_sold_items_xml(
        credentials=credentials,
        date_from=datetime(2026, 9, 11, 0, 0, tzinfo=berlin),
        date_to=datetime(2026, 9, 11, 23, 59, 59, tzinfo=berlin),
        range_id_from="9001",
    )
    assert "<CallName>GetSoldItems</CallName>" in xml
    assert "<OrderDirection>0</OrderDirection>" in xml
    assert "<FilterValue>PayDate</FilterValue>" in xml
    assert "<ValueFrom>9001</ValueFrom>" in xml
    assert "PartnerToken>pt<" in xml
    assert "UserPassword" not in xml


@pytest.mark.unit
def test_parse_sold_items_extracts_otto_order_and_item():
    orders, has_more, last_order_id = parse_sold_items_xml(SAMPLE_XML)
    assert has_more is True
    assert last_order_id == "9002"
    assert len(orders) == 1
    order = orders[0]
    assert order.afterbuy_order_id == "9002"
    assert order.marketplace == "otto"
    assert str(order.total_amount) == "19.90"
    assert order.buyer_email == "ada@example.com"
    assert order.platform_order_number == "AB-123"
    item = order.items[0]
    assert item.anr == "BD-100"
    assert item.ean == "4006381333931"
    assert item.quantity == 2
    assert item.marketplace == "otto"


@pytest.mark.unit
def test_afterbuy_dates_accept_missing_leading_zeros():
    parsed = parse_afterbuy_datetime("1.9.2026 9:05:00")
    assert parsed is not None
    assert (
        format_afterbuy_datetime(
            datetime(2026, 9, 1, 9, 5, tzinfo=ZoneInfo("Europe/Berlin"))
        )
        == "01.09.2026 09:05:00"
    )


@pytest.mark.unit
def test_detect_marketplace_kaufland_and_hood():
    assert (
        detect_marketplace(
            platform_name="Kaufland.de",
            buyer_platform_user_id="",
            email="",
        )
        == "kaufland"
    )
    assert (
        detect_marketplace(
            platform_name="Hood.de",
            buyer_platform_user_id="",
            email="",
        )
        == "hood"
    )
    assert (
        detect_marketplace(
            platform_name="Hitflip",
            buyer_platform_user_id="",
            email="buyer@kaufland-marktplatz.de",
            payment_method="",
        )
        == "kaufland"
    )
    assert (
        detect_marketplace(
            platform_name="ebay",
            buyer_platform_user_id="",
            email="other@example.com",
            payment_method="Kaufland",
        )
        == "kaufland"
    )
    assert (
        detect_marketplace(
            platform_name="ebay",
            buyer_platform_user_id="Hood-Ada",
            email="5564363V15590B23374918@hood.de",
            payment_method="",
        )
        == "hood"
    )
    assert (
        detect_marketplace(
            platform_name="ebay",
            buyer_platform_user_id="",
            email="buyer@example.com",
            payment_method="HoodPay",
        )
        == "hood"
    )
    assert (
        detect_marketplace(
            platform_name="Hitflip",
            buyer_platform_user_id="Hitflip-user",
            email="someone@example.com",
            payment_method="PayPal",
        )
        == ""
    )


@pytest.mark.integration
@pytest.mark.django_db
def test_upsert_matches_jv_ean_and_notifies_managers(seller, manager, product_factory):
    from apps.afterbuy.models import AfterbuySaleNotification
    from apps.afterbuy.sync import upsert_sold_order
    from apps.notifications.models import Notification
    from apps.products.models import Product

    product = product_factory(
        owner=seller,
        status=Product.Status.APPROVED,
        ean_jv="4006381333931",
    )
    orders, _, _ = parse_sold_items_xml(SAMPLE_XML)
    stored = upsert_sold_order(account="jv", order=orders[0])
    item = stored.items.get()
    assert item.matched_product_id == product.id
    assert item.sale_notification.status == AfterbuySaleNotification.Status.SENT
    sold = Notification.objects.get(
        user=manager,
        product=product,
        notification_type=Notification.Type.PRODUCT_SOLD,
    )
    assert not Notification.objects.filter(
        user=seller,
        notification_type=Notification.Type.PRODUCT_SOLD,
    ).exists()
    assert "Afterbuy" in sold.body
    assert "уведомите продавца" in sold.body
    assert "2" in sold.body
    assert sold.afterbuy_order_item_id == item.id
    upsert_sold_order(account="jv", order=orders[0])
    assert (
        Notification.objects.filter(
            notification_type=Notification.Type.PRODUCT_SOLD
        ).count()
        == 1
    )


def _item(**overrides) -> NormalizedSoldItem:
    payload = {
        "item_id": "",
        "anr": "",
        "product_id": "",
        "sku": "",
        "alternative_item_number": "",
        "ean": "",
        "title": "Stuhl",
        "quantity": 1,
        "unit_price": Decimal("9.95"),
        "platform_name": "OTTO Market",
        "marketplace": "otto",
        "platform_order_number": "",
        "extra_tags": {},
    }
    payload.update(overrides)
    return NormalizedSoldItem(**payload)


@pytest.mark.unit
def test_catalog_ean_prefers_sku_when_ean_field_is_empty():
    item = _item(sku="4062292574733", ean="", title="Другое название")
    assert catalog_ean_candidates(item) == ["4062292574733"]


@pytest.mark.unit
def test_catalog_ean_ignores_title_and_short_codes():
    item = _item(sku="AB-12", ean="", anr="99", title="4062292574733")
    assert catalog_ean_candidates(item) == []


@pytest.mark.integration
@pytest.mark.django_db
def test_upsert_matches_jv_sku_when_afterbuy_ean_is_empty(seller, product_factory):
    from datetime import UTC, datetime
    from decimal import Decimal

    from apps.afterbuy.sync import upsert_sold_order
    from apps.afterbuy.types import NormalizedSoldItem, NormalizedSoldOrder
    from apps.products.models import Product

    product = product_factory(
        owner=seller,
        status=Product.Status.APPROVED,
        ean_jv="4062292574733",
    )
    item = NormalizedSoldItem(
        item_id="1",
        anr="",
        product_id="",
        sku="4062292574733",
        alternative_item_number="",
        ean="",
        title="Любое имя с площадки",
        quantity=2,
        unit_price=Decimal("10.00"),
        platform_name="OTTO Market",
        marketplace="otto",
        platform_order_number="",
    )
    order = NormalizedSoldOrder(
        afterbuy_order_id="9003",
        paid_at=datetime(2026, 9, 16, 12, 0, tzinfo=UTC),
        total_amount=Decimal("20.00"),
        currency="EUR",
        payment_method="OTTO Payments",
        platform_order_number="",
        marketplace="otto",
        buyer_name="Ada",
        buyer_email="ada@example.com",
        buyer_platform_user_id="Otto-1",
        items=(item,),
    )
    stored = upsert_sold_order(account="jv", order=order)
    assert stored.items.get().matched_product_id == product.id


@pytest.mark.integration
@pytest.mark.django_db
def test_manager_syncs_afterbuy_stock_and_notifies_seller(
    api_client, seller, manager, product_factory, monkeypatch
):
    from apps.afterbuy.sync import upsert_sold_order
    from apps.notifications.models import Notification
    from apps.orchestrator.models import MarketplaceJob, MarketplacePublication
    from apps.products.models import Product

    product = product_factory(
        owner=seller,
        status=Product.Status.APPROVED,
        ean_jv="4006381333931",
        ean_xl="4006381333932",
    )
    for marketplace, account, ean in (
        ("otto", "jv", product.ean_jv),
        ("hood", "jv", product.ean_jv),
        ("otto", "xl", product.ean_xl),
        ("kaufland", "xl", product.ean_xl),
    ):
        MarketplacePublication.objects.create(
            product=product,
            marketplace=marketplace,
            account=account,
            ean=ean,
            status=MarketplacePublication.Status.ACTIVE,
        )

    orders, _, _ = parse_sold_items_xml(SAMPLE_XML)
    upsert_sold_order(account="jv", order=orders[0])
    note = Notification.objects.get(
        user=manager,
        notification_type=Notification.Type.PRODUCT_SOLD,
    )

    monkeypatch.setattr(
        "apps.afterbuy.sale_actions.fetch_selling_channel_quantity",
        lambda **_kwargs: 18,
    )
    captured: dict = {}

    def fake_create_marketplace_job(*, product, requested_by, operation, targets):
        captured["targets"] = targets
        return MarketplaceJob.objects.create(
            product=product,
            requested_by=requested_by,
            operation=operation,
            requested_channels=[item["marketplace"] for item in targets],
            request_payload={
                "targets": targets,
                "target_payloads": {},
                "payloads": {},
                "accounts": {},
            },
        )

    monkeypatch.setattr(
        "apps.afterbuy.sale_actions.create_marketplace_job",
        fake_create_marketplace_job,
    )
    monkeypatch.setattr(
        "apps.afterbuy.views.execute_marketplace_job.delay",
        lambda *_args, **_kwargs: None,
    )

    api_client.force_authenticate(user=manager)
    list_response = api_client.get("/api/v1/notifications/?category=afterbuy")
    assert list_response.status_code == 200
    assert list_response.data["results"][0]["id"] == note.id

    sync_response = api_client.post(
        f"/api/v1/notifications/{note.id}/afterbuy-sync-stock/"
    )
    assert sync_response.status_code == 202, sync_response.data
    assert sync_response.data["qty_after"] == 18
    pairs = {(item["marketplace"], item["account"]) for item in captured["targets"]}
    assert ("otto", "jv") not in pairs
    assert pairs == {("hood", "jv"), ("otto", "xl"), ("kaufland", "xl")}
    product.variants.get().refresh_from_db()
    assert product.variants.get().quantity == 18

    notify_response = api_client.post(
        f"/api/v1/notifications/{note.id}/afterbuy-notify-seller/"
    )
    assert notify_response.status_code == 201
    seller_note = Notification.objects.get(
        user=seller,
        notification_type=Notification.Type.PRODUCT_SOLD,
    )
    assert "Ваш товар купили" in seller_note.title
    assert "Afterbuy" not in seller_note.body
    repeat = api_client.post(f"/api/v1/notifications/{note.id}/afterbuy-notify-seller/")
    assert repeat.status_code == 200
    assert (
        Notification.objects.filter(
            user=seller,
            notification_type=Notification.Type.PRODUCT_SOLD,
        ).count()
        == 1
    )

