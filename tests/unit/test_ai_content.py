import pytest

from apps.orchestrator.ai_content import (
    GeneratedContentValidationError,
    universal_content_to_marketplace_configuration,
    universal_description_to_hood_html,
    validate_universal_content,
)


@pytest.mark.unit
def test_validates_and_normalizes_universal_ai_content():
    content = validate_universal_content(
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
        }
    )

    assert content["title"] == "Holzstuhl mit Stoffbezug"
    assert len(content["bullet_points"]) == 3
    assert content["description"].count("\n\n") == 1
    assert content["materials"] == []
    assert content["color"] == ""
    assert content["material_composition"] == ""


@pytest.mark.unit
@pytest.mark.parametrize(
    "content",
    [
        {
            "title": "",
            "description": "Erster Absatz.\n\nZweiter Absatz.",
            "bullet_points": ["A", "B", "C"],
        },
        {
            "title": "Gültiger Titel",
            "description": "Nur ein Absatz.",
            "bullet_points": ["A", "B", "C"],
        },
        {
            "title": "Gültiger Titel",
            "description": "Erster Absatz.\n\nZweiter Absatz.",
            "bullet_points": ["Nur ein Punkt"],
        },
        {
            "title": "X" * 66,
            "description": "Erster Absatz.\n\nZweiter Absatz.",
            "bullet_points": ["A", "B", "C"],
        },
    ],
)
def test_rejects_invalid_universal_ai_content(content):
    with pytest.raises(GeneratedContentValidationError):
        validate_universal_content(content)


@pytest.mark.unit
def test_maps_one_draft_to_each_marketplace_configuration():
    content = {
        "title": "Holzstuhl mit Stoffbezug",
        "description": "Erster Absatz.\n\nZweiter Absatz.",
        "bullet_points": ["Holz", "Stoff", "Blau"],
        "materials": ["Holz", "Stoff"],
        "color": "Blau",
        "material_composition": "80% Polyester, 20% Baumwolle",
    }

    otto = universal_content_to_marketplace_configuration(
        marketplace="otto",
        content=content,
    )
    hood = universal_content_to_marketplace_configuration(
        marketplace="hood",
        content=content,
    )
    kaufland = universal_content_to_marketplace_configuration(
        marketplace="kaufland",
        content=content,
    )

    assert otto == {
        "product_line": f"{content['title']} (BD)",
        "description": content["description"],
        "bullet_points": content["bullet_points"],
        "materials": ["Holz", "Stoff"],
        "color": "Blau",
    }
    assert hood["title"] == f"{content['title']} (BD)"
    assert hood["description"] == ("<p>Erster Absatz.</p><p>Zweiter Absatz.</p>")
    assert hood["materials"] == ["Holz", "Stoff"]
    assert hood["color"] == "Blau"
    assert "material_composition" not in hood
    assert kaufland == {
        "title": f"{content['title']} (BD)",
        "description": content["description"],
        "materials": ["Holz", "Stoff"],
        "color": "Blau",
        "material_composition": "80% Polyester, 20% Baumwolle",
    }


@pytest.mark.unit
def test_hood_html_escapes_untrusted_text():
    result = universal_description_to_hood_html(
        "<script>alert('xss')</script>\n\nNormaler Text."
    )

    assert "<script>" not in result
    assert "&lt;script&gt;" in result


@pytest.mark.unit
def test_rejects_unknown_marketplace():
    with pytest.raises(ValueError, match="Unsupported marketplace"):
        universal_content_to_marketplace_configuration(
            marketplace="unknown",
            content={
                "title": "Stuhl",
                "description": "Absatz eins.\n\nAbsatz zwei.",
                "bullet_points": ["A", "B", "C"],
            },
        )
