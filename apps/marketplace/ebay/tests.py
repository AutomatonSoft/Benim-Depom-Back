import json
import os
from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.core.cache import cache
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

from apps.marketplace.ebay import catalog
from apps.marketplace.ebay.catalog_views import (
    EbayCategoriesView,
    EbayCategoryAspectsView,
)
from apps.marketplace.ebay.client import EbayAPIError, EbayConfigurationError
from apps.marketplace.ebay.models import EbayDraft
from apps.marketplace.ebay.readiness import _policy_summary


@pytest.fixture
def catalog_api(settings, monkeypatch):
    """Изолирует существующий источник API от локальных файлов, сети и базы данных."""
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }
    settings.EBAY_ENVIRONMENT = "sandbox"
    settings.EBAY_CLIENT_ID = "catalog-test-client"
    monkeypatch.setattr(catalog, "_local_payload", lambda relative_path: None)
    cache.clear()
    tree = {
        "categoryTreeId": "77",
        "categoryTreeVersion": "1",
        "rootCategoryNode": {
            "childCategoryTreeNodes": [
                {
                    "category": {"categoryId": "10", "categoryName": "Möbel"},
                    "childCategoryTreeNodes": [
                        {
                            "category": {"categoryId": "11", "categoryName": "Sessel"},
                            "leafCategoryTreeNode": True,
                        }
                    ],
                }
            ]
        },
    }
    aspects = [
        {
            "localizedAspectName": "Marke",
            "aspectConstraint": {"aspectRequired": True, "aspectMode": "FREE_TEXT"},
            "aspectValues": [{"localizedValue": "Beispiel"}],
        },
        {"localizedAspectName": "Farbe", "aspectConstraint": {"aspectRequired": False}},
    ]

    def read_resource(**kwargs):
        """Выдаёт вымышленные ответы только для разрешённых маршрутов Taxonomy."""
        path = kwargs["path"]
        if path.endswith("get_default_category_tree_id"):
            return {"categoryTreeId": "77"}
        if path.endswith("/category_tree/77"):
            return deepcopy(tree)
        if path.endswith("get_item_aspects_for_category"):
            assert kwargs["params"] == {"category_id": "11"}
            return {"aspects": deepcopy(aspects)}
        raise AssertionError(f"Unexpected eBay path: {path}")

    resource = MagicMock(side_effect=read_resource)
    token = MagicMock(
        return_value={"access_token": "application-test", "expires_in": 7200}
    )
    monkeypatch.setattr(catalog, "token_request", token)
    monkeypatch.setattr(catalog, "get_seller_resource", resource)
    monkeypatch.setattr(catalog, "build_marketplace_session", MagicMock())
    yield SimpleNamespace(resource=resource, token=token, tree=tree, aspects=aspects)
    cache.clear()


def test_catalog_navigation_aspects_and_cache(catalog_api):
    """Проверяет уровни дерева, обязательные поля, исходные ограничения и повторное чтение."""
    root = catalog.get_categories()
    assert root["parent"] is None
    assert root["categories"] == [{"id": "10", "name": "Möbel", "is_leaf": False}]
    assert catalog.get_categories(parent_id="10")["categories"][0]["id"] == "11"
    result = catalog.get_category_aspects(category_id="11")
    assert result["required_aspects"] == ["Marke"]
    assert result["aspects"] == catalog_api.aspects
    assert catalog.get_category_aspects(category_id="11") == result
    assert catalog_api.resource.call_count == 3
    catalog_api.token.assert_called_once_with(
        {
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope",
        }
    )


def test_catalog_cache_is_separate_for_environment_and_client(catalog_api, settings):
    """Не позволяет использовать дерево и токен другого окружения или приложения."""
    catalog.get_categories()
    settings.EBAY_ENVIRONMENT = "production"
    catalog.get_categories()
    settings.EBAY_CLIENT_ID = "another-client"
    catalog.get_categories()
    assert catalog_api.token.call_count == 3
    assert catalog_api.resource.call_count == 6


def test_catalog_rejects_unknown_and_non_leaf_categories(catalog_api):
    """Отклоняет неизвестную категорию и запрос характеристик родительского узла."""
    with pytest.raises(NotFound):
        catalog.get_categories(parent_id="999")
    with pytest.raises(ValidationError):
        catalog.get_category_aspects(category_id="10")
    assert catalog_api.resource.call_count == 2


def test_catalog_does_not_cache_invalid_aspects(catalog_api):
    """Повреждённый ответ eBay не становится успешным пустым справочником в кеше."""
    catalog.get_categories()
    catalog_api.resource.side_effect = None
    catalog_api.resource.return_value = {"aspects": [{"localizedAspectName": "Marke"}]}
    with pytest.raises(EbayAPIError):
        catalog.get_category_aspects(category_id="11")
    catalog_api.resource.return_value = {"aspects": catalog_api.aspects}
    assert catalog.get_category_aspects(category_id="11")["required_aspects"] == [
        "Marke"
    ]


@pytest.mark.parametrize("role", [None, "seller"])
def test_catalog_requires_manager_without_contacting_ebay(catalog_api, role):
    """Проверяет закрытый доступ обоих маршрутов до вызова внешнего API."""
    for view, kwargs in (
        (EbayCategoriesView, {}),
        (EbayCategoryAspectsView, {"category_id": "11"}),
    ):
        request = APIRequestFactory().get("/")
        if role:
            force_authenticate(
                request,
                user=SimpleNamespace(
                    is_authenticated=True, is_superuser=False, role=role
                ),
            )
        assert view.as_view()(request, **kwargs).status_code in {401, 403}
    catalog_api.resource.assert_not_called()


def test_catalog_manager_input_validation(catalog_api):
    """Проверяет доступ менеджера и отсекает неверный идентификатор до чтения eBay."""
    user = SimpleNamespace(is_authenticated=True, is_superuser=False, role="manager")
    factory = APIRequestFactory()
    request = factory.get("/", {"parent_id": "../bad"})
    force_authenticate(request, user=user)
    assert EbayCategoriesView.as_view()(request).status_code == 400
    catalog_api.resource.assert_not_called()
    request = factory.get("/")
    force_authenticate(request, user=user)
    assert EbayCategoriesView.as_view()(request).status_code == 200


def test_readiness_accepts_ebay_non_motor_policy():
    policy = {
        "fulfillmentPolicyId": "example",
        "marketplaceId": "EBAY_DE",
        "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES"}],
    }
    assert _policy_summary(policy, "fulfillmentPolicyId")["applicable_to_furniture"]


@pytest.fixture
def local_catalog(settings, tmp_path, monkeypatch):
    """Создаёт маленький файловый каталог и запрещает обращения к eBay."""
    settings.BASE_DIR = tmp_path
    settings.EBAY_ENVIRONMENT = "sandbox"
    settings.EBAY_CLIENT_ID = ""
    settings.EBAY_CLIENT_SECRET = ""
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }
    cache.clear()
    network = MagicMock(side_effect=AssertionError("Unexpected eBay request"))
    monkeypatch.setattr(catalog, "_read_taxonomy", network)
    monkeypatch.setattr(catalog, "token_request", network)
    base = tmp_path / "data" / "ebay" / "sandbox"
    (base / "aspects").mkdir(parents=True)
    metadata = {
        "environment": "sandbox",
        "marketplace_id": "EBAY_DE",
        "category_tree_id": "77",
        "category_tree_version": "135",
    }
    category = {"id": "38208", "name": "Sofas & Sessel", "is_leaf": True}
    categories = {
        **metadata,
        "rootCategoryNode": {
            "category": {"categoryId": "3197", "categoryName": "Möbel"},
            "childCategoryTreeNodes": [
                {
                    "category": {
                        "categoryId": "38208",
                        "categoryName": category["name"],
                    },
                    "leafCategoryTreeNode": True,
                }
            ],
        },
    }
    aspects = {
        **metadata,
        "category": category,
        "aspects": [
            {
                "localizedAspectName": "Farbe",
                "aspectConstraint": {"aspectRequired": True, "aspectMode": "FREE_TEXT"},
                "aspectValues": [{"localizedValue": "Grau"}],
            }
        ],
    }
    (base / "categories.json").write_text(json.dumps(categories), encoding="utf-8")
    aspect_path = base / "aspects" / "38208.json"
    aspect_path.write_text(json.dumps(aspects), encoding="utf-8")
    yield SimpleNamespace(base=base, aspects=aspects, path=aspect_path, network=network)
    cache.clear()


def test_local_catalog_navigation_and_aspects_without_credentials(local_catalog):
    """Сохраняет формат ответов и читает только мебель без токенов и внешнего API."""
    assert catalog.get_categories()["categories"] == [
        {"id": "3197", "name": "Möbel", "is_leaf": False}
    ]
    assert catalog.get_categories(parent_id="3197")["categories"][0]["id"] == "38208"
    assert catalog.get_categories(parent_id="38208")["categories"] == []
    result = catalog.get_category_aspects(category_id="38208")
    assert result["aspects"] == local_catalog.aspects["aspects"]
    assert result["required_aspects"] == ["Farbe"]
    with pytest.raises(NotFound):
        catalog.get_categories(parent_id="11700")
    with pytest.raises(ValidationError):
        catalog.get_category_aspects(category_id="3197")
    local_catalog.network.assert_not_called()


@pytest.mark.parametrize(
    "failure", ["missing", "broken_json", "wrong_environment", "wrong_version"]
)
def test_local_catalog_rejects_invalid_files_without_api_fallback(
    local_catalog, failure
):
    """Не заменяет отсутствующие или несовместимые локальные данные запросом к API."""
    if failure == "missing":
        local_catalog.path.unlink()
    elif failure == "broken_json":
        local_catalog.path.write_text("{", encoding="utf-8")
    else:
        field = (
            "environment" if failure == "wrong_environment" else "category_tree_version"
        )
        local_catalog.aspects[field] = (
            "production" if failure == "wrong_environment" else "136"
        )
        local_catalog.path.write_text(
            json.dumps(local_catalog.aspects), encoding="utf-8"
        )
    with pytest.raises(EbayConfigurationError):
        catalog.get_category_aspects(category_id="38208")
    local_catalog.network.assert_not_called()


def test_local_catalog_refreshes_after_file_replacement(local_catalog):
    """Обновление JSON не ждёт истечения суточного кеша."""
    catalog.get_category_aspects(category_id="38208")
    previous = local_catalog.path.stat()
    local_catalog.aspects["aspects"][0]["aspectValues"][0]["localizedValue"] = "Blau"
    local_catalog.path.write_text(json.dumps(local_catalog.aspects), encoding="utf-8")
    os.utime(
        local_catalog.path,
        ns=(previous.st_atime_ns, previous.st_mtime_ns + 1_000_000_000),
    )
    result = catalog.get_category_aspects(category_id="38208")
    assert result["aspects"][0]["aspectValues"][0]["localizedValue"] == "Blau"


def test_production_does_not_read_sandbox_files(local_catalog, settings):
    """Production сохраняет свой источник и не использует снимок Sandbox."""
    settings.EBAY_ENVIRONMENT = "production"
    assert catalog._local_payload("categories.json") is None
    with pytest.raises(AssertionError, match="Unexpected eBay request"):
        catalog.get_categories()


