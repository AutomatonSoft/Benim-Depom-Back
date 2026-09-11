from django.db import models


class AfterbuyOrder(models.Model):
    class Account(models.TextChoices):
        JV = "jv", "JV"
        XL = "xl", "XL"

    class Marketplace(models.TextChoices):
        OTTO = "otto", "OTTO"
        HOOD = "hood", "Hood"
        KAUFLAND = "kaufland", "Kaufland"
        OTHER = "other", "Other"

    account = models.CharField(max_length=2, choices=Account.choices, db_index=True)
    afterbuy_order_id = models.CharField(max_length=32, db_index=True)
    marketplace = models.CharField(
        max_length=16,
        choices=Marketplace.choices,
        default=Marketplace.OTHER,
        db_index=True,
    )
    paid_at = models.DateTimeField(null=True, blank=True, db_index=True)
    total_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    currency = models.CharField(max_length=8, default="EUR")
    payment_method = models.CharField(max_length=80, blank=True)
    platform_order_number = models.CharField(max_length=80, blank=True)
    buyer_name = models.CharField(max_length=160, blank=True)
    buyer_email = models.CharField(max_length=254, blank=True)
    buyer_platform_user_id = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("account", "afterbuy_order_id"),
                name="afterbuy_order_account_id_uniq",
            )
        ]

    def __str__(self) -> str:
        return f"{self.account} {self.afterbuy_order_id}"


class AfterbuyOrderItem(models.Model):
    order = models.ForeignKey(
        AfterbuyOrder,
        on_delete=models.CASCADE,
        related_name="items",
    )
    item_key = models.CharField(max_length=80)
    afterbuy_item_id = models.CharField(max_length=32, blank=True)
    anr = models.CharField(max_length=64, blank=True)
    product_id = models.CharField(max_length=64, blank=True)
    sku = models.CharField(max_length=64, blank=True)
    ean = models.CharField(max_length=14, blank=True)
    title = models.CharField(max_length=255, blank=True)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    platform_name = models.CharField(max_length=80, blank=True)
    marketplace = models.CharField(
        max_length=16,
        choices=AfterbuyOrder.Marketplace.choices,
        default=AfterbuyOrder.Marketplace.OTHER,
        db_index=True,
    )
    platform_order_number = models.CharField(max_length=80, blank=True)
    extra_tags = models.JSONField(default=dict, blank=True)
    matched_product = models.ForeignKey(
        "products.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="afterbuy_order_items",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("order", "item_key"),
                name="afterbuy_order_item_key_uniq",
            )
        ]

    def __str__(self) -> str:
        return self.title or self.item_key


class AfterbuySaleNotification(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        SKIPPED = "skipped", "Skipped"

    order_item = models.OneToOneField(
        AfterbuyOrderItem,
        on_delete=models.CASCADE,
        related_name="sale_notification",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.order_item_id} {self.status}"
