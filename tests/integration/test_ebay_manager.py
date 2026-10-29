from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.marketplace.ebay.localization import TERMS, localize_catalog
from apps.marketplace.ebay.manager import manager_draft_values
from apps.marketplace.ebay.models import EbayDraft, EbayOfferPreparation
from apps.orchestrator.models import (
    MarketplaceContentGeneration,
    MarketplaceListingConfiguration,
)
from apps.products.models import ProductSetPart


def test_set_dimension_sync_keeps_named_rows_without_duplicating_them(settings):
    main = SimpleNamespace(width_cm=50, length_cm=55, height_cm=90, quantity=2)
    part = SimpleNamespace(width_cm=70, length_cm=80, height_cm=90)
    product = SimpleNamespace(
        variants=SimpleNamespace(all=lambda: [main]), set_parts=[part, part]
    )
    schema = {"aspects": []}
    with (
        patch(
            "apps.marketplace.ebay.catalog.get_category_aspects", return_value=schema
        ),
        patch(
            "apps.marketplace.ebay.manager.public_generated_listing_urls",
            return_value=[],
        ),
        patch(
            "apps.marketplace.ebay.manager.safe_listing_price_eur", return_value=None
        ),
    ):
        rows = manager_draft_values(
            product, values={"category_id": "38208", "aspects": {}}
        )["aspects"]
        assert (
            len(rows) == 2
        )  # Identical dimensions still belong to two different set pieces.
        named = {"Maße Sessel (B × L × H)": ["70 × 80 × 90 cm"]}
        assert (
            manager_draft_values(
                product, values={"category_id": "38208", "aspects": named}
            )["aspects"]
            == named
        )


@pytest.mark.django_db
@pytest.mark.parametrize("environment", ["sandbox", "production"])
def test_common_ai_apply_saves_depotum_text_and_keeps_legacy_channel(
    api_client, manager, product_factory, settings, environment
):
    settings.EBAY_ENVIRONMENT = environment
    product = product_factory(owner=manager)
    targets = [
        {"marketplace": "otto", "account": "jv"},
        {"marketplace": "ebay", "account": "depotum"},
    ]
    content = {
        "title": "Stuhl",
        "ebay_title": "Lederstuhl " + "A" * 64,
        "description": "Erster Absatz.\n\nZweiter Absatz.",
        "bullet_points": ["A", "B", "C"],
        "materials": [],
        "color": "Blau",
        "material_composition": "",
    }
    job = MarketplaceContentGeneration.objects.create(
        product=product,
        requested_by=manager,
        status="succeeded",
        targets=targets,
        result={"universal": {"content": content}},
    )
    api_client.force_authenticate(manager)
    response = api_client.post(
        f"/api/v1/orchestrator/products/{product.pk}/ai-content/generations/{job.pk}/apply/",
        {"targets": targets, "overwrite": True},
        format="json",
    )
    assert response.status_code == 200, response.data
    draft = EbayDraft.objects.get(
        product=product, environment=environment, account="depotum"
    )
    assert draft.title == content["ebay_title"] + " BD"
    assert len(draft.title) == 78
    assert draft.description == content["description"]
    assert draft.aspects == {"Farbe": ["Blau"]}
    assert draft.category_id == ""
    assert EbayOfferPreparation.objects.count() == 0
    legacy = MarketplaceListingConfiguration.objects.get(
        product=product, marketplace="otto", account="jv"
    )
    assert legacy.configuration["product_line"] == "Stuhl (BD)"
    assert not MarketplaceListingConfiguration.objects.filter(
        marketplace="ebay"
    ).exists()

    draft.title = "Manuell BD"
    draft.save()
    response = api_client.post(
        f"/api/v1/orchestrator/products/{product.pk}/ai-content/generations/{job.pk}/apply/",
        {"targets": [targets[1]], "overwrite": False},
        format="json",
    )
    assert response.status_code == 200
    draft.refresh_from_db()
    assert draft.title == "Manuell BD"
    assert "title" in response.data["skipped_fields"]["ebay:depotum"]


