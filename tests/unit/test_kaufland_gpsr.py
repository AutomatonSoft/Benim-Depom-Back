import pytest
from django.test import override_settings

from apps.marketplace.kaufland.gpsr import gpsr_payload_for_account
from apps.marketplace.kaufland.payload_builder import (
    KauflandPayloadValidationError,
    build_kaufland_create_payload,
    build_kaufland_update_payload,
)
from tests.unit.test_marketplace_colors import make_product

pytestmark = pytest.mark.unit


def test_gpsr_payload_loads_jv_and_xl_contacts():
    jv = gpsr_payload_for_account("jv")
    xl = gpsr_payload_for_account("xl")
    assert jv["manufacturer"] == ["AEA GmbH & Co. KG"]
    assert jv["product_safety_contact"]["email_address"] == "info@jvmoebel.de"
    assert jv["product_safety_contact"]["phone_number"] == "+49 7392 9378440"
    assert xl["manufacturer"] == ["XL MOEBEL GmbH"]
    assert "Germany" in xl["product_safety_contact"]["address"]


def test_kaufland_create_payload_includes_gpsr_fields():
    payload = build_kaufland_create_payload(
        product=make_product(),
        account="jv",
        configuration={
            "title": "Test chair",
            "description": "Detailed product description",
            "price": "299.00",
            "materials": ["Holz"],
            "color": "Weiß",
        },
    )
    assert payload["manufacturer"] == ["AEA GmbH & Co. KG"]
    assert payload["product_safety_contact"] == {
        "name": "AEA GmbH & Co. KG",
        "address": "Am Flugplatz 28, 88483 Burgrieden, Germany",
        "phone_number": "+49 7392 9378440",
        "email_address": "info@jvmoebel.de",
    }


def test_kaufland_update_payload_includes_gpsr_fields():
    payload = build_kaufland_update_payload(
        product=make_product(),
        account="jv",
        configuration={"title": "Updated chair"},
    )
    assert payload["manufacturer"] == ["AEA GmbH & Co. KG"]
    assert payload["product_safety_contact"]["name"] == "AEA GmbH & Co. KG"


def test_kaufland_create_rejects_missing_gpsr_file(tmp_path):
    missing = tmp_path / "missing-gpsr.json"
    with override_settings(KAUFLAND_GPSR_JSON_PATH=missing):
        try:
            build_kaufland_create_payload(
                product=make_product(),
                account="jv",
                configuration={
                    "title": "Test chair",
                    "description": "Detailed product description",
                    "price": "299.00",
                    "materials": ["Holz"],
                    "color": "Weiß",
                },
            )
        except KauflandPayloadValidationError as error:
            assert "product_safety_contact" in error.errors
        else:
            raise AssertionError("Expected GPSR validation error")
