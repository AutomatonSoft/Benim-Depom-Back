from django.conf import settings
from django.db import models

from apps.products.models import Product


class ModerationDecision(models.Model):
    class Decision(models.TextChoices):
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="moderation_decisions",
    )
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="moderation_decisions",
    )
    decision = models.CharField(
        max_length=20,
        choices=Decision.choices,
    )
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.product_id}: {self.decision}"