@pytest.fixture
def draft_api(db, local_catalog):
    """Создаёт отдельный товар и менеджера для API черновиков без внешней сети."""
    from django.contrib.auth import get_user_model

    from apps.products.models import Product

    user_model = get_user_model()
    manager = user_model.objects.create_user(
        username="ebay-draft-manager", email="draft-manager@example.com", role="manager"
    )
    seller = user_model.objects.create_user(
        username="ebay-draft-seller", email="draft-seller@example.com", role="seller"
    )
    product = Product.objects.create(
        owner=seller,
        title="Original sofa",
        product_type="Sofa",
        unit_price="100.00",
        currency="EUR",
    )
    client = APIClient()
    client.force_authenticate(manager)
    return SimpleNamespace(
        client=client,
        product=product,
        seller=seller,
        url=f"/api/v1/ebay/drafts/depotum/{product.pk}/",
    )


def test_draft_create_edit_and_read_do_not_change_product(draft_api, local_catalog):
    """Черновик остаётся отдельной записью; чтение не создаёт данные, товар не меняется."""
    from apps.products.models import Product

    api = draft_api
    before = Product.objects.filter(pk=api.product.pk).values().get()
    assert api.client.get(api.url).status_code == 404
    assert (
        api.client.patch(api.url, {"title": "Draft"}, format="json").status_code == 404
    )
    assert not EbayDraft.objects.exists()
    response = api.client.put(api.url, {}, format="json")
    assert response.status_code == 201
    assert "title" in response.data["missing_requirements"]["fields"]
    data = {
        "title": "Graues Sofa",
        "description": "Neues Sofa mit Stoffbezug.",
        "category_id": "38208",
        "aspects": {"Farbe": ["Grau"]},
        "image_urls": ["https://example.com/sofa.jpg"],
        "price_eur": "2099.00",
        "quantity": 0,
    }
    response = api.client.patch(api.url, data, format="json")
    assert response.status_code == 200
    assert response.data["missing_requirements"] == {
        "fields": [
            "fulfillment_policy_id",
            "payment_policy_id",
            "return_policy_id",
            "merchant_location_key",
        ],
        "aspects": [],
    }
    assert response.data["account"] == "depotum"
    assert response.data["currency"] == "EUR"
    assert response.data["marketplace_id"] == "EBAY_DE"
    assert response.data["category_tree_version"] == "135"
    assert api.client.get(api.url).data["aspects"] == data["aspects"]
    assert (
        api.client.patch(api.url, {"category_id": ""}, format="json").status_code == 400
    )
    assert api.client.get(api.url).data["category_id"] == "38208"
    assert EbayDraft.objects.count() == 1
    assert Product.objects.filter(pk=api.product.pk).values().get() == before
    local_catalog.network.assert_not_called()


@pytest.mark.parametrize(
    "data",
    [
        {"title": "x" * 81},
        {"quantity": -1},
        {"price_eur": "0.00"},
        {"category_id": "3197"},
        {"category_id": "../38208"},
        {"aspects": {"Farbe": ["Grau"]}},
        {"category_id": "38208", "aspects": {"Unknown": ["value"]}},
        {"category_id": "38208", "aspects": {"Farbe": ["Grau", "Blau"]}},
        {"image_urls": ["http://example.com/image.jpg"]},
    ],
)
def test_invalid_draft_does_not_create_records(draft_api, data):
    """Неверные данные отклоняются до сохранения даже неполного черновика."""
    response = draft_api.client.put(draft_api.url, data, format="json")
    assert response.status_code == 400
    assert not EbayDraft.objects.exists()


def test_draft_reports_missing_aspects_and_edits_without_catalog(
    draft_api, local_catalog
):
    """Пропущенные обязательные характеристики не мешают сохранить промежуточную работу."""
    response = draft_api.client.put(
        draft_api.url, {"category_id": "38208"}, format="json"
    )
    assert response.status_code == 201
    assert response.data["missing_requirements"]["aspects"] == ["Farbe"]
    local_catalog.path.unlink()
    response = draft_api.client.patch(
        draft_api.url, {"title": "Draft sofa"}, format="json"
    )
    assert response.status_code == 200
    assert draft_api.client.get(draft_api.url).status_code == 200


def test_draft_environment_isolation_and_manager_access(draft_api, settings):
    """Sandbox не виден в Production; продавец и анонимный пользователь не имеют доступа."""
    api = draft_api
    assert api.client.put(api.url, {}, format="json").status_code == 201
    settings.EBAY_ENVIRONMENT = "production"
    assert api.client.get(api.url).status_code == 404
    assert (
        api.client.put(
            api.url, {"title": "Production draft"}, format="json"
        ).status_code
        == 201
    )
    assert EbayDraft.objects.count() == 2
    api.client.force_authenticate(api.seller)
    for method in (api.client.get, api.client.put, api.client.patch):
        assert method(api.url).status_code == 403
    api.client.force_authenticate(None)
    assert api.client.get(api.url).status_code in {401, 403}
    assert EbayDraft.objects.count() == 2


def test_draft_selection_only_and_length_constraints():
    """Проверяет списочные значения и длину строк по правилам выбранной категории."""
    from apps.marketplace.ebay.drafts import _validate_aspects

    schema = {
        "aspects": [
            {
                "localizedAspectName": "Montage erforderlich",
                "aspectConstraint": {
                    "aspectMode": "SELECTION_ONLY",
                    "itemToAspectCardinality": "SINGLE",
                    "aspectMaxLength": 4,
                },
                "aspectValues": [{"localizedValue": "Ja"}, {"localizedValue": "Nein"}],
            }
        ]
    }
    _validate_aspects({"Montage erforderlich": ["Ja"]}, schema)
    for values in (["Maybe"], ["No"], ["Ja", "Nein"]):
        with pytest.raises(ValidationError):
            _validate_aspects({"Montage erforderlich": values}, schema)


@pytest.fixture
def listing_settings_api(monkeypatch):
    """Имитирует чтение политик и склада текущего JV, не обращаясь к eBay."""
    from apps.marketplace.ebay import drafts

    values = {
        "fulfillment_policy_id": "201",
        "payment_policy_id": "202",
        "return_policy_id": "203",
        "merchant_location_key": "DE_88483",
    }
    payloads = {
        "/sell/inventory/v1/location/DE_88483": {
            "merchantLocationKey": "DE_88483",
            "merchantLocationStatus": "ENABLED",
            "locationTypes": ["WAREHOUSE"],
            "location": {"address": {"postalCode": "88483", "country": "DE"}},
        }
    }
    for field, (resource, id_field) in drafts.POLICY_FIELDS.items():
        payloads[f"/sell/account/v1/{resource}_policy/{values[field]}"] = {
            id_field: values[field],
            "marketplaceId": "EBAY_DE",
            "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES"}],
        }

    def read_resource(**kwargs):
        """Возвращает только разрешённые фиктивные ресурсы для проверяемого токена."""
        assert kwargs["access_token"] == "listing-settings-token"
        return deepcopy(payloads[kwargs["path"]])

    resource = MagicMock(side_effect=read_resource)
    token = MagicMock(return_value="listing-settings-token")
    monkeypatch.setattr(drafts, "get_access_token", token)
    monkeypatch.setattr(drafts, "get_seller_resource", resource)
    monkeypatch.setattr(drafts, "build_marketplace_session", MagicMock())
    return SimpleNamespace(
        values=values, payloads=payloads, resource=resource, token=token
    )


def test_draft_attaches_listing_settings_without_changing_product(
    draft_api, listing_settings_api
):
    """Ссылки проверяются у JV и сохраняются только в черновике; чтение не вызывает eBay."""
    from apps.products.models import Product

    before = Product.objects.filter(pk=draft_api.product.pk).values().get()
    response = draft_api.client.put(
        draft_api.url, listing_settings_api.values, format="json"
    )
    assert response.status_code == 201
    for field, value in listing_settings_api.values.items():
        assert response.data[field] == value
        assert field not in response.data["missing_requirements"]["fields"]
    listing_settings_api.token.assert_called_once_with(account="depotum")
    assert listing_settings_api.resource.call_count == 4
    assert draft_api.client.get(draft_api.url).status_code == 200
    assert (
        draft_api.client.patch(
            draft_api.url, {"title": "New draft title"}, format="json"
        ).status_code
        == 200
    )
    assert (
        draft_api.client.patch(
            draft_api.url, listing_settings_api.values, format="json"
        ).status_code
        == 200
    )
    assert listing_settings_api.resource.call_count == 4
    assert Product.objects.filter(pk=draft_api.product.pk).values().get() == before


@pytest.mark.parametrize(
    "failure",
    [
        "marketplace",
        "motors",
        "disabled_location",
        "not_warehouse",
        "address",
        "policy_id",
    ],
)
def test_invalid_listing_settings_do_not_change_draft(
    draft_api, listing_settings_api, failure
):
    """Чужая площадка, неверная политика и непригодный склад не заменяют сохранённые ссылки."""
    values = listing_settings_api.values
    response = draft_api.client.put(draft_api.url, {}, format="json")
    assert response.status_code == 201
    policy = listing_settings_api.payloads["/sell/account/v1/fulfillment_policy/201"]
    location = listing_settings_api.payloads["/sell/inventory/v1/location/DE_88483"]
    if failure == "marketplace":
        policy["marketplaceId"] = "EBAY_US"
    elif failure == "motors":
        policy["categoryTypes"] = [{"name": "MOTORS_VEHICLES"}]
    elif failure == "disabled_location":
        location["merchantLocationStatus"] = "DISABLED"
    elif failure == "not_warehouse":
        location["locationTypes"] = ["STORE"]
    elif failure == "address":
        location["location"]["address"] = {"country": "DE"}
    else:
        policy["fulfillmentPolicyId"] = "999"
    response = draft_api.client.patch(draft_api.url, values, format="json")
    assert response.status_code == 400
    draft = EbayDraft.objects.get()
    assert all(getattr(draft, field) == "" for field in values)


def test_listing_settings_api_error_keeps_existing_draft(
    draft_api, listing_settings_api
):
    """Ошибка внешнего API не превращается в сохранение непроверенных ссылок."""
    draft_api.client.put(draft_api.url, {}, format="json")
    listing_settings_api.resource.side_effect = EbayAPIError("eBay unavailable")
    response = draft_api.client.patch(
        draft_api.url, listing_settings_api.values, format="json"
    )
    assert response.status_code == 502
    assert EbayDraft.objects.get().fulfillment_policy_id == ""


def test_listing_settings_are_separate_between_environments(
    draft_api, listing_settings_api, settings
):
    """Production-ссылки не копируются в уже существующий Sandbox-черновик."""
    draft_api.client.put(draft_api.url, {}, format="json")
    settings.EBAY_ENVIRONMENT = "production"
    assert (
        draft_api.client.put(
            draft_api.url, listing_settings_api.values, format="json"
        ).status_code
        == 201
    )
    settings.EBAY_ENVIRONMENT = "sandbox"
    assert draft_api.client.get(draft_api.url).data["merchant_location_key"] == ""
    assert (
        EbayDraft.objects.get(environment="production").merchant_location_key
        == "DE_88483"
    )


def test_draft_can_clear_listing_settings_without_api(draft_api, listing_settings_api):
    """Явная очистка ссылок сохраняет неполный черновик и не требует токена eBay."""
    draft_api.client.put(draft_api.url, listing_settings_api.values, format="json")
    response = draft_api.client.patch(
        draft_api.url,
        {field: "" for field in listing_settings_api.values},
        format="json",
    )
    assert response.status_code == 200
    assert response.data["merchant_location_key"] == ""
    assert listing_settings_api.resource.call_count == 4


