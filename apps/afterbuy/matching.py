from apps.products.models import Product

from .models import AfterbuyOrder
from .types import NormalizedSoldItem


def _digits(value: str) -> str:
    """Оставляет только цифры: 'SKU 4062-292' → '4062292'."""
    return "".join(character for character in value if character.isdigit())


def _is_ean_like(value: str) -> bool:
    """Наш EAN на площадку — 8–14 цифр, как в Product.ean_jv / ean_xl."""
    return 8 <= len(value) <= 14


def catalog_ean_candidates(item: NormalizedSoldItem) -> list[str]:
    """
    Возможные EAN из строки Afterbuy, без дублей, в порядке надёжности.

    SKU первый: на OTTO/Hood/Kaufland мы выставляем SKU = EAN.
    """
    raw_values = [
        item.sku,
        item.ean,
        item.anr,
        item.alternative_item_number,
        item.product_id,
        *item.extra_tags.values(),
    ]
    seen: set[str] = set()
    candidates: list[str] = []
    for raw in raw_values:
        ean = _digits(raw)
        if not _is_ean_like(ean) or ean in seen:
            continue
        seen.add(ean)
        candidates.append(ean)
    return candidates


def match_catalog_product(
    *,
    account: str,
    item: NormalizedSoldItem,
) -> Product | None:
    """
    Находит наш товар по EAN аккаунта JV/XL.

    Не списывает склад и не шлёт пуш — только связь Afterbuy-строки с Product.
    """
    candidates = catalog_ean_candidates(item)
    if not candidates:
        return None

    field = "ean_jv" if account == AfterbuyOrder.Account.JV else "ean_xl"
    return (
        Product.objects.filter(**{f"{field}__in": candidates})
        .exclude(status=Product.Status.ARCHIVED)
        .order_by("id")
        .first()
    )