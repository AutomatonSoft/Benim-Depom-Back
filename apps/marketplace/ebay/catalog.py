import hashlib
import json
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from rest_framework.exceptions import NotFound, ValidationError
from urllib3.util.retry import Retry

from apps.common.marketplace_http import build_marketplace_session

from .client import (
    EbayAPIError,
    EbayConfigurationError,
    api_base_url,
    get_seller_resource,
    token_request,
)

MARKETPLACE = "EBAY_DE"
CATALOG_CACHE_SECONDS = 86400


def _local_payload(relative_path):
    """Читает справочник Sandbox; изменение файла сразу меняет ключ кеша."""
    api_base_url()
    if settings.EBAY_ENVIRONMENT != "sandbox":
        return None
    path = Path(settings.BASE_DIR) / "data" / "ebay" / "sandbox" / relative_path
    try:
        stat = path.stat()
        digest = hashlib.sha256(str(path).encode()).hexdigest()[:16]
        key = f"ebay:local:v1:{digest}:{stat.st_mtime_ns}:{stat.st_size}"
        payload = cache.get(key)
        if payload is None:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(payload, dict)
                or payload.get("environment") != "sandbox"
                or payload.get("marketplace_id") != MARKETPLACE
                or not isinstance(payload.get("category_tree_id"), str)
                or not payload["category_tree_id"].isascii()
                or not payload["category_tree_id"].isdecimal()
                or not isinstance(payload.get("category_tree_version"), str)
                or not payload["category_tree_version"]
            ):
                raise ValueError("Invalid catalog metadata")
            cache.set(key, payload, timeout=CATALOG_CACHE_SECONDS)
        return payload
    except (OSError, UnicodeError, ValueError) as error:
        raise EbayConfigurationError(
            "Local eBay Sandbox catalog is missing or invalid. "
            "Check data/ebay/sandbox/."
        ) from error


def _cache_key(resource):
    """Разделяет справочники и токен по окружению и приложению eBay."""
    api_base_url()
    client = hashlib.sha256(settings.EBAY_CLIENT_ID.encode()).hexdigest()[:16]
    return f"ebay:catalog:{settings.EBAY_ENVIRONMENT}:{client}:{resource}"


def _application_token():
    """Получает токен приложения для справочников, не используя подключения продавцов."""
    key = _cache_key("application-token")
    token = cache.get(key)
    if token:
        return token
    payload = token_request(
        {
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope",
        }
    )
    token = payload.get("access_token")
    expires_in = payload.get("expires_in")
    if (
        not isinstance(token, str)
        or not token
        or type(expires_in) is not int
        or expires_in <= 0
    ):
        raise EbayAPIError("eBay returned an invalid application token response.")
    cache.set(key, token, timeout=max(1, expires_in - 60))
    return token


def _read_taxonomy(path, params=None):
    """Читает только заданный сервером путь Taxonomy с ограниченным ожиданием."""
    with build_marketplace_session() as session:
        for adapter in session.adapters.values():
            adapter.max_retries = Retry(total=0)
        return get_seller_resource(
            session=session,
            access_token=_application_token(),
            path=f"/commerce/taxonomy/v1/{path}",
            params=params,
        )


def _category_summary(node):
    """Проверяет узел eBay и оставляет краткие данные для выбора категории."""
    category = node.get("category")
    leaf = node.get("leafCategoryTreeNode", False)
    if not isinstance(category, dict) or type(leaf) is not bool:
        raise EbayAPIError("eBay returned an invalid category.")
    category_id = category.get("categoryId")
    name = category.get("categoryName")
    if (
        not isinstance(category_id, str)
        or not category_id.isascii()
        or not category_id.isdecimal()
        or not isinstance(name, str)
        or not name.strip()
    ):
        raise EbayAPIError("eBay returned an invalid category.")
    return {"id": category_id, "name": name, "is_leaf": leaf}


def _children(node):
    """Отклоняет повреждённые дочерние узлы вместо выдачи ложного пустого списка."""
    children = node.get("childCategoryTreeNodes", [])
    if not isinstance(children, list) or any(
        not isinstance(child, dict) for child in children
    ):
        raise EbayAPIError("eBay returned invalid child categories.")
    return children