@pytest.fixture
def preview_draft(settings):
    """Даёт заполненный несохранённый черновик для проверки JSON без базы и сети."""
    settings.EBAY_ENVIRONMENT = "sandbox"
    from apps.products.models import Product

    return EbayDraft(
        product=Product(id=69, ean_jv="4071489789867"),
        environment="sandbox",
        account="depotum",
        title="Schwarzes Sofa",
        description="Sofa mit Lederbezug.",
        image_urls=[
            "https://example.com/white.jpg",
            "https://example.com/interior.jpg",
        ],
        price_eur=Decimal("7049.00"),
        quantity=1,
        category_id="38208",
        aspects={"Farbe": ["Schwarz"]},
        required_aspects=["Farbe"],
        category_tree_id="77",
        category_tree_version="135",
        fulfillment_policy_id="201",
        payment_policy_id="202",
        return_policy_id="203",
        merchant_location_key="DE_88483",
    )


def test_preview_builds_inventory_item_and_offer(preview_draft):
    """Проверяет NEW, цену, фото, характеристики и совпадение SKU с существующим JV EAN."""
    from apps.marketplace.ebay.description import render_listing_description
    from apps.marketplace.ebay.preview import build_draft_preview

    result = build_draft_preview(preview_draft)
    assert result["preview_only"] is True
    assert result["draft_complete"] is True
    assert result["missing_requirements"] == {"fields": [], "aspects": []}
    assert result["sku"] == result["ean"] == "4071489789867"
    inventory = result["inventory_item"]
    assert inventory["method"] == "PUT"
    assert (
        inventory["url"]
        == "https://api.sandbox.ebay.com/sell/inventory/v1/inventory_item/4071489789867"
    )
    assert inventory["headers"] == {
        "Content-Type": "application/json",
        "Content-Language": "de-DE",
    }
    assert inventory["body"] == {
        "condition": "NEW",
        "availability": {"shipToLocationAvailability": {"quantity": 1}},
        "product": {
            "title": preview_draft.title,
            "description": preview_draft.description,
            "imageUrls": preview_draft.image_urls,
            "aspects": preview_draft.aspects,
            "ean": ["4071489789867"],
        },
    }
    assert result["offer"]["method"] == "POST"
    assert result["offer"]["body"] == {
        "sku": "4071489789867",
        "marketplaceId": "EBAY_DE",
        "format": "FIXED_PRICE",
        "listingDuration": "GTC",
        "includeCatalogProductDetails": False,
        "tax": {"vatPercentage": 19.0},
        "categoryId": "38208",
        "merchantLocationKey": "DE_88483",
        "listingDescription": render_listing_description(
            preview_draft, ean="4071489789867"
        ),
        "availableQuantity": 1,
        "pricingSummary": {"price": {"value": "7049.00", "currency": "EUR"}},
        "listingPolicies": {
            "fulfillmentPolicyId": "201",
            "paymentPolicyId": "202",
            "returnPolicyId": "203",
        },
    }
    assert "sku_and_ean_strategy" not in result["checks_not_performed"]
    assert "gpsr_and_other_regulatory_requirements" in result["checks_not_performed"]
    assert "Authorization" not in inventory["headers"]
    json.dumps(result)


def test_offer_default_vat_does_not_increase_price(preview_draft):
    """Ставка передаётся отдельно; цена черновика остаётся окончательной ценой предложения."""
    from apps.marketplace.ebay.preview import build_draft_preview

    before_price = preview_draft.price_eur
    offer = build_draft_preview(preview_draft)["offer"]["body"]
    assert offer["tax"] == {"vatPercentage": 19.0}
    assert offer["pricingSummary"]["price"] == {"value": "7049.00", "currency": "EUR"}
    assert preview_draft.price_eur == before_price


def test_offer_update_applies_default_vat_and_preserves_other_tax_fields(preview_draft):
    """Обновление задаёт 19%, сохраняя прочие поля tax и исходную цену."""
    from apps.marketplace.ebay.preview import build_draft_preview
    from apps.marketplace.ebay.updating import _update_bodies

    preview = build_draft_preview(preview_draft)
    inventory = deepcopy(preview["inventory_item"]["body"])
    offer = deepcopy(preview["offer"]["body"])
    offer["tax"] = {"vatPercentage": 7.0, "applyTax": False}
    _, body = _update_bodies(inventory, offer, preview)
    assert body["tax"] == {"vatPercentage": 19.0, "applyTax": False}
    assert body["pricingSummary"]["price"]["value"] == "7049.00"
    assert offer["tax"]["vatPercentage"] == 7.0


def test_listing_html_gallery_and_plain_inventory_description(preview_draft):
    """HTML предназначен только Offer; галерея и вкладки используют CSS без скриптов."""
    from apps.marketplace.ebay.preview import build_draft_preview

    result = build_draft_preview(preview_draft)
    html = result["offer"]["body"]["listingDescription"]
    assert (
        result["inventory_item"]["body"]["product"]["description"]
        == preview_draft.description
    )
    assert 'href="#dep-img-2"' in html
    assert "#dep-img-2:target~.dep-main .dep-slide-2{display:block}" in html
    assert 'src="https://example.com/interior.jpg"' in html
    assert 'id="dep-details"' in html
    assert 'id="dep-colors"' in html
    assert "4071489789867" in html
    assert "Schwarz" in html
    assert "@media(max-width:700px)" in html
    assert "<script" not in html
    assert "<iframe" not in html
    assert "<link " not in html
    assert "varvara.de" not in html
    assert "15%" not in html
    assert "6-10" not in html


def test_listing_html_escapes_content_without_changing_draft(preview_draft):
    """Данные товара не могут внедрить HTML, обработчики или CSS в шаблон."""
    from apps.marketplace.ebay.description import render_listing_description

    preview_draft.title = '<img src=x onerror="alert(1)">'
    preview_draft.description = "<script>alert(1)</script>\nSecond line & text"
    preview_draft.aspects = {"<style>": ["<iframe>"], "Farbe": ["<b>Rot</b>"]}
    preview_draft.image_urls = ['https://example.com/image.jpg?x=" onerror="alert(1)']
    before = {
        field.attname: deepcopy(getattr(preview_draft, field.attname))
        for field in preview_draft._meta.concrete_fields
    }
    html = render_listing_description(preview_draft, ean="4071489789867")
    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;<br>Second line &amp; text" in html
    assert "&lt;iframe&gt;" in html
    assert "&lt;b&gt;Rot&lt;/b&gt;" in html
    assert 'src="https://example.com/image.jpg?x=&quot; onerror=&quot;alert(1)"' in html
    assert {
        field.attname: getattr(preview_draft, field.attname)
        for field in preview_draft._meta.concrete_fields
    } == before


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "http://example.com/a.jpg",
        "https://user:pass@example.com/a.jpg",
        "https://example.com:bad/a.jpg",
    ],
)
def test_listing_html_refuses_unsafe_image_urls(preview_draft, url):
    from apps.marketplace.ebay.description import render_listing_description

    preview_draft.image_urls = [url]
    with pytest.raises(ValidationError):
        render_listing_description(preview_draft, ean="4071489789867")


@pytest.mark.parametrize("image_count", [0, 1, 3])
def test_listing_html_handles_different_gallery_sizes(preview_draft, image_count):
    from apps.marketplace.ebay.description import render_listing_description

    preview_draft.image_urls = [
        f"https://example.com/{i}.jpg" for i in range(image_count)
    ]
    preview_draft.aspects = {}
    html = render_listing_description(preview_draft, ean="4071489789867")
    assert html.count('class="dep-thumb"') == image_count
    assert 'id="dep-colors"' in html
    assert ('<div class="dep-gallery">' in html) is bool(image_count)


def test_listing_html_preserves_reference_banners_and_detail_blocks(preview_draft):
    """Фирменные баннеры и пять вкладок сохраняются; характеристики выводятся без таблицы."""
    from apps.marketplace.ebay.description import render_listing_description

    preview_draft.aspects.update(
        {"Breite": ["90 cm"], "Höhe": ["85 cm"], "Polsterstoff": ["100% Leder"]}
    )
    html = render_listing_description(preview_draft, ean="4071489789867")
    for banner in (
        "webtag1_jv_10122.png",
        "webtag2_jv_women21321.png",
        "webtag3_jv_3330.png",
    ):
        assert html.count(f"https://productron.de/AfterbuyWebTag/{banner}") == 1
    for caption in ("Produktdetails", "Farben", "Lieferzeiten", "Zahlung", "Optional"):
        assert f">{caption}</label>" in html
    assert "<strong>Artikel-Nr.</strong> EAN: 4071489789867" in html
    assert "<strong>Maße:</strong>" in html
    assert "Breite: 90 cm" in html
    assert "<strong>Farbe:</strong> Schwarz" in html
    assert "<strong>Polsterstoff:</strong> 100% Leder" in html
    assert "<table" not in html
    assert "Weitere Informationen" not in html
    assert html.index('class="dep-company-header"') < html.index('class="dep-layout"')
    assert html.index('class="dep-layout"') < html.index('class="dep-company-footer"')


def test_preview_does_not_replace_unknown_price_or_quantity_with_zero(preview_draft):
    """Незаполненный черновик остаётся неполным; неизвестный остаток не превращается в ноль."""
    from apps.marketplace.ebay.preview import build_draft_preview

    preview_draft.quantity = None
    preview_draft.price_eur = None
    preview_draft.aspects = {}
    result = build_draft_preview(preview_draft)
    assert result["draft_complete"] is False
    assert result["missing_requirements"]["aspects"] == ["Farbe"]
    assert "quantity" in result["missing_requirements"]["fields"]
    assert "price_eur" in result["missing_requirements"]["fields"]
    assert "availability" not in result["inventory_item"]["body"]
    assert "availableQuantity" not in result["offer"]["body"]
    assert "pricingSummary" not in result["offer"]["body"]
    preview_draft.quantity = 0
    result = build_draft_preview(preview_draft)
    assert result["offer"]["body"]["availableQuantity"] == 0
    assert "quantity" not in result["missing_requirements"]["fields"]


@pytest.mark.parametrize(
    "ean", ["", "4071489789868", "４０７１４８９７８９８６７", "../12345678"]
)
def test_preview_rejects_missing_or_invalid_ean_without_allocating(preview_draft, ean):
    """Невалидный EAN не превращается в SKU и не заменяется выделенным номером."""
    from apps.marketplace.ebay.preview import build_draft_preview

    preview_draft.product.ean_jv = ean
    result = build_draft_preview(preview_draft)
    assert result["sku"] == ""
    assert result["inventory_item"]["url"] is None
    assert "ean_jv" in result["missing_requirements"]["fields"]
    assert "ean" not in result["inventory_item"]["body"]["product"]
    assert preview_draft.product.ean_jv == ean


@pytest.fixture
def regulatory_contact():
    return {
        "companyName": "Example Manufacturer GmbH",
        "addressLine1": "Example Street 1",
        "city": "Berlin",
        "postalCode": "10115",
        "country": "DE",
        "email": "contact@example.com",
    }


def test_gpsr_responsible_person_does_not_become_manufacturer(regulatory_contact):
    """Ответственный сохраняется отдельно; производителя не выводим из его контактов."""
    from apps.marketplace.ebay.regulatory import (
        EbayRegulatorySerializer,
        missing_regulatory_fields,
    )

    serializer = EbayRegulatorySerializer(
        data={"responsiblePersons": [regulatory_contact]}
    )
    assert serializer.is_valid(), serializer.errors
    assert "manufacturer" not in serializer.validated_data
    assert serializer.validated_data["responsiblePersons"][0]["types"] == [
        "EU_RESPONSIBLE_PERSON"
    ]
    assert missing_regulatory_fields(serializer.validated_data) == [
        "regulatory.manufacturer"
    ]


