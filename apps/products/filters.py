from rest_framework.exceptions import ValidationError

from .models import Product


ALLOWED_ORDERING = {
    "created_at",
    "-created_at",
    "updated_at",
    "-updated_at",
    "title",
    "-title",
}


def _get_integer(query_params, name: str) -> int | None:
    value = query_params.get(name)

    if value in (None, ""):
        return None

    try:
        return int(value)
    except ValueError as error:
        raise ValidationError(
            {name: "This value must be an integer."}
        ) from error


def filter_products(*, queryset, query_params):
    search = query_params.get("search", "").strip()

    if search:
        queryset = queryset.filter(title__icontains=search)

    status_value = query_params.get("status")

    if status_value:
        valid_statuses = set(Product.Status.values)

        if status_value not in valid_statuses:
            raise ValidationError(
                {"status": "Unknown product status."}
            )

        queryset = queryset.filter(status=status_value)

    product_type_id = _get_integer(query_params, "product_type")
    if product_type_id is not None:
        queryset = queryset.filter(product_type_id=product_type_id)

    category_id = _get_integer(query_params, "category")
    if category_id is not None:
        queryset = queryset.filter(category_id=category_id)

    color_hex = query_params.get("color_hex", "").strip()
    if color_hex:
        if len(color_hex) != 7 or not color_hex.startswith("#"):
            raise ValidationError(
                {"color_hex": "Use a hexadecimal color in the #RRGGBB format."}
            )

        try:
            int(color_hex[1:], 16)
        except ValueError as error:
            raise ValidationError(
                {"color_hex": "Use a hexadecimal color in the #RRGGBB format."}
            ) from error

        queryset = queryset.filter(variants__color_hex__iexact=color_hex)

    material = query_params.get("material", "").strip()
    if material:
        queryset = queryset.filter(variants__materials__contains=[material])

    is_available = query_params.get("is_available")
    if is_available is not None:
        values = {
            "true": True,
            "false": False,
        }

        normalized_value = is_available.lower()

        if normalized_value not in values:
            raise ValidationError(
                {
                    "is_available": (
                        "Use true or false."
                    )
                }
            )

        queryset = queryset.filter(
            is_available=values[normalized_value]
        )

    ordering = query_params.get("ordering", "-created_at")

    if ordering not in ALLOWED_ORDERING:
        raise ValidationError(
            {
                "ordering": (
                    "Allowed values: "
                    f"{', '.join(sorted(ALLOWED_ORDERING))}."
                )
            }
        )

    return queryset.order_by(ordering).distinct()
