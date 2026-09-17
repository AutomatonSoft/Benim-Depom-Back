from decimal import Decimal
from types import SimpleNamespace

import pytest

from apps.catalog.otto_catalog import get_otto_catalog
from apps.marketplace.otto.payload_builder import (
    OttoPayloadValidationError,
    build_otto_payload,
    calculate_uvp,
)
from apps.orchestrator.serializers import OttoListingConfigurationSerializer

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("price", "expected"),
    [
        ("999.99", "1349.99"),
        ("1000.00", "1250.00"),
        ("2500.00", "2950.00"),
        ("4999.99", "5899.99"),
        ("5000.00", "5500.00"),
    ],
)
def test_calculate_uvp_uses_all_price_bands_without_gap(price, expected):
    assert calculate_uvp(Decimal(price)) == Decimal(expected)


def make_product(*, ean="4071489789744", variant_count=1, quantity=20):
    catalog = get_otto_catalog()
    category = next(
        item
        for item in catalog["categories_by_id"].values()
        if item["category_group_id"] in catalog["attributes_by_id_by_group_id"]
    )
    group_id = int(category["category_group_id"])
    attributes = catalog["attributes_by_group_id"][group_id]
    attribute = next(item for item in attributes if item["type"] == "STRING")

    return SimpleNamespace(
        pk=42,
        ean_jv=ean,
        ean_xl="",
        otto_category_name=category["name"],
        otto_category_group_id=group_id,
        otto_attributes={str(attribute["attributeId"]): "Test value"},
        variants=SimpleNamespace(
            count=lambda: variant_count,
            all=lambda: [SimpleNamespace(quantity=quantity)] * variant_count,
        ),
        images=cover_listing_images(),
    )


def cover_listing_images(*, url="https://cdn.example/white.jpg"):
    generated = [
        SimpleNamespace(mode="white", image=SimpleNamespace(url=url)),
        SimpleNamespace(
            mode="interior",
            image=SimpleNamespace(url="https://cdn.example/interior.jpg"),
        ),
        SimpleNamespace(
            mode="human", image=SimpleNamespace(url="https://cdn.example/human.jpg")
        ),
    ]
    cover = SimpleNamespace(
        is_primary=True,
        generated_images=SimpleNamespace(all=lambda: generated),
    )
    return SimpleNamespace(all=lambda: [cover])


def valid_configuration():
    return {
        "product_line": "Teststuhl aus Holz",
        "standard_price": "1000.00",
        "vat": "FULL",
        "shipping_profile_id": "b4139e65-603f-52f7-9b99-393cf6b2461f",
        "media_urls": ["https://xlmeubilair.nl/api-media/test-chair.jpg"],
        "description": "Deutsche Beschreibung",
        "bullet_points": ["Massivholz", "Modernes Design"],
    }


def test_build_otto_payload_maps_category_attributes_and_msrp():
    payload = build_otto_payload(
        product=make_product(),
        account="jv",
        configuration=valid_configuration(),
    )

    item = payload[0]
    assert item["productReference"] == "4071489789744"
    assert item["sku"] == "4071489789744"
    assert item["ean"] == "4071489789744"
    assert item["quantity"] == 20
    assert item["pricing"]["standardPrice"] == {
        "amount": 1000.0,
        "currency": "EUR",
    }
    assert item["pricing"]["msrp"] == {
        "amount": 1250.0,
        "currency": "EUR",
    }
    assert item["delivery"] == {
        "type": "FORWARDER_CURBSIDE",
        "deliveryTime": 5,
    }
    assert item["productDescription"]["attributes"][0]["values"] == ["Test value"]
    assert item["mediaAssets"] == [
        {"type": "IMAGE", "location": "https://cdn.example/white.jpg"},
        {"type": "IMAGE", "location": "https://cdn.example/interior.jpg"},
        {"type": "IMAGE", "location": "https://cdn.example/human.jpg"},
    ]


def test_build_otto_payload_ignores_configured_seller_photos():
    product = make_product()
    configuration = valid_configuration()
    configuration["media_urls"] = ["https://seller.example/warehouse.jpg"]
    item = build_otto_payload(
        product=product,
        account="jv",
        configuration=configuration,
    )[0]
    locations = [asset["location"] for asset in item["mediaAssets"]]
    assert "https://seller.example/warehouse.jpg" not in locations
    assert "https://cdn.example/white.jpg" in locations


def test_build_otto_payload_requires_generated_cover_images():
    product = make_product()
    product.images = SimpleNamespace(all=lambda: [])
    with pytest.raises(OttoPayloadValidationError) as error:
        build_otto_payload(
            product=product,
            account="jv",
            configuration=valid_configuration(),
        )
    assert "media_urls" in error.value.errors


def test_build_otto_payload_rejects_missing_ean_and_incomplete_configuration():
    configuration = valid_configuration()
    configuration.pop("shipping_profile_id")

    with pytest.raises(OttoPayloadValidationError) as error:
        build_otto_payload(
            product=make_product(ean=""),
            account="jv",
            configuration=configuration,
        )

    assert "ean" in error.value.errors
    assert "shipping_profile_id" in error.value.errors


def test_build_otto_payload_rejects_multiple_internal_variants():
    with pytest.raises(OttoPayloadValidationError) as error:
        build_otto_payload(
            product=make_product(variant_count=2),
            account="jv",
            configuration=valid_configuration(),
        )

    assert "variants" in error.value.errors


def test_build_otto_payload_includes_filled_optional_ottt_fields():
    configuration = valid_configuration()
    configuration.update(
        {
            "mpn": "CHAIR-2026-BROWN",
            "offering_start_date": "2026-09-01T10:00:00Z",
            "release_date": "2026-08-25T10:00:00Z",
            "order_max_quantity": 2,
            "order_period_in_days": 30,
        }
    )

    item = build_otto_payload(
        product=make_product(),
        account="jv",
        configuration=configuration,
    )[0]

    assert item["mpn"] == "CHAIR-2026-BROWN"
    assert item["offeringStartDate"] == "2026-09-01T10:00:00+00:00"
    assert item["releaseDate"] == "2026-08-25T10:00:00+00:00"
    assert item["order"] == {"maxOrderQuantity": {"quantity": 2, "periodInDays": 30}}


def test_build_otto_payload_sends_variant_quantity_including_zero():
    item = build_otto_payload(
        product=make_product(quantity=0),
        account="jv",
        configuration=valid_configuration(),
    )[0]
    assert item["quantity"] == 0


def test_otto_configuration_serializer_returns_dates_from_json_storage():
    serializer = OttoListingConfigurationSerializer(
        instance={
            "offering_start_date": "2026-09-01T10:00:00+00:00",
            "release_date": "2026-08-25T10:00:00Z",
        }
    )

    assert serializer.data["offering_start_date"] == "2026-09-01T10:00:00Z"
    assert serializer.data["release_date"] == "2026-08-25T10:00:00Z"