def test_gpsr_patch_validates_complete_contact(draft_api):
    """PATCH не позволяет обойти обязательный адрес вложенного контакта."""
    draft_api.client.put(draft_api.url, {}, format="json")
    response = draft_api.client.patch(
        draft_api.url,
        {"regulatory": {"manufacturer": {"companyName": "Incomplete"}}},
        format="json",
    )
    assert response.status_code == 400
    assert EbayDraft.objects.get().regulatory == {}


@pytest.fixture
def preparation_api(draft_api, preview_draft, regulatory_contact, monkeypatch):
    """Изолирует весь сетевой путь подготовки; никакой тест не обращается к eBay."""
    from apps.marketplace.ebay import preparation

    draft_api.product.ean_jv = "4071489789867"
    draft_api.product.save(update_fields=["ean_jv"])
    preview_draft.product = draft_api.product
    preview_draft.regulatory = {"manufacturer": regulatory_contact}
    preview_draft.save()
    monkeypatch.setattr(preparation, "_check_local", MagicMock())
    monkeypatch.setattr(preparation, "_validate_listing_settings", MagicMock())
    monkeypatch.setattr(
        preparation, "get_access_token", lambda **kwargs: "secret-token"
    )
    monkeypatch.setattr(
        preparation,
        "get_seller_resource",
        lambda **kwargs: {
            "itemConditionPolicies": [
                {"categoryId": "38208", "itemConditions": [{"conditionId": "1000"}]}
            ]
        },
    )
    session = MagicMock()
    session.adapters = {}
    context = MagicMock()
    context.__enter__.return_value = session
    monkeypatch.setattr(preparation, "build_marketplace_session", lambda: context)

    def response(status, payload=None):
        result = MagicMock(status_code=status)
        result.json.return_value = payload or {}
        return result

    session.request.side_effect = [
        response(404),
        response(404),
        response(204),
        response(201, {"offerId": "9001"}),
    ]
    return SimpleNamespace(
        client=draft_api.client,
        url=draft_api.url + "prepare/",
        session=session,
        draft=preview_draft,
        response=response,
    )


def test_prepare_creates_unpublished_offer_and_repeat_is_noop(preparation_api):
    """Повтор возвращает offerId без новых запросов; publish и delete не вызываются."""
    from apps.marketplace.ebay.models import EbayOfferPreparation

    api = preparation_api
    response = api.client.post(api.url, {"confirm_stock_location": True}, format="json")
    assert response.status_code == 200, response.data
    assert response.data["offer_id"] == "9001"
    assert response.data["status"] == "prepared"
    assert response.data["publication_performed"] is False
    assert EbayOfferPreparation.objects.get().inventory_confirmed is True
    assert [call.args[0] for call in api.session.request.call_args_list] == [
        "GET",
        "GET",
        "PUT",
        "POST",
    ]
    assert all(
        "publish" not in call.args[1] for call in api.session.request.call_args_list
    )
    assert "secret-token" not in json.dumps(response.data)
    assert (
        api.client.post(
            api.url, {"confirm_stock_location": True}, format="json"
        ).status_code
        == 200
    )
    assert api.session.request.call_count == 4
    assert api.client.get(api.url).data["offer_id"] == "9001"


def test_prepare_refuses_existing_external_offer(preparation_api):
    """Наличие Offer на любом marketplace запрещает изменение общего Inventory Item."""
    api = preparation_api
    api.session.request.side_effect = [
        api.response(
            200,
            {
                "offers": [
                    {
                        "offerId": "external",
                        "status": "PUBLISHED",
                        "marketplaceId": "EBAY_US",
                    }
                ]
            },
        )
    ]
    response = api.client.post(api.url, {"confirm_stock_location": True}, format="json")
    assert response.status_code == 409
    assert api.session.request.call_count == 1


def test_prepare_refuses_unowned_inventory(preparation_api):
    api = preparation_api
    api.session.request.side_effect = [
        api.response(404),
        api.response(200, {"sku": "4071489789867"}),
    ]
    assert (
        api.client.post(
            api.url, {"confirm_stock_location": True}, format="json"
        ).status_code
        == 409
    )
    assert api.session.request.call_count == 2


def test_prepare_timeout_blocks_retry_and_edit(preparation_api):
    """После таймаута createOffer повтор и редактирование блокируются до сверки eBay."""
    import requests

    api = preparation_api
    api.session.request.side_effect = [
        api.response(404),
        api.response(404),
        api.response(204),
        requests.Timeout(),
    ]
    assert (
        api.client.post(
            api.url, {"confirm_stock_location": True}, format="json"
        ).status_code
        == 502
    )
    state = api.client.get(api.url).data
    assert state["status"] == "uncertain"
    assert state["inventory_confirmed"] is True
    assert state["offer_id"] == ""
    assert (
        api.client.post(
            api.url, {"confirm_stock_location": True}, format="json"
        ).status_code
        == 409
    )
    assert api.session.request.call_count == 4
    assert (
        api.client.patch(
            api.url.removesuffix("prepare/"), {"title": "Changed"}, format="json"
        ).status_code
        == 400
    )


def test_prepare_routes_changed_draft_to_existing_offer_update(
    preparation_api, monkeypatch
):
    """После изменения черновика prepare обновляет прежний Offer без повторного создания."""
    from apps.marketplace.ebay import updating
    from apps.marketplace.ebay.models import EbayOfferPreparation

    api = preparation_api
    assert (
        api.client.post(
            api.url, {"confirm_stock_location": True}, format="json"
        ).status_code
        == 200
    )
    api.draft.title = "Changed title"
    api.draft.save(update_fields=["title"])
    operation = MagicMock(return_value=EbayOfferPreparation.objects.get())
    monkeypatch.setattr(updating, "update_unpublished_offer", operation)
    response = api.client.post(api.url, {"confirm_stock_location": True}, format="json")
    assert response.status_code == 200
    assert response.data["offer_id"] == "9001"
    operation.assert_called_once_with(api.draft.product_id, account="depotum")
    assert api.session.request.call_count == 4


def test_prepare_requires_stock_confirmation_and_manager(preparation_api, draft_api):
    api = preparation_api
    assert api.client.post(api.url, {}, format="json").status_code == 400
    assert (
        api.client.post(
            api.url, {"confirm_stock_location": False}, format="json"
        ).status_code
        == 400
    )
    api.client.force_authenticate(draft_api.seller)
    assert (
        api.client.post(
            api.url, {"confirm_stock_location": True}, format="json"
        ).status_code
        == 403
    )
    assert api.client.get(api.url).status_code == 403
    api.session.request.assert_not_called()


@pytest.mark.parametrize("with_responsible_person", [False, True])
def test_prepare_missing_manufacturer_is_warning(
    preview_draft, regulatory_contact, monkeypatch, with_responsible_person
):
    """Отсутствующий производитель виден в preview, но не блокирует неопубликованный Offer."""
    from apps.marketplace.ebay.preparation import _check_local
    from apps.marketplace.ebay.preview import build_draft_preview

    preview_draft.regulatory = (
        {"responsiblePersons": [regulatory_contact]} if with_responsible_person else {}
    )
    preview = build_draft_preview(preview_draft)
    draft = SimpleNamespace(
        regulatory=preview_draft.regulatory,
        description=preview_draft.description,
        quantity=1,
        category_id=preview_draft.category_id,
        aspects=preview_draft.aspects,
        product=SimpleNamespace(
            status="approved",
            is_available=True,
            variants=SimpleNamespace(aggregate=lambda **kwargs: {"total": 1}),
        ),
    )
    monkeypatch.setattr(
        "apps.marketplace.ebay.preparation.get_category_aspects",
        lambda **kwargs: {
            "required_aspects": ["Farbe"],
            "aspects": [{"localizedAspectName": "Farbe", "aspectConstraint": {}}],
        },
    )
    _check_local(draft, preview)
    assert "regulatory.manufacturer" in preview["missing_regulatory_fields"]
    assert "manufacturer" not in preview["offer"]["body"].get("regulatory", {})


def test_preparation_preserves_existing_product_delete_behavior(preparation_api):
    """Локальное состояние отправки не добавляет запрет на существующее удаление товара."""
    from apps.marketplace.ebay.models import EbayOfferPreparation

    api = preparation_api
    assert (
        api.client.post(
            api.url, {"confirm_stock_location": True}, format="json"
        ).status_code
        == 200
    )
    api.draft.product.delete()
    assert not EbayOfferPreparation.objects.exists()
    assert api.session.request.call_count == 4


@pytest.mark.parametrize("quantity", [0, 2])
def test_prepare_local_check_rejects_zero_or_excess_stock(
    preview_draft, regulatory_contact, quantity
):
    """Остаток не берётся из цены или черновика без проверки доступного количества товара."""
    from apps.marketplace.ebay.preparation import _check_local
    from apps.marketplace.ebay.preview import build_draft_preview

    preview_draft.regulatory = {"manufacturer": regulatory_contact}
    preview_draft.quantity = quantity
    preview = build_draft_preview(preview_draft)
    draft = SimpleNamespace(
        regulatory=preview_draft.regulatory,
        description=preview_draft.description,
        quantity=quantity,
        product=SimpleNamespace(
            status="approved",
            is_available=True,
            variants=SimpleNamespace(aggregate=lambda **kwargs: {"total": 1}),
        ),
    )
    with pytest.raises(ValidationError, match="quantity"):
        _check_local(draft, preview)


def test_prepare_http_error_stores_sanitized_diagnostics(preparation_api):
    """Ошибка содержит этап и причину, но не токен, сырой ответ или контакты."""
    api = preparation_api
    api.session.request.side_effect = [
        api.response(404),
        api.response(404),
        api.response(
            400,
            {
                "errors": [
                    {
                        "errorId": 25002,
                        "message": "Invalid request secret-token",
                        "longMessage": "Missing field inventoryItemGroupKey; email user@example.com",
                    }
                ],
                "private_payload": "do not store this",
            },
        ),
    ]
    response = api.client.post(api.url, {"confirm_stock_location": True}, format="json")
    assert response.status_code == 502
    state = api.client.get(api.url).data
    assert state["status"] == "failed"
    assert "25002" in state["last_error"]
    assert "secret-token" not in state["last_error"]
    assert "user@example.com" not in state["last_error"]
    assert "private_payload" not in state["last_error"]
    assert "PUT /sell/inventory/v1/inventory_item/" in state["last_error"]
    assert "Missing field inventoryItemGroupKey" in state["last_error"]


def test_offer_error_redacts_gpsr_contacts_and_identifies_post(preparation_api):
    """Причина отказа createOffer доступна без отражённых адресов из regulatory."""
    api = preparation_api
    contact = api.draft.regulatory["manufacturer"]
    api.session.request.side_effect = [
        api.response(404),
        api.response(404),
        api.response(204),
        api.response(
            400,
            {
                "errors": [
                    {
                        "errorId": 2004,
                        "message": "Invalid request",
                        "longMessage": "Invalid regulatory field: "
                        + " | ".join(contact.values()),
                    }
                ]
            },
        ),
    ]
    response = api.client.post(api.url, {"confirm_stock_location": True}, format="json")
    assert response.status_code == 502
    state = api.client.get(api.url).data
    assert state["status"] == "failed"
    assert state["inventory_confirmed"] is True
    assert "POST /sell/inventory/v1/offer" in state["last_error"]
    assert "Invalid regulatory field" in state["last_error"]
    for field, value in contact.items():
        if field != "country":
            assert value not in state["last_error"]


