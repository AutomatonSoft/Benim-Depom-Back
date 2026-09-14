from apps.products.models import Product

from .models import AfterbuyOrder
from .types import NormalizedSoldItem


def _digits(value: str) -> str:
    return "".join(character for character in value if character.isdigit())


def match_catalog_product(
    *,
    account: str,
    item: NormalizedSoldItem,
) -> Product | None:
    ean = _digits(item.ean)
    if len(ean) < 8:
        return None

    field = "ean_jv" if account == AfterbuyOrder.Account.JV else "ean_xl"
    return (
        Product.objects.filter(**{field: ean})
        .exclude(status=Product.Status.ARCHIVED)
        .order_by("id")
        .first()
    )
