import uuid

from django.conf import settings
from django.db import models


class MarketplaceJob(models.Model):
    class Operation(models.TextChoices):
        SEARCH = "search", "Search"
        PUBLISH = "publish", "Publish"
        UPDATE = "update", "Update"
        DELETE = "delete", "Delete"
        DEACTIVATE = "deactivate", "Deactivate"
        ACTIVATE = "activate", "Activate"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        PENDING_CONFIRMATION = "pending_confirmation", "Pending confirmation"
        SUCCEEDED = "succeeded", "Succeeded"
        PARTIAL = "partial", "Partial"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(
        "products.Product",
        on_delete=models.PROTECT,
        related_name="marketplace_jobs",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="marketplace_jobs",
    )
    operation = models.CharField(max_length=16, choices=Operation.choices)
    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.QUEUED,
        db_index=True,
    )
    request_id = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)
    requested_channels = models.JSONField(default=list)
    request_payload = models.JSONField(default=dict)
    results = models.JSONField(default=list)
    error = models.JSONField(default=dict)
    celery_task_id = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = (
            models.Index(
                fields=("product", "created_at"), name="marketplace_job_product_idx"
            ),
            models.Index(
                fields=("status", "created_at"), name="marketplace_job_status_idx"
            ),
        )


class MarketplacePublication(models.Model):
    """Displays product statuses on marketplaces."""

    class Marketplace(models.TextChoices):
        OTTO = "otto", "OTTO"
        HOOD = "hood", "Hood"
        KAUFLAND = "kaufland", "Kaufland"

    class Account(models.TextChoices):
        JV = "jv", "JV"
        XL = "xl", "XL"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PUBLISHING = "publishing", "Publishing"
        ACTIVE = "active", "Active"
        DEACTIVATING = "deactivating", "Deactivating"
        DEACTIVATED = "deactivated", "Deactivated"
        DELETING = "deleting", "Deleting"
        DELETED = "deleted", "Deleted"
        FAILED = "failed", "Failed"

    status_before_operation = models.CharField(
        max_length=16,
        choices=Status.choices,
        blank=True,
        default="",
        help_text=(
            "Stable status before an in-progress marketplace operation. "
            "Used to restore the listing if the external request fails."
        ),
    )

    product = models.ForeignKey(
        "products.Product",
        on_delete=models.PROTECT,
        related_name="marketplace_publications",
    )
    marketplace = models.CharField(
        max_length=16,
        choices=Marketplace.choices,
    )
    account = models.CharField(
        max_length=2,
        choices=Account.choices,
    )
    ean = models.CharField(
        max_length=14,
        db_index=True,
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    external_id = models.CharField(
        max_length=255,
        blank=True,
    )
    external_reference = models.CharField(
        max_length=255,
        blank=True,
    )

    last_job = models.ForeignKey(
        MarketplaceJob,
        on_delete=models.SET_NULL,
        related_name="publications",
        null=True,
        blank=True,
    )
    attempt_count = models.PositiveIntegerField(default=0)
    last_request = models.JSONField(default=dict, blank=True)
    last_response = models.JSONField(default=dict, blank=True)
    last_error = models.JSONField(default=dict, blank=True)

    published_at = models.DateTimeField(null=True, blank=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("marketplace", "account", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("product", "marketplace", "account"),
                name="unique_product_marketplace_account",
            ),
        ]
        indexes = [
            models.Index(
                fields=("product", "status"),
                name="market_pub_product_state_idx",
            ),
            models.Index(
                fields=("marketplace", "account", "status"),
                name="market_pub_channel_state_idx",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.product_id} / {self.marketplace} / {self.account} / {self.status}"
        )


class MarketplaceListingConfiguration(models.Model):
    """Manager-owned marketplace content prepared before publication.

    A configuration deliberately exists separately from ``MarketplacePublication``:
    a manager may prepare and review marketplace content while a product is still
    under moderation and therefore has no assigned EAN or external listing yet.
    """

    product = models.ForeignKey(
        "products.Product",
        on_delete=models.PROTECT,
        related_name="marketplace_listing_configurations",
    )
    marketplace = models.CharField(
        max_length=16,
        choices=MarketplacePublication.Marketplace.choices,
    )
    account = models.CharField(
        max_length=2,
        choices=MarketplacePublication.Account.choices,
    )
    configuration = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("marketplace", "account", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("product", "marketplace", "account"),
                name="unique_product_marketplace_listing_config",
            ),
        ]
        indexes = [
            models.Index(
                fields=("product", "marketplace", "account"),
                name="market_cfg_product_channel_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.product_id} / {self.marketplace} / {self.account}"


class MarketplaceContentGeneration(models.Model):
    """Async AI generation request for manager-editable marketplace content"""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        PARTIAL = "partial", "Partial"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(
        "products.Product",
        on_delete=models.PROTECT,
        related_name="content_generation_jobs",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="content_generation_jobs",
    )

    # Например:
    # [{"marketplace": "otto", "account": "jv"}]
    targets = models.JSONField(default=list)

    language = models.CharField(max_length=8, default="de")

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.QUEUED,
        db_index=True,
    )

    # Снимок данных товара на момент запуска:
    # название, тип, размеры, материалы, цвет, цена, категория и т.д.
    # Нужен для аудита и чтобы результат был воспроизводимым.
    input_snapshot = models.JSONField(default=dict)

    # Структурированный ответ AI, еще не применённый к configuration
    result = models.JSONField(default=dict, blank=True)

    error = models.JSONField(default=dict, blank=True)

    model = models.CharField(max_length=100, blank=True)

    celery_task_id = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = (
            models.Index(
                fields=("product", "created_at"),
                name="content_gen_product_idx",
            ),
            models.Index(
                fields=("status", "created_at"), name="content_gen_status_idx"
            ),
        )

    def __str__(self) -> str:
        return f"{self.product_id} / {self.language} /{self.status} / {self.id}"