@pytest.mark.parametrize(
    "person_type", ["EUResponsiblePerson", "EU_RESPONSIBLE_PERSON"]
)
def test_preview_normalizes_responsible_person_for_rest_without_changing_draft(
    preview_draft, regulatory_contact, person_type
):
    """Сохранённое старое значение не уходит в REST и не требует изменения базы."""
    from apps.marketplace.ebay.preview import build_draft_preview

    contact = {**regulatory_contact, "types": [person_type]}
    preview_draft.regulatory = {"responsiblePersons": [contact]}
    before = deepcopy(preview_draft.regulatory)
    result = build_draft_preview(preview_draft)
    assert result["offer"]["body"]["regulatory"]["responsiblePersons"][0]["types"] == [
        "EU_RESPONSIBLE_PERSON"
    ]
    assert preview_draft.regulatory == before


def test_error_reason_is_visible_and_redacted(regulatory_contact):
    """Причина ошибки десериализации не теряется вместе с общим сообщением 2004."""
    from apps.marketplace.ebay.preparation import _error_messages

    reason = (
        "Invalid responsiblePersons.types EUResponsiblePerson; "
        + regulatory_contact["addressLine1"]
    )
    message = _error_messages(
        [
            {
                "message": "Invalid request",
                "parameters": [
                    {"name": "reason", "value": reason + " secret-token"},
                    None,
                ],
            }
        ],
        "secret-token",
        {"regulatory": {"responsiblePersons": [regulatory_contact]}},
    )
    assert "reason: Invalid responsiblePersons.types EUResponsiblePerson" in message
    assert regulatory_contact["addressLine1"] not in message
    assert "secret-token" not in message


@pytest.fixture
def update_api(preparation_api, monkeypatch):
    """Создаёт локально подготовленный Offer и подменяет все ответы обновления."""
    from apps.marketplace.ebay import preparation, updating
    from apps.marketplace.ebay.preview import build_draft_preview

    api = preparation_api
    assert (
        api.client.post(
            api.url, {"confirm_stock_location": True}, format="json"
        ).status_code
        == 200
    )
    api.session.request.reset_mock()
    context = MagicMock()
    context.__enter__.return_value = api.session
    monkeypatch.setattr(updating, "build_marketplace_session", lambda: context)
    monkeypatch.setattr(updating, "get_access_token", preparation.get_access_token)
    monkeypatch.setattr(
        updating, "_validate_listing_settings", preparation._validate_listing_settings
    )
    preview = build_draft_preview(api.draft)
    api.offer = {
        **preview["offer"]["body"],
        "offerId": "9001",
        "status": "UNPUBLISHED",
        "tax": {"vatPercentage": 19.0},
    }
    api.inventory = {
        **preview["inventory_item"]["body"],
        "sku": "4071489789867",
        "locale": "de_DE",
        "packageWeightAndSize": {"weight": {"value": 30, "unit": "KILOGRAM"}},
    }
    api.update_url = api.url
    api.draft.price_eur = Decimal("7050.00")
    api.draft.save(update_fields=["price_eur"])

    def replies():
        return [
            api.response(200, {"offers": [deepcopy(api.offer)], "total": 1}),
            api.response(200, deepcopy(api.offer)),
            api.response(200, deepcopy(api.inventory)),
            api.response(204),
            api.response(200, {"offers": [deepcopy(api.offer)], "total": 1}),
            api.response(200, deepcopy(api.offer)),
            api.response(204),
        ]

    api.replies = replies
    api.session.request.side_effect = replies()
    return api


def test_update_unpublished_offer_preserves_id_and_additional_fields(update_api):
    """PUT сохраняет настройки eBay и offerId; POST create/publish не выполняются."""
    from apps.marketplace.ebay.models import EbayOfferPreparation

    api = update_api
    before_product = (
        type(api.draft.product).objects.filter(pk=api.draft.product_id).values().get()
    )
    response = api.client.post(
        api.update_url, {"confirm_stock_location": True}, format="json"
    )
    assert response.status_code == 200, response.data
    assert response.data["offer_id"] == "9001"
    assert response.data["status"] == "prepared"
    assert response.data["publication_performed"] is False
    calls = api.session.request.call_args_list
    assert [call.args[0] for call in calls] == [
        "GET",
        "GET",
        "GET",
        "PUT",
        "GET",
        "GET",
        "PUT",
    ]
    assert calls[-1].args[1].endswith("/offer/9001")
    inventory_body = calls[3].kwargs["json"]
    offer_body = calls[-1].kwargs["json"]
    assert (
        inventory_body["packageWeightAndSize"] == api.inventory["packageWeightAndSize"]
    )
    assert "sku" not in inventory_body and "locale" not in inventory_body
    assert offer_body["tax"] == api.offer["tax"]
    assert offer_body["pricingSummary"]["price"]["value"] == "7050.00"
    assert not (
        {"offerId", "status", "sku", "marketplaceId", "format"} & offer_body.keys()
    )
    assert (
        type(api.draft.product).objects.filter(pk=api.draft.product_id).values().get()
        == before_product
    )
    assert EbayOfferPreparation.objects.count() == 1
    api.session.request.side_effect = api.replies()
    assert (
        api.client.post(
            api.update_url, {"confirm_stock_location": True}, format="json"
        ).data["offer_id"]
        == "9001"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "PUBLISHED"},
        {"listing": {"listingId": "active"}},
        {"sku": "another"},
        {"marketplaceId": "EBAY_US"},
        {"offerId": "other"},
        {"listingStartDate": "2030-01-01T00:00:00Z"},
    ],
)
def test_update_refuses_live_or_mismatched_offer(update_api, changes):
    api = update_api
    api.offer.update(changes)
    api.session.request.side_effect = api.replies()
    response = api.client.post(
        api.update_url, {"confirm_stock_location": True}, format="json"
    )
    assert response.status_code == 409
    assert all(call.args[0] == "GET" for call in api.session.request.call_args_list)


def test_update_refuses_another_offer_using_same_inventory(update_api):
    api = update_api
    api.session.request.side_effect = [
        api.response(
            200,
            {
                "offers": [api.offer, {"offerId": "external", "status": "PUBLISHED"}],
                "total": 2,
            },
        )
    ]
    assert (
        api.client.post(
            api.update_url, {"confirm_stock_location": True}, format="json"
        ).status_code
        == 409
    )
    assert api.session.request.call_count == 1


def test_update_rechecks_offer_after_inventory_put(update_api):
    """Если Offer опубликован извне между шагами, второй PUT не отправляется."""
    api = update_api
    replies = api.replies()
    replies[5] = api.response(200, {**api.offer, "status": "PUBLISHED"})
    api.session.request.side_effect = replies
    assert (
        api.client.post(
            api.update_url, {"confirm_stock_location": True}, format="json"
        ).status_code
        == 409
    )
    assert [call.args[0] for call in api.session.request.call_args_list].count(
        "PUT"
    ) == 1
    state = api.client.get(api.url).data
    assert state["status"] == "failed"
    assert "Inventory Item was updated" in state["last_error"]


def test_update_timeout_keeps_old_hash_and_locks_retry(update_api):
    import requests

    from apps.marketplace.ebay.models import EbayOfferPreparation

    api = update_api
    old_hash = EbayOfferPreparation.objects.get().payload_hash
    replies = api.replies()
    replies[-1] = requests.Timeout()
    api.session.request.side_effect = replies
    assert (
        api.client.post(
            api.update_url, {"confirm_stock_location": True}, format="json"
        ).status_code
        == 502
    )
    preparation = EbayOfferPreparation.objects.get()
    assert preparation.status == "uncertain"
    assert preparation.offer_id == "9001"
    assert preparation.payload_hash == old_hash
    count = api.session.request.call_count
    assert (
        api.client.post(
            api.update_url, {"confirm_stock_location": True}, format="json"
        ).status_code
        == 409
    )
    assert api.session.request.call_count == count


def test_update_service_requires_existing_offer(draft_api, preview_draft):
    from apps.marketplace.ebay.preparation import PreparationConflict
    from apps.marketplace.ebay.updating import update_unpublished_offer

    preview_draft.product = draft_api.product
    preview_draft.save()
    with pytest.raises(PreparationConflict, match="prepare first"):
        update_unpublished_offer(draft_api.product.pk, account="depotum")


def test_update_requires_manager_and_stock_confirmation(update_api, draft_api):
    api = update_api
    assert api.client.post(api.update_url, {}, format="json").status_code == 400
    api.client.force_authenticate(draft_api.seller)
    assert (
        api.client.post(
            api.update_url, {"confirm_stock_location": True}, format="json"
        ).status_code
        == 403
    )
    api.session.request.assert_not_called()


@pytest.mark.parametrize(
    "extra_inventory,extra_offer",
    [
        ({"inventoryItemGroupKeys": ["group"]}, {}),
        ({"newUnsupportedField": True}, {}),
        ({}, {"newUnsupportedField": True}),
    ],
)
def test_update_does_not_drop_unknown_remote_fields(
    update_api, extra_inventory, extra_offer
):
    api = update_api
    api.inventory.update(extra_inventory)
    api.offer.update(extra_offer)
    api.session.request.side_effect = api.replies()
    assert (
        api.client.post(
            api.update_url, {"confirm_stock_location": True}, format="json"
        ).status_code
        == 409
    )
    assert all(call.args[0] == "GET" for call in api.session.request.call_args_list)


@pytest.fixture
def publish_api(update_api, monkeypatch):
    """Изолирует публикацию моками; реальные eBay/FTP недоступны из тестов."""
    from apps.marketplace.ebay import publishing
    from apps.marketplace.ebay.models import EbayOfferPreparation
    from apps.marketplace.ebay.preparation import _payload_hash
    from apps.marketplace.ebay.preview import build_draft_preview

    api = update_api
    preview = build_draft_preview(api.draft)
    api.fingerprint = _payload_hash(preview)
    EbayOfferPreparation.objects.update(payload_hash=api.fingerprint, account="depotum")
    api.offer = {
        **deepcopy(preview["offer"]["body"]),
        "offerId": "9001",
        "status": "UNPUBLISHED",
    }
    api.inventory = {
        **deepcopy(preview["inventory_item"]["body"]),
        "sku": "4071489789867",
    }
    api.publish_url = api.url.removesuffix("prepare/") + "publish/"
    api.confirmation = {
        "confirm_stock_location": True,
        "confirm_publication": True,
        "preview_hash": api.fingerprint,
    }
    context = MagicMock()
    context.__enter__.return_value = api.session
    monkeypatch.setattr(publishing, "build_marketplace_session", lambda: context)
    monkeypatch.setattr(publishing, "get_access_token", lambda **kwargs: "secret-token")
    monkeypatch.setattr(publishing, "_check_local", MagicMock())
    monkeypatch.setattr(publishing, "_validate_listing_settings", MagicMock())
    monkeypatch.setattr(
        publishing,
        "get_seller_resource",
        lambda **kwargs: {"sellerRegistrationCompleted": True},
    )
    api.images = MagicMock()
    monkeypatch.setattr(publishing, "_check_images", api.images)

    def replies():
        return [
            api.response(200, {"offers": [deepcopy(api.offer)], "total": 1}),
            api.response(200, deepcopy(api.offer)),
            api.response(200, deepcopy(api.inventory)),
            api.response(200, {"offers": [deepcopy(api.offer)], "total": 1}),
            api.response(200, deepcopy(api.offer)),
            api.response(200, {"listingId": "123456789012"}),
        ]

    api.publish_replies = replies
    api.session.request.side_effect = replies()
    return api