def _category_tree():
    """Читает мебель Sandbox из JSON, сохраняя загрузку каталога Production через API."""
    local = _local_payload("categories.json")
    key = None
    if local is not None:
        tree_id = local["category_tree_id"]
        root = local.get("rootCategoryNode")
        if not isinstance(root, dict) or _category_summary(root)["id"] != "3197":
            raise EbayConfigurationError("Local eBay furniture category is invalid.")
        tree = {
            "categoryTreeId": tree_id,
            "categoryTreeVersion": local["category_tree_version"],
            "rootCategoryNode": {"childCategoryTreeNodes": [root]},
        }
    else:
        key = _cache_key("tree")
        tree = cache.get(key)
        if tree is not None:
            return tree
        identity = _read_taxonomy(
            "get_default_category_tree_id", {"marketplace_id": MARKETPLACE}
        )
        tree_id = identity.get("categoryTreeId")
        if (
            not isinstance(tree_id, str)
            or not tree_id.isascii()
            or not tree_id.isdecimal()
        ):
            raise EbayAPIError("eBay returned an invalid category tree identifier.")
        tree = _read_taxonomy(f"category_tree/{tree_id}")
    if (
        tree.get("categoryTreeId") != tree_id
        or not isinstance(tree.get("categoryTreeVersion"), str)
        or not tree["categoryTreeVersion"]
        or not isinstance(tree.get("rootCategoryNode"), dict)
    ):
        raise EbayAPIError("eBay returned an invalid category tree.")
    pending = _children(tree["rootCategoryNode"]).copy()
    if not pending:
        raise EbayAPIError("eBay returned an empty category tree.")
    while pending:
        node = pending.pop()
        _category_summary(node)
        children = _children(node)
        if not node.get("leafCategoryTreeNode", False) and not children:
            raise EbayAPIError("eBay returned an incomplete category tree.")
        pending.extend(children)
    if key is not None:
        cache.set(key, tree, timeout=CATALOG_CACHE_SECONDS)
    return tree


def _find_category(tree, category_id):
    """Ищет категорию в дереве текущего рынка без рекурсии и внешних URL."""
    pending = _children(tree["rootCategoryNode"]).copy()
    while pending:
        node = pending.pop()
        if node["category"]["categoryId"] == category_id:
            return node
        pending.extend(_children(node))
    raise NotFound("eBay category was not found in the EBAY_DE tree.")


def _tree_context(tree):
    return {
        "environment": settings.EBAY_ENVIRONMENT,
        "marketplace_id": MARKETPLACE,
        "category_tree_id": tree["categoryTreeId"],
        "category_tree_version": tree["categoryTreeVersion"],
    }


def get_categories(*, parent_id=None):
    """Возвращает один уровень категорий, не передавая клиенту всё дерево eBay."""
    tree = _category_tree()
    node = (
        _find_category(tree, parent_id)
        if parent_id is not None
        else tree["rootCategoryNode"]
    )
    return {
        **_tree_context(tree),
        "parent": _category_summary(node) if parent_id is not None else None,
        "categories": [_category_summary(child) for child in _children(node)],
    }


def get_category_aspects(*, category_id):
    """Читает характеристики конечной категории, сохраняя ограничения и значения eBay."""
    tree = _category_tree()
    category = _category_summary(_find_category(tree, category_id))
    if not category["is_leaf"]:
        raise ValidationError({"category_id": "Choose a leaf eBay category."})
    local = _local_payload(f"aspects/{category_id}.json")
    key = None
    if local is not None:
        if (
            local["category_tree_id"] != tree["categoryTreeId"]
            or local["category_tree_version"] != tree["categoryTreeVersion"]
            or local.get("category") != category
        ):
            raise EbayConfigurationError(
                "Local eBay category aspects do not match the tree."
            )
        aspects = local.get("aspects")
        if not isinstance(aspects, list):
            raise EbayConfigurationError("Local eBay category aspects are invalid.")
    else:
        key = _cache_key(
            f"aspects:{tree['categoryTreeId']}:{tree['categoryTreeVersion']}:{category_id}"
        )
        aspects = cache.get(key)
    cache_miss = aspects is None
    if cache_miss:
        payload = _read_taxonomy(
            f"category_tree/{tree['categoryTreeId']}/get_item_aspects_for_category",
            {"category_id": category_id},
        )
        aspects = payload.get("aspects")
        if not isinstance(aspects, list):
            raise EbayAPIError("eBay returned invalid category aspects.")
    for aspect in aspects:
        if (
            not isinstance(aspect, dict)
            or not isinstance(aspect.get("localizedAspectName"), str)
            or not aspect["localizedAspectName"].strip()
            or not isinstance(aspect.get("aspectConstraint"), dict)
            or type(aspect["aspectConstraint"].get("aspectRequired")) is not bool
        ):
            raise EbayAPIError("eBay returned invalid category aspect constraints.")
    if key is not None and cache_miss:
        cache.set(key, aspects, timeout=CATALOG_CACHE_SECONDS)
    return {
        **_tree_context(tree),
        "category": category,
        "aspects": aspects,
        "required_aspects": [
            aspect["localizedAspectName"]
            for aspect in aspects
            if aspect["aspectConstraint"]["aspectRequired"]
        ],
    }
