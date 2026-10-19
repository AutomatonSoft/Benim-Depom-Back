from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class EbayConnection(models.Model):
    """Хранит подключение JV/XL/Depotum и зашифрованные токены с их сроками."""

    class Environment(models.TextChoices):
        SANDBOX = "sandbox", "Sandbox"
        PRODUCTION = "production", "Production"

    class Account(models.TextChoices):
        JV = "jv", "JV"
        XL = "xl", "XL"
        DEPOTUM = "depotum", "Depotum"

    environment = models.CharField(max_length=10, choices=Environment.choices)
    account = models.CharField(max_length=7, choices=Account.choices)
    ebay_user_id = models.CharField(max_length=255)
    access_token_encrypted = models.TextField()
    refresh_token_encrypted = models.TextField()
    access_token_expires_at = models.DateTimeField()
    refresh_token_expires_at = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("environment", "account"), name="ebay_connection_env_account"
            ),
            models.UniqueConstraint(
                fields=("environment", "ebay_user_id"), name="ebay_connection_env_user"
            ),
        ]


class EbayOAuthAttempt(models.Model):
    """Связывает одноразовый OAuth state с менеджером, аккаунтом и окружением."""

    state_hash = models.CharField(max_length=64, primary_key=True)
    environment = models.CharField(
        max_length=10, choices=EbayConnection.Environment.choices
    )
    account = models.CharField(max_length=7, choices=EbayConnection.Account.choices)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    expires_at = models.DateTimeField(db_index=True)


class EbayDraft(models.Model):
    """Хранит независимый черновик eBay, не изменяя товар, EAN и другие листинги."""

    product = models.ForeignKey(
        "products.Product", on_delete=models.CASCADE, related_name="+"
    )
    environment = models.CharField(
        max_length=10, choices=EbayConnection.Environment.choices
    )
    account = models.CharField(
        max_length=7, choices=EbayConnection.Account.choices, default="jv"
    )
    title = models.CharField(max_length=80, blank=True)
    description = models.TextField(blank=True)
    image_urls = models.JSONField(default=list, blank=True)
    price_eur = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    quantity = models.PositiveIntegerField(null=True, blank=True)
    category_id = models.CharField(max_length=20, blank=True)
    aspects = models.JSONField(default=dict, blank=True)
    category_tree_id = models.CharField(max_length=20, blank=True)
    category_tree_version = models.CharField(max_length=255, blank=True)
    required_aspects = models.JSONField(default=list, blank=True)
    fulfillment_policy_id = models.CharField(max_length=64, blank=True)
    payment_policy_id = models.CharField(max_length=64, blank=True)
    return_policy_id = models.CharField(max_length=64, blank=True)
    merchant_location_key = models.CharField(max_length=50, blank=True)
    regulatory = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("product", "environment", "account"),
                name="ebay_draft_product_env_account",
            )
        ]


class EbayOfferPreparation(models.Model):
    """Фиксирует владение SKU и результат отправки без публикации и изменения товара."""

    class Status(models.TextChoices):
        PROCESSING = "processing", "Processing"
        PREPARED = "prepared", "Prepared"
        FAILED = "failed", "Failed"
        UNCERTAIN = "uncertain", "Requires reconciliation"

    draft = models.OneToOneField(
        EbayDraft, on_delete=models.CASCADE, related_name="preparation"
    )
    environment = models.CharField(
        max_length=10, choices=EbayConnection.Environment.choices
    )
    account = models.CharField(max_length=7, choices=EbayConnection.Account.choices)
    sku = models.CharField(max_length=14)
    payload_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=10, choices=Status.choices)
    inventory_confirmed = models.BooleanField(default=False)
    offer_id = models.CharField(max_length=64, blank=True)
    last_error = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("environment", "account", "sku"),
                name="ebay_preparation_env_account_sku",
            )
        ]