def test_publish_checks_read_only_and_accept_equivalent_price(publish_api):
    from apps.marketplace.ebay.models import EbayOfferPreparation

    api = publish_api
    api.offer["pricingSummary"]["price"]["value"] = "7050.0"
    api.session.request.side_effect = api.publish_replies()
    before = EbayOfferPreparation.objects.values().get()
    response = api.client.get(api.publish_url)
    assert response.status_code == 200, response.data
    assert response.data["ready"] is True
    assert response.data["preview_hash"] == api.fingerprint
    assert response.data["blocking_reasons"] == []
    assert response["Cache-Control"] == "no-store"
    assert all(call.args[0] == "GET" for call in api.session.request.call_args_list)
    assert EbayOfferPreparation.objects.values().get() == before
    api.images.assert_called_once()


def test_publish_requires_manufacturer_but_does_not_invent_it(publish_api):
    api = publish_api
    contact = api.draft.regulatory["manufacturer"]
    api.draft.regulatory = {
        "responsiblePersons": [{**contact, "types": ["EU_RESPONSIBLE_PERSON"]}]
    }
    api.draft.save(update_fields=["regulatory"])
    response = api.client.get(api.publish_url)
    assert response.status_code == 200
    assert response.data["ready"] is False
    assert "regulatory.manufacturer" in response.data["blocking_reasons"]
    api.session.request.assert_not_called()
    api.images.assert_not_called()


def test_publish_once_saves_listing_id_and_repeat_is_noop(publish_api):
    from apps.marketplace.ebay.models import EbayOfferPreparation

    api = publish_api
    before_product = (
        type(api.draft.product).objects.filter(pk=api.draft.product_id).values().get()
    )
    response = api.client.post(api.publish_url, api.confirmation, format="json")
    assert response.status_code == 200, response.data
    assert response.data["status"] == "published"
    assert response.data["listing_id"] == "123456789012"
    assert response.data["offer_id"] == "9001"
    assert response.data["publication_performed"] is True
    preparation = EbayOfferPreparation.objects.get()
    assert preparation.listing_id == "123456789012"
    assert preparation.status == "published"
    calls = api.session.request.call_args_list
    assert [call.args[0] for call in calls] == ["GET"] * 5 + ["POST"]
    assert calls[-1].args[1].endswith("/offer/9001/publish")
    assert calls[-1].kwargs["json"] is None
    count = api.session.request.call_count
    assert (
        api.client.post(api.publish_url, api.confirmation, format="json").data[
            "listing_id"
        ]
        == "123456789012"
    )
    assert api.session.request.call_count == count
    assert (
        type(api.draft.product).objects.filter(pk=api.draft.product_id).values().get()
        == before_product
    )
    assert (
        api.client.post(
            api.update_url, {"confirm_stock_location": True}, format="json"
        ).status_code
        == 409
    )
    assert (
        api.client.post(
            api.url, {"confirm_stock_location": True}, format="json"
        ).status_code
        == 409
    )


@pytest.mark.parametrize(
    "field", ["confirm_publication", "confirm_stock_location", "preview_hash"]
)
def test_publish_requires_explicit_confirmation_and_hash(publish_api, field):
    api = publish_api
    data = dict(api.confirmation)
    data.pop(field)
    assert api.client.post(api.publish_url, data, format="json").status_code == 400
    api.session.request.assert_not_called()


def test_publish_rejects_stale_review_without_changing_state(publish_api):
    from apps.marketplace.ebay.models import EbayOfferPreparation

    api = publish_api
    before = EbayOfferPreparation.objects.values().get()
    api.confirmation["preview_hash"] = "0" * 64
    assert (
        api.client.post(api.publish_url, api.confirmation, format="json").status_code
        == 409
    )
    assert EbayOfferPreparation.objects.values().get() == before
    api.session.request.assert_not_called()


@pytest.mark.parametrize(
    "changes",
    [{"status": "PUBLISHED"}, {"sku": "wrong"}, {"listing": {"listingId": "external"}}],
)
def test_publish_refuses_live_or_mismatched_offer(publish_api, changes):
    api = publish_api
    api.offer.update(changes)
    api.session.request.side_effect = api.publish_replies()
    response = api.client.post(api.publish_url, api.confirmation, format="json")
    assert response.status_code == 400
    assert all(call.args[0] == "GET" for call in api.session.request.call_args_list)


def test_publish_refuses_remote_price_change(publish_api):
    api = publish_api
    api.offer["pricingSummary"]["price"]["value"] = "9999.00"
    api.session.request.side_effect = api.publish_replies()
    response = api.client.post(api.publish_url, api.confirmation, format="json")
    assert response.status_code == 400
    assert "offer.pricingSummary.price.value" in str(response.data)
    assert all(call.args[0] == "GET" for call in api.session.request.call_args_list)


@pytest.mark.parametrize("failure", ["timeout", "missing_id"])
def test_publish_uncertain_result_blocks_retry(publish_api, failure):
    import requests

    from apps.marketplace.ebay.models import EbayOfferPreparation

    api = publish_api
    replies = api.publish_replies()
    replies[-1] = requests.Timeout() if failure == "timeout" else api.response(200, {})
    api.session.request.side_effect = replies
    assert (
        api.client.post(api.publish_url, api.confirmation, format="json").status_code
        == 502
    )
    state = EbayOfferPreparation.objects.get()
    assert state.status == "uncertain" and state.listing_id == ""
    count = api.session.request.call_count
    assert (
        api.client.post(api.publish_url, api.confirmation, format="json").status_code
        == 409
    )
    assert api.session.request.call_count == count


def test_publish_definite_rejection_is_failed(publish_api):
    from apps.marketplace.ebay.models import EbayOfferPreparation

    api = publish_api
    replies = api.publish_replies()
    replies[-1] = api.response(
        400, {"errors": [{"errorId": 25002, "message": "Invalid request"}]}
    )
    api.session.request.side_effect = replies
    assert (
        api.client.post(api.publish_url, api.confirmation, format="json").status_code
        == 502
    )
    state = EbayOfferPreparation.objects.get()
    assert state.status == "failed" and state.listing_id == ""


def test_publish_requires_manager_and_scopes_environment(
    publish_api, draft_api, settings
):
    api = publish_api
    settings.EBAY_ENVIRONMENT = "production"
    assert api.client.get(api.publish_url).status_code == 404
    assert (
        api.client.post(api.publish_url, api.confirmation, format="json").status_code
        == 404
    )
    settings.EBAY_ENVIRONMENT = "sandbox"
    api.client.force_authenticate(draft_api.seller)
    assert api.client.get(api.publish_url).status_code == 403
    assert (
        api.client.post(api.publish_url, api.confirmation, format="json").status_code
        == 403
    )
    api.session.request.assert_not_called()


@pytest.mark.parametrize(
    "url",
    [
        "http://media.example.com/a.jpg",
        "https://other.example.com/a.jpg",
        "https://media.example.com:444/a.jpg",
        "https://user:pass@media.example.com/a.jpg",
    ],
)
def test_publish_image_check_refuses_unsafe_origin(settings, monkeypatch, url):
    from apps.marketplace.ebay import publishing

    settings.FTP_MEDIA_PUBLIC_BASE_URL = "https://media.example.com/api-media/"
    session = MagicMock()
    context = MagicMock()
    context.__enter__.return_value = session
    monkeypatch.setattr(publishing, "build_marketplace_session", lambda: context)
    with pytest.raises(ValidationError):
        publishing._check_images([url])
    session.head.assert_not_called()


@pytest.mark.parametrize(
    "status,content_type,valid",
    [
        (200, "image/jpeg", True),
        (302, "image/jpeg", False),
        (200, "text/html", False),
        (404, "text/html", False),
    ],
)
def test_publish_image_availability_uses_head_without_redirects(
    settings, monkeypatch, status, content_type, valid
):
    from apps.marketplace.ebay import publishing

    settings.FTP_MEDIA_PUBLIC_BASE_URL = "https://media.example.com/api-media/"
    session = MagicMock()
    context = MagicMock()
    context.__enter__.return_value = session
    image_response = MagicMock(
        status_code=status, headers={"Content-Type": content_type}
    )
    session.head.return_value.__enter__.return_value = image_response
    monkeypatch.setattr(publishing, "build_marketplace_session", lambda: context)
    monkeypatch.setattr(publishing, "_validate_public_host", lambda host: None)
    if valid:
        publishing._check_images(["https://media.example.com/api-media/a.jpg"])
    else:
        with pytest.raises(ValidationError):
            publishing._check_images(["https://media.example.com/api-media/a.jpg"])
    assert session.head.call_args.kwargs["allow_redirects"] is False
    session.post.assert_not_called()


def test_publish_image_check_refuses_private_dns(settings, monkeypatch):
    from apps.common.safe_image_download import GeneratedImageDownloadError
    from apps.marketplace.ebay import publishing

    settings.FTP_MEDIA_PUBLIC_BASE_URL = "https://media.example.com/api-media/"
    session = MagicMock()
    context = MagicMock()
    context.__enter__.return_value = session
    monkeypatch.setattr(publishing, "build_marketplace_session", lambda: context)
    monkeypatch.setattr(
        publishing,
        "_validate_public_host",
        MagicMock(side_effect=GeneratedImageDownloadError("private")),
    )
    with pytest.raises(ValidationError):
        publishing._check_images(["https://media.example.com/api-media/a.jpg"])
    session.head.assert_not_called()


def test_publish_blocks_incomplete_seller_registration(publish_api, monkeypatch):
    from apps.marketplace.ebay import publishing

    api = publish_api
    monkeypatch.setattr(
        publishing,
        "get_seller_resource",
        lambda **kwargs: {"sellerRegistrationCompleted": False},
    )
    response = api.client.get(api.publish_url)
    assert response.status_code == 200
    assert response.data["ready"] is False
    assert "Seller registration" in str(response.data["blocking_reasons"])
    api.session.request.assert_not_called()


def test_publish_checks_image_failure_never_sends_publish(publish_api):
    api = publish_api
    api.images.side_effect = ValidationError("Image is unavailable.")
    response = api.client.post(api.publish_url, api.confirmation, format="json")
    assert response.status_code == 400
    assert all(call.args[0] == "GET" for call in api.session.request.call_args_list)


def test_depotum_draft_is_separate_and_checks_its_own_policies(
    draft_api, listing_settings_api
):
    """Depotum не использует черновик JV и проверяет ссылки с токеном Depotum уже при создании."""
    from apps.products.models import Product

    api = draft_api
    EbayDraft.objects.create(
        product=api.product,
        environment="sandbox",
        account="jv",
        title="Existing JV draft",
    )
    before = EbayDraft.objects.filter(account="jv").values().get()
    before_product = Product.objects.filter(pk=api.product.pk).values().get()
    response = api.client.put(
        api.url, {**listing_settings_api.values, "account": "jv"}, format="json"
    )
    assert response.status_code == 201, response.data
    assert response.data["account"] == "depotum"
    listing_settings_api.token.assert_called_once_with(account="depotum")
    assert response.data["title"] == ""
    assert EbayDraft.objects.filter(account="jv").values().get() == before
    assert Product.objects.filter(pk=api.product.pk).values().get() == before_product
    assert EbayDraft.objects.count() == 2


