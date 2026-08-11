from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


def product_image_upload_to(instance, filename: str) -> str:
    extension = Path(filename).suffix.lower()
    return f"products/{instance.product.owner_id}/{uuid4().hex}{extension}"


class Product(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        UNDER_REVIEW = "under_review", "Under review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        ARCHIVED = "archived", "Archived"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="products",
    )
    title = models.CharField(max_length=255)
    product_type = models.ForeignKey(
        "catalog.ProductType",
        on_delete=models.PROTECT,
        related_name="products",
    )
    category = models.ForeignKey(
        "catalog.Category",
        on_delete=models.SET_NULL,
        related_name="products",
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )

    is_available = models.BooleanField(default=True)

    approved_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
    )
    availability_reminder_sent_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    
    availability_confirmed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(
                fields=("owner", "status", "created_at"),
                name="product_owner_status_idx",
            ),
            models.Index(
                fields=("status", "created_at"),
                name="product_status_date_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.owner.username})"


class ProductVariant(models.Model):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="variants",
    )
    color = models.ForeignKey(
        "catalog.Color",
        on_delete=models.PROTECT,
        related_name="product_variants",
    )
    material = models.ForeignKey(
        "catalog.Material",
        on_delete=models.PROTECT,
        related_name="product_variants",
    )
    width_cm = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    height_cm = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    length_cm = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    quantity = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
    )

    class Meta:
        ordering = ("id",)

    def __str__(self) -> str:
        return f"{self.product.title} — {self.color} / {self.material}"


class ProductImage(models.Model):
    class ProcessingStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="images",
    )
    image = models.ImageField(upload_to=product_image_upload_to)
    processed_image = models.ImageField(
        upload_to=product_image_upload_to,
        blank=True,
        null=True,
    )
    position = models.PositiveSmallIntegerField(default=0)
    is_primary = models.BooleanField(default=False)
    processing_status = models.CharField(
        max_length=20,
        choices=ProcessingStatus.choices,
        default=ProcessingStatus.PENDING,
    )
    processing_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("position", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("product", "position"),
                name="unique_product_image_position",
            ),
            models.UniqueConstraint(
                fields=("product",),
                condition=models.Q(is_primary=True),
                name="unique_primary_product_image",
            ),
        ]

    def __str__(self) -> str:
        return f"Image #{self.id} for {self.product.title}"