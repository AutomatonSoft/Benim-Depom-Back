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
from apps.marketplace.ebay.client import EbayAPIError
from apps.marketplace.ebay.readiness import _policy_summary


@pytest.fixture
def catalog_api(settings, monkeypatch):
    """Подменяет только eBay и кеш, чтобы проверять каталог без сети и базы данных."""
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }
    settings.EBAY_ENVIRONMENT = "sandbox"
    settings.EBAY_CLIENT_ID = "catalog-test-client"
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