@pytest.fixture
def depotum_api(preparation_api, monkeypatch):
    """Даёт независимые записи JV/Depotum одного товара и перехватывает все токены."""
    from apps.marketplace.ebay import preparation, publishing, updating
    from apps.marketplace.ebay.models import EbayOfferPreparation

    api = preparation_api
    api.draft.account = "jv"
    api.draft.save(update_fields=["account"])
    api.jv_draft_id = api.draft.pk
    EbayOfferPreparation.objects.create(
        draft=api.draft,
        environment="sandbox",
        account="jv",
        sku="4071489789867",
        payload_hash="a" * 64,
        status="failed",
        inventory_confirmed=True,
        offer_id="jv-existing-offer",
    )
    api.jv_before = EbayOfferPreparation.objects.filter(account="jv").values().get()
    api.draft = deepcopy(api.draft)
    api.draft.pk = None
    api.draft.account = "depotum"
    api.draft.save(force_insert=True)
    api.token = MagicMock(return_value="depotum-token")
    context = MagicMock()
    context.__enter__.return_value = api.session
    for module in (preparation, updating, publishing):
        monkeypatch.setattr(module, "get_access_token", api.token)
        monkeypatch.setattr(module, "build_marketplace_session", lambda: context)
    for module in (updating, publishing):
        monkeypatch.setattr(
            module, "_validate_listing_settings", preparation._validate_listing_settings
        )
    monkeypatch.setattr(publishing, "_check_local", preparation._check_local)
    monkeypatch.setattr(publishing, "_check_images", MagicMock())
    monkeypatch.setattr(
        publishing,
        "get_seller_resource",
        lambda **kwargs: {"sellerRegistrationCompleted": True},
    )
    return api


def test_depotum_preview_and_prepare_use_jv_ean_and_depotum_token(depotum_api):
    from apps.marketplace.ebay.models import EbayOfferPreparation

    api = depotum_api
    from apps.marketplace.ebay.preview import build_draft_preview

    preview = build_draft_preview(api.draft)
    assert preview["account"] == "depotum"
    assert preview["sku"] == preview["ean"] == "4071489789867"
    api.token.assert_not_called()
    response = api.client.post(api.url, {"confirm_stock_location": True}, format="json")
    assert response.status_code == 200, response.data
    assert response.data["account"] == "depotum"
    assert response.data["offer_id"] == "9001"
    api.token.assert_called_once_with(account="depotum")
    assert all(
        call.kwargs["headers"]["Authorization"] == "Bearer depotum-token"
        for call in api.session.request.call_args_list
    )
    assert (
        EbayOfferPreparation.objects.filter(account="jv").values().get()
        == api.jv_before
    )
    assert EbayOfferPreparation.objects.count() == 2


