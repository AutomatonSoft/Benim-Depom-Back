from decimal import Decimal
from types import SimpleNamespace

import pytest

from apps.marketplace.colors import listing_color
from apps.marketplace.hood.payload_builder import (
    HoodPayloadValidationError,
    build_hood_payload,
)
from apps.marketplace.kaufland.payload_builder import (
    build_kaufland_create_payload,
)

pytestmark = pytest.mark.unit


def make_product():
    variant = SimpleNamespace(
        color="beyaz",
        materials=["Wood", "Fabric"],
        material_composition="",
        width_cm=Decimal("50.00"),
        height_cm=Decimal("90.00"),
        length_cm=Decimal("55.00"),
        quantity=3,
    )
    return SimpleNamespace(
        ean_jv="4071489789768",
        ean_xl="",
        listing_price_eur_override=None,
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


def test_listing_color_uses_only_listing_field():
    chosen, error = listing_color(
        configuration={"color": "Weiß"},
        variant_color="beyaz",
    )
    assert error is None
    assert chosen == "Weiß"

    chosen, error = listing_color(configuration={}, variant_color="siyah")
    assert chosen == ""
    assert error == "Translate the product color to German."

    chosen, error = listing_color(
        configuration={"color": "açık mavi"},
        variant_color="beyaz",
    )
    assert error == "Color must be in German."


def test_hood_and_kaufland_use_listing_german_color():
    product = make_product()
    configuration = {
        "title": "Test chair",
        "description": "<p>Detailed product description</p>",
        "price": "299.00",
        "category_id": "2412",
        "image_urls": ["https://example.com/chair.jpg"],
        "materials": ["Holz", "Stoff"],
        "color": "Weiß",
    }

    hood_payload = build_hood_payload(
        product=product,
        account="jv",
        configuration=configuration,
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
            "materials": ["Holz", "Stoff"],
            "color": "Weiß",
        },
    )

    hood_color = next(
        item["value"]
        for item in hood_payload["product_properties"]
        if item["name"] == "Farbe"
    )
    assert hood_color == "Weiß"
    assert kaufland_payload["color"] == "Weiß"
    assert kaufland_payload["material"] == "Holz"
    assert "material_composition" not in kaufland_payload
    hood_material = next(
        item["value"]
        for item in hood_payload["product_properties"]
        if item["name"] == "Material"
    )
    assert hood_material == "Holz, Stoff"
    assert hood_payload["image_urls"][0] == "https://cdn.example/white.jpg"
    assert kaufland_payload["picture"][0] == "https://cdn.example/white.jpg"
    assert "https://example.com/chair.jpg" not in hood_payload["image_urls"]


def test_hood_rejects_seller_language_listing_color():
    product = make_product()
    with pytest.raises(HoodPayloadValidationError) as caught:
        build_hood_payload(
            product=product,
            account="jv",
            configuration={
                "title": "Test chair",
                "description": "<p>Detailed product description</p>",
                "price": "299.00",
                "materials": ["Holz"],
                "color": "açık mavi",
            },
        )
    assert caught.value.errors["color"] == "Color must be in German."


def test_kaufland_payload_uses_default_delivery_when_missing():
    product = make_product()

    kaufland_payload = build_kaufland_create_payload(
        product=product,
        account="jv",
        configuration={
            "title": "Test chair",
            "description": "Detailed product description",
            "price": "299.00",
            "materials": ["Holz"],
            "color": "Weiß",
        },
    )

    assert kaufland_payload["delivery"] == 32


def test_hood_payload_uses_default_category_when_missing():
    product = make_product()

    hood_payload = build_hood_payload(
        product=product,
        account="jv",
        configuration={
            "title": "Test chair",
            "description": "<p>Detailed product description</p>",
            "price": "299.00",
            "materials": ["Holz"],
            "color": "Weiß",
        },
    )

    assert hood_payload["categoryID"] == "2412"


def test_kaufland_sends_textile_composition_only_when_set():
    product = make_product()
    product.variants.get().material_composition = "100% Polyester"
    payload = build_kaufland_create_payload(
        product=product,
        account="jv",
        configuration={
            "title": "Test sofa",
            "description": "Detailed product description",
            "price": "299.00",
            "materials": ["Textil"],
            "material_composition": "100% Polyester",
            "color": "Weiß",
        },
    )
    assert payload["material"] == "Textil"
    assert payload["material_composition"] == "100% Polyester"
    assert payload["parts_of_animal_origin"] == "No"


def test_kaufland_marks_real_leather_as_animal_origin():
    product = make_product()
    product.variants.get().material_composition = "100% Leder"
    payload = build_kaufland_create_payload(
        product=product,
        account="jv",
        configuration={
            "title": "Leather sofa",
            "description": "Detailed product description",
            "price": "299.00",
            "materials": ["Leder"],
            "material_composition": "100% Leder",
            "color": "Schwarz",
        },
    )
    assert payload["material"] == "Leder"
    assert payload["material_composition"] == "100% Leder"
    assert payload["parts_of_animal_origin"] == "Yes"
