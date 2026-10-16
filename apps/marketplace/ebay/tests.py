import json
import os
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.core.cache import cache
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.marketplace.ebay import catalog
from apps.marketplace.ebay.catalog_views import (
    EbayCategoriesView,
    EbayCategoryAspectsView,
)
from apps.marketplace.ebay.client import EbayAPIError, EbayConfigurationError
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