@pytest.mark.django_db
@pytest.mark.parametrize("environment", ["sandbox", "production"])
def test_manager_save_uses_card_facts_jv_images_set_dimensions_and_environment_settings(
    api_client, manager, product_factory, settings, environment
):
    settings.EBAY_ENVIRONMENT = environment
    settings.EBAY_DEPOTUM_LISTING_DEFAULTS = {
        "fulfillment_policy_id": "11",
        "payment_policy_id": "12",
        "return_policy_id": "13",
        "merchant_location_key": f"{environment}-warehouse",
    }
    product = product_factory(owner=manager, listing_price_eur_override="3899.00")
    ProductSetPart.objects.create(
        product=product,
        description="Sessel",
        width_cm=70,
        length_cm=80,
        height_cm=90,
        position=0,
    )
    schema = {
        "aspects": [
            {"localizedAspectName": name, "aspectConstraint": {"aspectRequired": True}}
            for name in ("Breite", "Länge", "Höhe", "Farbe")
        ]
    }
    urls = [
        "https://images.example/white.jpg",
        "https://images.example/interior.jpg",
        "https://images.example/human.jpg",
    ]
    api_client.force_authenticate(manager)
    with (
        patch("apps.marketplace.ebay.drafts._validate_listing_settings") as policies,
        patch(
            "apps.marketplace.ebay.drafts.get_category_aspects",
            return_value={
                **schema,
                "category_tree_id": "77",
                "category_tree_version": "1",
                "required_aspects": [],
            },
        ),
        patch(
            "apps.marketplace.ebay.catalog.get_category_aspects", return_value=schema
        ),
        patch(
            "apps.marketplace.ebay.manager.public_generated_listing_urls",
            return_value=urls,
        ) as images,
    ):
        response = api_client.put(
            f"/api/v1/ebay/drafts/depotum/{product.pk}/",
            {
                "sync_product": True,
                "title": "Sofagarnitur BD",
                "description": "Beschreibung",
                "category_id": "38208",
                "aspects": {"Farbe": ["Blau"]},
                "quantity": 999,
                "price_eur": "1.00",
            },
            format="json",
        )
    assert response.status_code == 201, response.data
    draft = EbayDraft.objects.get(pk=response.data["id"])
    assert draft.environment == environment
    assert draft.title == "Sofagarnitur BD"
    assert draft.quantity == 3
    assert str(draft.price_eur) == "3899.00"
    assert draft.image_urls == urls
    assert draft.aspects == {
        "Farbe": ["Blau"],
        "Breite": ["50 cm"],
        "Länge": ["55 cm"],
        "Höhe": ["90 cm"],
        "Maße Teil 2 (B × L × H)": ["70 × 80 × 90 cm"],
    }
    assert draft.merchant_location_key == f"{environment}-warehouse"
    assert draft.regulatory["manufacturer"]["companyName"] == "AEA GmbH & Co. KG"
    assert draft.regulatory["responsiblePersons"][0]["types"] == [
        "EU_RESPONSIBLE_PERSON"
    ]
    images.assert_called_once_with(product, account="jv")
    assert policies.call_args.kwargs["account"] == "depotum"
    assert "sync_product" not in response.data


@pytest.mark.django_db
def test_ai_apply_preserves_separate_ebay_title_when_common_text_is_edited(
    api_client, manager, product_factory
):
    product = product_factory(owner=manager)
    content = {
        "title": "Stuhl",
        "ebay_title": "Stuhl für eBay",
        "description": "Erster Absatz.\n\nZweiter Absatz.",
        "bullet_points": ["A", "B", "C"],
        "materials": [],
        "color": "Blau",
    }
    job = MarketplaceContentGeneration.objects.create(
        product=product,
        requested_by=manager,
        status="succeeded",
        targets=[{"marketplace": "ebay", "account": "depotum"}],
        result={"universal": {"content": content}},
    )
    api_client.force_authenticate(manager)
    response = api_client.patch(
        f"/api/v1/orchestrator/ai-content/generations/{job.pk}/",
        {key: value for key, value in content.items() if key != "ebay_title"},
        format="json",
    )
    assert response.status_code == 200, response.data
    assert (
        response.data["result"]["universal"]["content"]["ebay_title"]
        == content["ebay_title"]
    )


def test_catalog_labels_preserve_canonical_ebay_names_values_and_cached_data(
    monkeypatch,
):
    monkeypatch.setattr(
        "apps.marketplace.ebay.localization._labels",
        lambda language: {
            "Farbe": "Цвет",
            "Schwarz": "Чёрный",
            "Sofas & Sessel": "Диваны и кресла",
        },
    )
    original = {
        "category": {"id": "38208", "name": "Sofas & Sessel"},
        "aspects": [
            {
                "localizedAspectName": "Farbe",
                "aspectValues": [{"localizedValue": "Schwarz"}],
            }
        ],
    }
    before = deepcopy(original)
    localized = localize_catalog(original, "ru")
    assert localized["category"]["label"] == "Диваны и кресла"
    assert localized["aspects"][0]["label"] == "Цвет"
    assert localized["aspects"][0]["localizedAspectName"] == "Farbe"
    assert localized["aspects"][0]["aspectValues"][0] == {
        "localizedValue": "Schwarz",
        "label": "Чёрный",
    }
    assert original == before
    assert localize_catalog(original, "de") == original
    assert all(len(line.split("|")) == 4 for line in TERMS.strip().splitlines())
