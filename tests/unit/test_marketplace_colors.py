from decimal import Decimal
from types import SimpleNamespace

import pytest

from apps.marketplace.colors import german_color_name
from apps.marketplace.hood.payload_builder import build_hood_payload
from apps.marketplace.kaufland.payload_builder import (
    build_kaufland_create_payload,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("hex_color", "expected"),
    [
        ("#000000", "Schwarz"),
        ("#FFFFFF", "Weiß"),
        ("#5B91C8", "Blau"),
        ("#8B4513", "Braun"),
        ("#303030", "Anthrazit"),
        ("#005F6A", "Petrol"),
        ("#B87333", "Kupfer"),
    ],
)
def test_german_color_name_resolves_basic_color_families(hex_color, expected):
    assert german_color_name(hex_color) == expected


def test_german_color_name_rejects_invalid_hex():
    with pytest.raises(ValueError):
        german_color_name("blue")


def make_product():
    variant = SimpleNamespace(
        color_hex="#5B91C8",
        materials=["Wood", "Fabric"],
        width_cm=Decimal("50.00"),
        height_cm=Decimal("90.00"),
        length_cm=Decimal("55.00"),
        quantity=3,
    )
    return SimpleNamespace(
        ean_jv="4071489789768",
        ean_xl="",
        variants=SimpleNamespace(count=lambda: 1, get=lambda: variant),
        images=SimpleNamespace(
            all=lambda: [
                SimpleNamespace(
                    is_primary=True,
                    generated_images=SimpleNamespace(
                        all=lambda: [
                            SimpleNamespace(
                                mode="white",
                                image=SimpleNamespace(
                                    url="https://cdn.example/white.jpg"
                                ),
                            ),
                            SimpleNamespace(
                                mode="interior",
                                image=SimpleNamespace(
                                    url="https://cdn.example/interior.jpg"
                                ),
                            ),
                        ]
                    ),
                )
            ]
        ),
    )


def test_hood_and_kaufland_use_german_color_but_kaufland_uses_primary_material():
    product = make_product()

    hood_payload = build_hood_payload(
        product=product,
        account="jv",
        configuration={
            "title": "Test chair",
            "description": "<p>Detailed product description</p>",
            "price": "299.00",
            "category_id": "2412",
            "image_urls": ["https://example.com/chair.jpg"],
        },
    )
    kaufland_payload = build_kaufland_create_payload(
        product=product,
        account="jv",
        configuration={
            "title": "Test chair",
            "description": "Detailed product description",
            "price": "299.00",
            "delivery": 30,
            "image_urls": ["https://example.com/chair.jpg"],
        },
    )

    hood_color = next(
        item["value"]
        for item in hood_payload["product_properties"]
        if item["name"] == "Farbe"
    )
    assert hood_color == "Blau"
    assert kaufland_payload["color"] == "Blau"
    assert kaufland_payload["material"] == "Wood"
    assert hood_payload["image_urls"][0] == "https://cdn.example/white.jpg"
    assert kaufland_payload["picture"][0] == "https://cdn.example/white.jpg"
    assert "https://example.com/chair.jpg" not in hood_payload["image_urls"]
