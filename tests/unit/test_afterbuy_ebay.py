from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from apps.afterbuy.matching import match_catalog_product
from apps.afterbuy.parser import detect_marketplace, parse_sold_items_xml
from apps.marketplace.ebay.preparation import _save_state
from tests.unit.test_afterbuy import SAMPLE_XML, _item


def test_ebay_managed_payment_and_listing_number():
    xml = (
        SAMPLE_XML.replace("OTTO Payments", "eBay Managed Payment")
        .replace("OTTO Market", "")
        .replace("Otto-778899", "buyer")
        .replace("ada@example.com OTTO", "buyer@example.com")
        .replace("BD-100", "860071390916")
    )
    orders, _, _ = parse_sold_items_xml(xml)
    assert orders[0].marketplace == "ebay"
    assert orders[0].items[0].anr == "860071390916"
    assert orders[0].items[0].item_id == "55"
    assert (
        detect_marketplace(
            platform_name="",
            buyer_platform_user_id="",
            email="",
            payment_method="eBay Managed Payment",
        )
        == "ebay"
    )


def test_publication_preserves_old_listing_ids(monkeypatch):
    sync_publication = MagicMock()
    monkeypatch.setattr(
        "apps.marketplace.ebay.orchestration.sync_publication", sync_publication
    )
    preparation = SimpleNamespace(
        listing_id="100", listing_id_history=["99"], save=MagicMock()
    )
    _save_state(preparation, listing_id="101", status="published")
    assert preparation.listing_id_history == ["99", "100", "101"]
    _save_state(preparation, listing_id="101")
    assert preparation.listing_id_history == ["99", "100", "101"]
    assert "listing_id_history" in preparation.save.call_args.kwargs["update_fields"]
    assert sync_publication.call_count == 2
    sync_publication.assert_called_with(preparation)


@pytest.mark.django_db
def test_ebay_matches_listing_history_not_ean(seller, product_factory):
    from apps.marketplace.ebay.models import EbayDraft, EbayOfferPreparation
    from apps.products.models import Product

    product = product_factory(
        owner=seller, status=Product.Status.APPROVED, ean_jv="4071489789904"
    )
    draft = EbayDraft.objects.create(
        product=product, environment="production", account="depotum"
    )
    preparation = EbayOfferPreparation.objects.create(
        draft=draft,
        environment="production",
        account="depotum",
        sku=product.ean_jv,
        payload_hash="a" * 64,
        status="published",
        listing_id="860071390916",
        listing_id_history=["860071390915", "860071390916"],
    )
    item = _item(
        marketplace="ebay", platform_name="eBay", anr="860071390915", sku=product.ean_jv
    )
    assert match_catalog_product(account="jv", item=item) == product
    assert (
        match_catalog_product(account="jv", item=replace(item, anr="860071390916"))
        == product
    )
    assert (
        match_catalog_product(account="jv", item=replace(item, anr="206214422584"))
        is None
    )
    assert match_catalog_product(account="xl", item=item) is None
    preparation.environment = "sandbox"
    preparation.save(update_fields=["environment"])
    assert match_catalog_product(account="jv", item=item) is None


@pytest.mark.django_db
def test_ebay_import_is_idempotent_and_excludes_other_listings(
    seller, product_factory, monkeypatch
):
    from apps.afterbuy.models import AfterbuyOrderItem
    from apps.afterbuy.stats import sales_stats
    from apps.afterbuy.sync import upsert_sold_order
    from apps.marketplace.ebay.models import EbayDraft, EbayOfferPreparation
    from apps.products.models import Product

    product = product_factory(owner=seller, status=Product.Status.APPROVED)
    draft = EbayDraft.objects.create(
        product=product, environment="production", account="depotum"
    )
    EbayOfferPreparation.objects.create(
        draft=draft,
        environment="production",
        account="depotum",
        sku="4071489789904",
        payload_hash="a" * 64,
        status="withdrawn",
        listing_id="860071390916",
    )
    orders, _, _ = parse_sold_items_xml(SAMPLE_XML)
    order = replace(
        orders[0],
        marketplace="ebay",
        payment_method="eBay Managed Payment",
        items=(
            _item(item_id="1", marketplace="ebay", anr="860071390916"),
            _item(item_id="2", marketplace="ebay", anr="206214422584"),
        ),
    )
    monkeypatch.setattr("apps.afterbuy.sync.queue_sale_notification", MagicMock())
    upsert_sold_order(account="jv", order=order)
    upsert_sold_order(account="jv", order=order)
    assert AfterbuyOrderItem.objects.count() == 1
    assert AfterbuyOrderItem.objects.get().matched_product_id == product.pk
    channels = sales_stats(product=product, include_channels=True)["by_channel"]
    assert (
        next(row for row in channels if row["marketplace"] == "ebay")["quantity_sold"]
        == 1
    )
