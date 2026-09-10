import pytest

from apps.marketplace.materials import listing_materials
from apps.orchestrator.ai_content import (
    GeneratedContentValidationError,
    build_product_snapshot,
    build_universal_content_request,
    validate_universal_content,
)


@pytest.mark.unit
def test_content_request_forbids_source_language_in_customer_text():
    request = build_universal_content_request(
        product_snapshot={"seller_title": "Диван белый"}
    )
    instructions = request.instructions.lower()

    assert "write only in german" in instructions
    assert "never copy russian" in instructions
    assert "translate each seller material" in instructions
    assert "materials_de" not in instructions
    assert "claims not present in product data;\n" in request.instructions


@pytest.mark.unit
@pytest.mark.django_db
def test_product_snapshot_keeps_seller_materials(product_factory, seller):
    product = product_factory(owner=seller)
    product.variants.update(materials=["хлопок", "Wood"])

    snapshot = build_product_snapshot(product)

    variant = snapshot["variants"][0]
    assert variant["materials"] == ["хлопок", "Wood"]
    assert "materials_de" not in variant


@pytest.mark.unit
def test_listing_materials_uses_only_listing_field():
    chosen, error = listing_materials(
        configuration={"materials": ["Massivholz"]},
        variant_materials=["дерево", "ткань"],
    )
    assert error is None
    assert chosen == ["Massivholz"]

    chosen, error = listing_materials(
        configuration={},
        variant_materials=["Wood", "Fabric"],
    )
    assert chosen == []
    assert error == "Translate product materials to German."

    chosen, error = listing_materials(
        configuration={"materials": ["хлопок"]},
        variant_materials=["хлопок"],
    )
    assert error == "Materials must be in German."


@pytest.mark.unit
def test_rejects_ai_draft_without_translated_materials():
    with pytest.raises(GeneratedContentValidationError, match="German translations"):
        validate_universal_content(
            {
                "title": "Holzstuhl mit Stoffbezug",
                "description": (
                    "Ein stabiler Holzstuhl mit Stoffbezug.\n\n"
                    "Die Maße betragen 55 × 50 × 90 cm."
                ),
                "bullet_points": [
                    "Holzgestell",
                    "Stoffbezug",
                    "Farbe: Blau",
                ],
            },
            product_snapshot={
                "variants": [{"materials": ["дерево", "ткань"]}],
            },
        )
