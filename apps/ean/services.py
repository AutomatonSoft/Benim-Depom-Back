from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import EanCode
from .serializers import is_valid_gtin


def import_ean_codes(*, account: str, raw_codes: str, imported_by) -> dict:
    seen_codes: set[str] = set()
    valid_codes: list[str] = []
    duplicate_input_count = 0
    invalid_codes: list[str] = []

    for line in raw_codes.splitlines():
        code = line.strip().replace(" ", "")

        if not code:
            continue

        if code in seen_codes:
            duplicate_input_count += 1
            continue

        seen_codes.add(code)

        if not is_valid_gtin(code):
            invalid_codes.append(code)
            continue

        valid_codes.append(code)

    existing_codes = set(
        EanCode.objects.filter(code__in=valid_codes).values_list(
            "code",
            flat=True,
        )
    )
    new_codes = [code for code in valid_codes if code not in existing_codes]

    EanCode.objects.bulk_create(
        [
            EanCode(
                code=code,
                account=account,
                imported_by=imported_by,
            )
            for code in new_codes
        ],
        batch_size=1000,
    )

    return {
        "created_count": len(new_codes),
        "already_exists_count": len(existing_codes),
        "duplicate_input_count": duplicate_input_count,
        "invalid_count": len(invalid_codes),
        "invalid_codes": invalid_codes[:50],
    }


@transaction.atomic
def assign_ean_codes_to_product(*, product) -> list[EanCode]:
    if product.ean_jv and product.ean_xl:
        return list(
            EanCode.objects.filter(
                code__in=(product.ean_jv, product.ean_xl)
            ).order_by("account")
        )

    assigned_codes: list[EanCode] = []

    for account in (EanCode.Account.JV, EanCode.Account.XL):
        ean_code = (
            EanCode.objects.select_for_update(skip_locked=True)
            .filter(
                account=account,
                state=EanCode.State.AVAILABLE,
                product__isnull=True,
            )
            .order_by("id")
            .first()
        )

        if ean_code is None:
            raise ValidationError(
                {
                    "ean": (
                        f"No free EAN codes remain for account '{account}'. "
                        "Ask a manager to import more codes."
                    )
                }
            )

        ean_code.product = product
        ean_code.state = EanCode.State.RESERVED
        ean_code.assigned_at = timezone.now()
        ean_code.save(update_fields=("product", "state", "assigned_at"))
        assigned_codes.append(ean_code)

    assigned_by_account = {
        ean_code.account: ean_code.code
        for ean_code in assigned_codes
    }
    product.ean_jv = assigned_by_account[EanCode.Account.JV]
    product.ean_xl = assigned_by_account[EanCode.Account.XL]
    product.save(update_fields=("ean_jv", "ean_xl", "updated_at"))

    return assigned_codes

@transaction.atomic
def consume_ean_code(*, product, account: str) -> bool:
    """
    Делает EAN одноразовым после первой успешной публикации.

    Возвращает True, если код был впервые помечен использованным.
    """
    expected_code = (
        product.ean_jv
        if account == EanCode.Account.JV
        else product.ean_xl
    )

    if not expected_code:
        raise ValidationError(
            {"ean": f"Product has no EAN for account '{account}'."}
        )

    ean_code = EanCode.objects.select_for_update().get(
        product=product,
        account=account,
        code=expected_code,
    )

    if ean_code.state == EanCode.State.CONSUMED:
        return False

    if ean_code.state != EanCode.State.RESERVED:
        raise ValidationError(
            {
                "ean": (
                    f"EAN '{ean_code.code}' has invalid state "
                    f"'{ean_code.state}'."
                )
            }
        )

    ean_code.state = EanCode.State.CONSUMED
    ean_code.consumed_at = timezone.now()
    ean_code.save(update_fields=("state", "consumed_at"))

    return True



def get_ean_summary() -> dict:
    accounts = []
    threshold = settings.EAN_LOW_STOCK_THRESHOLD

    for account in EanCode.Account.values:
        available_count = EanCode.objects.filter(
            account=account,
            state=EanCode.State.AVAILABLE,
        ).count()
        accounts.append(
            {
                "account": account,
                "available_count": available_count,
                "is_low": available_count <= threshold,
            }
        )

    return {
        "low_stock_threshold": threshold,
        "accounts": accounts,
        "requires_attention": any(item["is_low"] for item in accounts),
    }
