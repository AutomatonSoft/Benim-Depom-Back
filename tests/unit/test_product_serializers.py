import pytest

from apps.products.serializers import (
    ProductImageReorderSerializer,
    ProductSerializer,
    ProductVariantSerializer,
)


def valid_variant(**overrides):
    data = {
        "color": " beyaz ",
        "materials": [" Wood ", "Fabric"],
        "width_cm": "50.00",
        "height_cm": "90.00",
        "length_cm": "55.00",
        "quantity": 3,
    }
    data.update(overrides)
    return data


@pytest.mark.unit
def test_variant_normalizes_color_and_materials():
    serializer = ProductVariantSerializer(data=valid_variant())

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["color"] == "beyaz"
    assert serializer.validated_data["materials"] == ["Wood", "Fabric"]


@pytest.mark.unit
def test_variant_allows_zero_quantity():
    serializer = ProductVariantSerializer(data=valid_variant(quantity=0))

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["quantity"] == 0


@pytest.mark.unit
@pytest.mark.parametrize(
    "overrides, field",
    [
        ({"color": "  "}, "color"),
        ({"materials": []}, "materials"),
        ({"materials": ["Wood", "wood"]}, "materials"),
        ({"materials": [" "]}, "materials"),
    ],
)
def test_variant_rejects_invalid_business_data(overrides, field):
    serializer = ProductVariantSerializer(data=valid_variant(**overrides))

    assert serializer.is_valid() is False
    assert field in serializer.errors


@pytest.mark.unit
def test_product_rejects_more_than_one_variant():
    serializer = ProductSerializer(
        data={
            "title": "Chair",
            "product_type": "chair",
            "unit_price": "10.00",
            "currency": "EUR",
            "warehouse_city": "IST",
            "variants": [
                valid_variant(),
                valid_variant(color="white"),
            ],
        }
    )

    assert serializer.is_valid() is False
    assert serializer.errors["variants"]


@pytest.mark.unit
def test_image_reorder_rejects_duplicate_ids():
    serializer = ProductImageReorderSerializer(data={"image_ids": [1, 1]})

    assert serializer.is_valid() is False
    assert "image_ids" in serializer.errors
