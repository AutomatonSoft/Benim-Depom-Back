import pytest

from apps.marketplace.materials import german_material_name
from apps.orchestrator.ai_content import (
    build_product_snapshot,
    build_universal_content_request,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("source", "german"),
    [
        ("хлопок", "Baumwolle"),
        ("Cotton", "Baumwolle"),
        ("pamuk", "Baumwolle"),
        ("wood", "Holz"),
        ("дерево", "Holz"),
        ("Fabric", "Stoff"),
        ("эко-кожа", "Kunstleder"),
        ("unknown-material", None),
        ("  ", None),
    ],
)
def test_maps_common_seller_materials_to_german(source, german):
    assert german_material_name(source) == german


@pytest.mark.unit
def test_content_request_forbids_source_language_in_customer_text():
    request = build_universal_content_request(
        product_snapshot={"seller_title": "Диван белый"}
    )
    instructions = request.instructions.lower()

    assert "write only in german" in instructions
    assert "never copy russian" in instructions
    assert "materials_de" in instructions
    assert "claims not present in product data;\n" in request.instructions


@pytest.mark.unit
@pytest.mark.django_db
def test_product_snapshot_adds_german_material_names(product_factory, seller):
    product = product_factory(owner=seller)
    product.variants.update(materials=["хлопок", "Wood"])

    snapshot = build_product_snapshot(product)

    variant = snapshot["variants"][0]
    assert variant["materials"] == ["хлопок", "Wood"]
    assert variant["materials_de"] == ["Baumwolle", "Holz"]