def test_depotum_update_and_publish_keep_jv_untouched(depotum_api):
    from apps.marketplace.ebay.models import EbayOfferPreparation
    from apps.marketplace.ebay.preview import build_draft_preview

    api = depotum_api
    assert (
        api.client.post(
            api.url, {"confirm_stock_location": True}, format="json"
        ).status_code
        == 200
    )
    api.draft.description += " Updated for Depotum."
    api.draft.save(update_fields=["description"])
    preview = build_draft_preview(api.draft)
    offer = {
        **deepcopy(preview["offer"]["body"]),
        "offerId": "9001",
        "status": "UNPUBLISHED",
    }
    inventory = {**deepcopy(preview["inventory_item"]["body"]), "sku": "4071489789867"}

    def listing():
        return api.response(200, {"offers": [offer], "total": 1})

    api.session.request.side_effect = [
        listing(),
        api.response(200, offer),
        api.response(200, inventory),
        api.response(204),
        listing(),
        api.response(200, offer),
        api.response(204),
    ]
    base = api.url.removesuffix("prepare/")
    response = api.client.post(
        base + "prepare/", {"confirm_stock_location": True}, format="json"
    )
    assert response.status_code == 200, response.data
    assert response.data["account"] == "depotum"
    assert response.data["offer_id"] == "9001"

    api.session.request.side_effect = [
        listing(),
        api.response(200, offer),
        api.response(200, inventory),
    ]
    checks = api.client.get(base + "publish/")
    assert checks.status_code == 200, checks.data
    assert checks.data["ready"] is True
    api.session.request.side_effect = [
        listing(),
        api.response(200, offer),
        api.response(200, inventory),
        listing(),
        api.response(200, offer),
        api.response(200, {"listingId": "depotum-listing"}),
    ]
    response = api.client.post(
        base + "publish/",
        {
            "confirm_publication": True,
            "confirm_stock_location": True,
            "preview_hash": checks.data["preview_hash"],
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    assert response.data["listing_id"] == "depotum-listing"
    assert response.data["account"] == "depotum"
    assert all(
        call.kwargs == {"account": "depotum"} for call in api.token.call_args_list
    )
    assert (
        EbayOfferPreparation.objects.filter(account="jv").values().get()
        == api.jv_before
    )
    assert (
        EbayDraft.objects.get(pk=api.jv_draft_id).description != api.draft.description
    )


@pytest.mark.parametrize("account", ["jv", "xl"])
def test_publish_service_refuses_non_depotum_before_network(
    draft_api, monkeypatch, account
):
    from apps.marketplace.ebay.preparation import PreparationConflict
    from apps.marketplace.ebay.publishing import publish_offer

    network = MagicMock(side_effect=AssertionError("Unexpected network"))
    monkeypatch.setattr("requests.sessions.Session.request", network)
    with pytest.raises(PreparationConflict, match="only for Depotum"):
        publish_offer(draft_api.product.pk, "0" * 64, account=account)
    network.assert_not_called()


def test_removed_routes_are_unavailable_without_network(monkeypatch):
    """Удалённые маршруты JV и диагностики не вызывают eBay."""
    from django.urls import Resolver404, resolve

    network = MagicMock(side_effect=AssertionError("Unexpected network"))
    monkeypatch.setattr("requests.sessions.Session.request", network)
    for account, suffixes in (
        ("jv", ("", "preview/", "remote/", "prepare/", "update/", "publish/")),
        ("depotum", ("preview/", "remote/", "update/")),
    ):
        for suffix in suffixes:
            with pytest.raises(Resolver404):
                resolve(
                    f"/drafts/{account}/69/{suffix}",
                    urlconf="apps.marketplace.ebay.urls",
                )
    network.assert_not_called()


def test_depotum_never_falls_back_to_jv_draft_or_offer(depotum_api):
    api = depotum_api
    api.draft.delete()
    base = api.url.removesuffix("prepare/")
    for suffix in ("", "prepare/", "publish/"):
        assert api.client.get(base + suffix).status_code == 404
    assert (
        api.client.post(
            api.url, {"confirm_stock_location": True}, format="json"
        ).status_code
        == 404
    )
    api.session.request.assert_not_called()


def test_depotum_without_its_connection_does_not_use_jv_token(depotum_api, monkeypatch):
    from apps.marketplace.ebay import preparation
    from apps.marketplace.ebay.models import EbayOfferPreparation

    api = depotum_api
    token = MagicMock(
        side_effect=EbayConfigurationError("Connect this eBay account first.")
    )
    monkeypatch.setattr(preparation, "get_access_token", token)
    response = api.client.post(api.url, {"confirm_stock_location": True}, format="json")
    assert response.status_code == 503
    token.assert_called_once_with(account="depotum")
    api.session.request.assert_not_called()
    assert (
        EbayOfferPreparation.objects.filter(account="jv").values().get()
        == api.jv_before
    )


@pytest.fixture
def listing_api(publish_api, monkeypatch):
    """Создаёт опубликованный листинг на моках и изолирует операции живого объявления."""
    from apps.marketplace.ebay import listing

    api = publish_api
    response = api.client.post(api.publish_url, api.confirmation, format="json")
    assert response.status_code == 200, response.data
    api.session.request.reset_mock()
    api.listing_url = api.url.removesuffix("prepare/") + "listing/"
    api.offer["status"] = "PUBLISHED"
    api.offer["listing"] = {
        "listingId": "123456789012",
        "listingStatus": "ACTIVE",
        "soldQuantity": 0,
    }
    api.inventory["availability"]["shipToLocationAvailability"][
        "allocationByFormat"
    ] = {"fixedPrice": 1, "auction": 0}
    context = MagicMock()
    context.__enter__.return_value = api.session
    monkeypatch.setattr(listing, "build_marketplace_session", lambda: context)
    api.token = MagicMock(return_value="listing-token")
    monkeypatch.setattr(listing, "get_access_token", api.token)
    monkeypatch.setattr(listing, "_check_local", MagicMock())
    monkeypatch.setattr(listing, "_check_images", MagicMock())
    monkeypatch.setattr(listing, "_validate_listing_settings", MagicMock())
    monkeypatch.setattr(listing, "_check_new_condition", MagicMock())
    return api


def test_listing_get_returns_all_resources_and_current_status_read_only(listing_api):
    from apps.marketplace.ebay.models import EbayOfferPreparation

    api = listing_api
    before = EbayOfferPreparation.objects.values().get()
    api.session.request.side_effect = [
        api.response(200, api.inventory),
        api.response(200, api.offer),
    ]
    response = api.client.get(api.listing_url)
    assert response.status_code == 200, response.data
    assert response.data["inventory_item"] == api.inventory
    assert response.data["offer"] == api.offer
    assert response.data["remote_status"] == "PUBLISHED"
    assert response.data["listing_status"] == "ACTIVE"
    assert response.data["remote_listing_id"] == "123456789012"
    assert response["Cache-Control"] == "no-store"
    assert all(call.args[0] == "GET" for call in api.session.request.call_args_list)
    api.token.assert_called_once_with(account="depotum")
    assert EbayOfferPreparation.objects.values().get() == before


def test_listing_update_applies_html_vat_and_keeps_ids(listing_api):
    from apps.marketplace.ebay.models import EbayOfferPreparation

    api = listing_api
    api.offer["tax"] = {"vatPercentage": 7.0, "applyTax": False}
    api.session.request.side_effect = [
        api.response(200, {"offers": [api.offer], "total": 1}),
        api.response(200, api.inventory),
        api.response(200, api.offer),
        api.response(204),
        api.response(200, api.inventory),
        api.response(200, api.offer),
        api.response(204),
    ]
    before_product = (
        type(api.draft.product).objects.values().get(pk=api.draft.product_id)
    )
    response = api.client.put(
        api.listing_url,
        {"confirm_update": True, "confirm_stock_location": True},
        format="json",
    )
    assert response.status_code == 200, response.data
    assert response.data["listing_id"] == "123456789012"
    assert response.data["offer_id"] == "9001"
    assert response.data["status"] == "published"
    calls = api.session.request.call_args_list
    assert [call.args[0] for call in calls] == [
        "GET",
        "GET",
        "GET",
        "PUT",
        "GET",
        "GET",
        "PUT",
    ]
    body = calls[-1].kwargs["json"]
    assert body["tax"] == {"vatPercentage": 19.0, "applyTax": False}
    assert '<div class="dep-listing"' in body["listingDescription"]
    assert "listing" not in body
    assert (
        "allocationByFormat"
        not in calls[3].kwargs["json"]["availability"]["shipToLocationAvailability"]
    )
    assert EbayOfferPreparation.objects.count() == 1
    assert (
        type(api.draft.product).objects.values().get(pk=api.draft.product_id)
        == before_product
    )


def test_listing_withdraw_keeps_resources_and_repeat_does_not_send(listing_api):
    from apps.marketplace.ebay.models import EbayOfferPreparation

    api = listing_api
    api.session.request.side_effect = [
        api.response(200, {"offers": [api.offer], "total": 1}),
        api.response(200, api.inventory),
        api.response(200, api.offer),
        api.response(200, {"listingId": "123456789012"}),
    ]
    response = api.client.delete(
        api.listing_url, {"confirm_withdrawal": True}, format="json"
    )
    assert response.status_code == 200, response.data
    assert response.data["status"] == "withdrawn"
    assert response.data["listing_id"] == "123456789012"
    calls = api.session.request.call_args_list
    assert [call.args[0] for call in calls] == ["GET", "GET", "GET", "POST"]
    assert calls[-1].args[1].endswith("/offer/9001/withdraw")
    assert EbayOfferPreparation.objects.get().offer_id == "9001"
    count = len(calls)
    assert (
        api.client.delete(
            api.listing_url, {"confirm_withdrawal": True}, format="json"
        ).status_code
        == 200
    )
    assert api.session.request.call_count == count
    assert (
        api.client.post(api.publish_url, api.confirmation, format="json").status_code
        == 409
    )


@pytest.mark.parametrize(
    "method,body",
    [
        ("put", {}),
        ("put", {"confirm_update": False, "confirm_stock_location": True}),
        ("delete", {}),
        ("delete", {"confirm_withdrawal": False}),
    ],
)
def test_listing_mutation_requires_confirmation(listing_api, method, body):
    api = listing_api
    assert (
        getattr(api.client, method)(api.listing_url, body, format="json").status_code
        == 400
    )
    api.session.request.assert_not_called()


@pytest.mark.parametrize("operation", ["get", "put", "delete"])
def test_listing_requires_manager(listing_api, draft_api, operation):
    api = listing_api
    api.client.force_authenticate(draft_api.seller)
    assert (
        getattr(api.client, operation)(api.listing_url, {}, format="json").status_code
        == 403
    )
    api.client.force_authenticate(None)
    assert api.client.get(api.listing_url).status_code in {401, 403}
    api.session.request.assert_not_called()


def test_listing_environment_isolation(listing_api, settings):
    api = listing_api
    settings.EBAY_ENVIRONMENT = "production"
    assert api.client.get(api.listing_url).status_code == 404
    assert (
        api.client.put(
            api.listing_url,
            {"confirm_update": True, "confirm_stock_location": True},
            format="json",
        ).status_code
        == 404
    )
    assert (
        api.client.delete(
            api.listing_url,
            {"confirm_withdrawal": True},
            format="json",
        ).status_code
        == 404
    )
    api.token.assert_not_called()
    api.session.request.assert_not_called()


def test_listing_get_can_inspect_missing_resources_without_clearing_uncertain(
    listing_api,
):
    from apps.marketplace.ebay.models import EbayOfferPreparation

    api = listing_api
    EbayOfferPreparation.objects.update(status="uncertain")
    api.session.request.side_effect = [api.response(404), api.response(404)]
    response = api.client.get(api.listing_url)
    assert response.status_code == 200
    assert response.data["offer"] is None and response.data["inventory_item"] is None
    assert response.data["remote_status"] is None
    assert EbayOfferPreparation.objects.get().status == "uncertain"


@pytest.mark.parametrize("failure", ["timeout", "rejection"])
def test_listing_withdraw_failure_does_not_report_success(listing_api, failure):
    import requests

    from apps.marketplace.ebay.models import EbayOfferPreparation

    api = listing_api
    api.session.request.side_effect = [
        api.response(200, {"offers": [api.offer], "total": 1}),
        api.response(200, api.inventory),
        api.response(200, api.offer),
        requests.Timeout()
        if failure == "timeout"
        else api.response(400, {"errors": [{"errorId": 25002}]}),
    ]
    assert (
        api.client.delete(
            api.listing_url, {"confirm_withdrawal": True}, format="json"
        ).status_code
        == 502
    )
    assert EbayOfferPreparation.objects.get().status == (
        "uncertain" if failure == "timeout" else "published"
    )
    if failure == "timeout":
        count = api.session.request.call_count
        assert (
            api.client.delete(
                api.listing_url, {"confirm_withdrawal": True}, format="json"
            ).status_code
            == 409
        )
        assert api.session.request.call_count == count


def test_listing_update_refuses_changed_listing_before_writes(listing_api):
    api = listing_api
    api.offer["listing"]["listingId"] = "external-relisting"
    api.session.request.side_effect = [
        api.response(200, {"offers": [api.offer], "total": 1}),
        api.response(200, api.inventory),
        api.response(200, api.offer),
    ]
    response = api.client.put(
        api.listing_url,
        {"confirm_update": True, "confirm_stock_location": True},
        format="json",
    )
    assert response.status_code == 409
    assert all(call.args[0] == "GET" for call in api.session.request.call_args_list)


def test_listing_partial_update_blocks_retry_and_preserves_hash(listing_api):
    from apps.marketplace.ebay.models import EbayOfferPreparation

    api = listing_api
    before_hash = EbayOfferPreparation.objects.get().payload_hash
    api.session.request.side_effect = [
        api.response(200, {"offers": [api.offer], "total": 1}),
        api.response(200, api.inventory),
        api.response(200, api.offer),
        api.response(204),
        api.response(200, api.inventory),
        api.response(200, api.offer),
        api.response(400, {"errors": [{"errorId": 2004}]}),
    ]
    response = api.client.put(
        api.listing_url,
        {"confirm_update": True, "confirm_stock_location": True},
        format="json",
    )
    assert response.status_code == 502
    preparation = EbayOfferPreparation.objects.get()
    assert preparation.status == "uncertain"
    assert preparation.payload_hash == before_hash
    assert "Inventory Item updated" in preparation.last_error


@pytest.fixture
def withdrawn_api(listing_api):
    """Снимает мок-листинг, сохраняя старый listingId и завершённые детали eBay."""
    api = listing_api
    api.session.request.side_effect = [
        api.response(200, {"offers": [deepcopy(api.offer)], "total": 1}),
        api.response(200, deepcopy(api.inventory)),
        api.response(200, deepcopy(api.offer)),
        api.response(200, {"listingId": "123456789012"}),
    ]
    response = api.client.delete(
        api.listing_url, {"confirm_withdrawal": True}, format="json"
    )
    assert response.status_code == 200, response.data
    api.offer["status"] = "UNPUBLISHED"
    api.offer["listing"]["listingStatus"] = "ENDED"
    api.session.request.reset_mock()

    def replies(result=None):
        return [
            api.response(200, {"offers": [deepcopy(api.offer)], "total": 1}),
            api.response(200, deepcopy(api.offer)),
            api.response(200, deepcopy(api.inventory)),
            api.response(200, {"offers": [deepcopy(api.offer)], "total": 1}),
            api.response(200, deepcopy(api.offer)),
            api.response(200, result or {"listingId": "new-depotum-listing"}),
        ]

    api.republish_replies = replies
    return api


def test_republish_uses_same_offer_new_listing_and_new_confirmation(withdrawn_api):
    from apps.marketplace.ebay.models import EbayOfferPreparation

    api = withdrawn_api
    # Подтверждение самой первой публикации больше не разрешает выставление.
    assert (
        api.client.post(api.publish_url, api.confirmation, format="json").status_code
        == 409
    )
    api.session.request.assert_not_called()
    api.session.request.side_effect = api.republish_replies()
    checks = api.client.get(api.publish_url)
    assert checks.status_code == 200 and checks.data["ready"] is True
    assert checks.data["preview_hash"] != api.confirmation["preview_hash"]
    confirmation = {**api.confirmation, "preview_hash": checks.data["preview_hash"]}
    api.session.request.reset_mock()
    api.session.request.side_effect = api.republish_replies()
    response = api.client.post(api.publish_url, confirmation, format="json")
    assert response.status_code == 200, response.data
    assert response.data["status"] == "published"
    assert response.data["listing_id"] == "new-depotum-listing"
    assert response.data["offer_id"] == "9001"
    posts = [
        call for call in api.session.request.call_args_list if call.args[0] == "POST"
    ]
    assert len(posts) == 1 and posts[0].args[1].endswith("/offer/9001/publish")
    assert EbayOfferPreparation.objects.count() == 1
    count = api.session.request.call_count
    assert (
        api.client.post(api.publish_url, confirmation, format="json").data["listing_id"]
        == "new-depotum-listing"
    )
    assert api.session.request.call_count == count


def test_republish_prepare_updates_withdrawn_offer_without_creating_another(
    withdrawn_api,
):
    from apps.marketplace.ebay.preview import build_draft_preview

    api = withdrawn_api
    api.draft.description += " Updated before relisting."
    api.draft.save(update_fields=["description"])
    preview = build_draft_preview(api.draft)
    api.session.request.side_effect = [
        api.response(200, {"offers": [deepcopy(api.offer)], "total": 1}),
        api.response(200, deepcopy(api.offer)),
        api.response(200, deepcopy(api.inventory)),
        api.response(204),
        api.response(200, {"offers": [deepcopy(api.offer)], "total": 1}),
        api.response(200, deepcopy(api.offer)),
        api.response(204),
    ]
    response = api.client.post(api.url, {"confirm_stock_location": True}, format="json")
    assert response.status_code == 200, response.data
    assert response.data["status"] == "prepared"
    assert response.data["offer_id"] == "9001"
    assert response.data["listing_id"] == "123456789012"
    assert all(
        call.args[0] in {"GET", "PUT"} for call in api.session.request.call_args_list
    )
    api.offer.update(deepcopy(preview["offer"]["body"]))
    api.inventory.update(deepcopy(preview["inventory_item"]["body"]))
    api.session.request.side_effect = api.republish_replies()
    checks = api.client.get(api.publish_url)
    assert checks.status_code == 200 and checks.data["ready"] is True


@pytest.mark.parametrize(
    "listing",
    [
        {"listingId": "123456789012", "listingStatus": "ACTIVE"},
        {"listingId": "foreign-listing", "listingStatus": "ENDED"},
    ],
)
def test_republish_rejects_active_or_foreign_history(withdrawn_api, listing):
    api = withdrawn_api
    api.offer["listing"] = listing
    api.session.request.side_effect = api.republish_replies()
    response = api.client.get(api.publish_url)
    assert response.status_code == 200 and response.data["ready"] is False
    assert all(call.args[0] == "GET" for call in api.session.request.call_args_list)


@pytest.mark.parametrize("failure", ["timeout", "same_id"])
def test_republish_uncertain_keeps_old_id_and_blocks_another_post(
    withdrawn_api, failure
):
    import requests

    from apps.marketplace.ebay.models import EbayOfferPreparation

    api = withdrawn_api
    api.session.request.side_effect = api.republish_replies()
    checks = api.client.get(api.publish_url)
    confirmation = {**api.confirmation, "preview_hash": checks.data["preview_hash"]}
    replies = api.republish_replies({"listingId": "123456789012"})
    if failure == "timeout":
        replies[-1] = requests.Timeout()
    api.session.request.side_effect = replies
    response = api.client.post(api.publish_url, confirmation, format="json")
    assert response.status_code == 502
    preparation = EbayOfferPreparation.objects.get()
    assert preparation.status == "uncertain"
    assert preparation.listing_id == "123456789012"
    count = api.session.request.call_count
    assert (
        api.client.post(api.publish_url, confirmation, format="json").status_code == 409
    )
    assert api.session.request.call_count == count
