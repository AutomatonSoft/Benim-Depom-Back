from django.db import models


class HoodProductSnapshot(models.Model):
    class Account(models.TextChoices):
        JV = "jv", "JV"
        XL = "xl", "XL"

    product = models.ForeignKey(
        "products.Product",
        on_delete=models.CASCADE,
        related_name="hood_snapshots",
    )
    account = models.CharField(max_length=2, choices=Account.choices)
    ean = models.CharField(max_length=14, db_index=True)
    payload = models.JSONField(default=dict)
    fetched_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = (
            models.UniqueConstraint(
                fields=("product", "account"),
                name="unique_hood_snapshot_product_account",
            ),
        )
