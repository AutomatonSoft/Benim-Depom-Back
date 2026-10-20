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
        url=f"/api/v1/ebay/drafts/jv/{product.pk}/",
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
    assert response.data["account"] == "jv"
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
    listing_settings_api.token.assert_called_once_with(account="jv")
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
        account="jv",
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
        "categoryId": "38208",
        "merchantLocationKey": "DE_88483",
        "listingDescription": preview_draft.description,
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


def test_preview_get_is_read_only_and_never_contacts_ebay(
    draft_api, preview_draft, monkeypatch
):
    """GET не создаёт черновик и не меняет товар, EAN или даты сохранённого черновика."""
    from apps.products.models import Product

    network = MagicMock(side_effect=AssertionError("Unexpected network request"))
    monkeypatch.setattr("requests.sessions.Session.request", network)
    url = draft_api.url + "preview/"
    assert draft_api.client.get(url).status_code == 404
    assert not EbayDraft.objects.exists()
    preview_draft.product = draft_api.product
    draft_api.product.ean_jv = "4071489789867"
    draft_api.product.save(update_fields=["ean_jv"])
    preview_draft.save()
    before_draft = EbayDraft.objects.values().get()
    before_product = Product.objects.filter(pk=draft_api.product.pk).values().get()
    response = draft_api.client.get(url)
    assert response.status_code == 200
    assert response.data["draft_complete"] is True
    assert response["Cache-Control"] == "no-store"
    assert EbayDraft.objects.values().get() == before_draft
    assert (
        Product.objects.filter(pk=draft_api.product.pk).values().get() == before_product
    )
    assert draft_api.client.post(url, {}, format="json").status_code == 405
    assert draft_api.client.put(url, {}, format="json").status_code == 405
    assert draft_api.client.patch(url, {}, format="json").status_code == 405
    assert draft_api.client.delete(url).status_code == 405
    network.assert_not_called()


def test_preview_isolates_environment_and_account(draft_api, preview_draft, settings):
    """Черновик Sandbox или другого аккаунта не показывается как Production JV."""
    draft_api.product.ean_jv = "4071489789867"
    draft_api.product.save(update_fields=["ean_jv"])
    preview_draft.product = draft_api.product
    preview_draft.save()
    url = draft_api.url + "preview/"
    settings.EBAY_ENVIRONMENT = "production"
    assert draft_api.client.get(url).status_code == 404
    draft = EbayDraft.objects.create(
        product=draft_api.product, environment="production", account="xl"
    )
    assert draft_api.client.get(url).status_code == 404
    draft.account = "jv"
    draft.save()
    response = draft_api.client.get(url)
    assert response.status_code == 200
    assert response.data["environment"] == "production"
    assert response.data["inventory_item"]["url"].startswith("https://api.ebay.com/")


def test_preview_requires_manager(draft_api):
    """Продавец и анонимный пользователь не получают даже содержимое предпросмотра."""
    url = draft_api.url + "preview/"
    draft_api.client.force_authenticate(draft_api.seller)
    assert draft_api.client.get(url).status_code == 403
    draft_api.client.force_authenticate(None)
    assert draft_api.client.get(url).status_code in {401, 403}


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


def test_prepare_rejects_changed_payload_after_success(preparation_api):
    api = preparation_api
    assert (
        api.client.post(
            api.url, {"confirm_stock_location": True}, format="json"
        ).status_code
        == 200
    )
    api.draft.title = "Changed title"
    api.draft.save(update_fields=["title"])
    assert (
        api.client.post(
            api.url, {"confirm_stock_location": True}, format="json"
        ).status_code
        == 409
    )
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
    api.update_url = api.url.removesuffix("prepare/") + "update/"
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


def test_update_requires_existing_offer(draft_api, preview_draft):
    preview_draft.product = draft_api.product
    preview_draft.save()
    assert (
        draft_api.client.post(
            draft_api.url + "update/", {"confirm_stock_location": True}, format="json"
        ).status_code
        == 409
    )


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
