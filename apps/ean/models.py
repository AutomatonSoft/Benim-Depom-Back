from django.conf import settings
from django.db import models


class EanCode(models.Model):
    class Account(models.TextChoices):
        JV = "jv", "JV"
        XL = "xl", "XL"

    code = models.CharField(max_length=14, unique=True)
    account = models.CharField(max_length=2, choices=Account.choices)
    product = models.ForeignKey(
        "products.Product",
        on_delete=models.PROTECT,
        related_name="ean_codes",
        null=True,
        blank=True,
    )
    imported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="imported_ean_codes",
        null=True,
        blank=True,
    )
    imported_at = models.DateTimeField(auto_now_add=True)
    assigned_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("id",)
        indexes = [
            models.Index(
                fields=("account", "product"),
                name="ean_account_product_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.account}: {self.code}"
