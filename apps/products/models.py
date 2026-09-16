from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models


def product_image_upload_to(instance, filename: str) -> str:
    extension = Path(filename).suffix.lower()
    return f"products/{instance.product.owner_id}/{uuid4().hex}{extension}"


def generated_image_upload_to(instance, filename: str) -> str:
    extension = Path(filename).suffix.lower() or ".jpg"
    owner_id = instance.source_image.product.owner_id
    return f"products/{owner_id}/generated/{uuid4().hex}{extension}"


class Product(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        WITHDRAWN = "withdrawn", "Withdrawn"
        ARCHIVED = "archived", "Archived"
        DEACTIVATED = "deactivated", "Deactivated"

    class Currency(models.TextChoices):
        TRY = "TRY", "Turkish lira"
        EUR = "EUR", "Euro"
        USD = "USD", "US dollar"

    class WarehouseCity(models.TextChoices):
        IST = "IST", "Istanbul"
        ANK = "ANK", "Ankara"
        IZM = "IZM", "Izmir"
        BUR = "BUR", "Bursa"
        KSY = "KSY", "Kars"
        INE = "INE", "Inegol"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="products",
    )
    title = models.CharField(max_length=255)
    # A free-form type entered by the seller. The mobile UI may suggest values
    # in the selected language, but the backend does not maintain a type catalog.
    product_type = models.CharField(max_length=255, db_index=True)
    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    currency = models.CharField(
        max_length=3,
        choices=Currency.choices,
        default=Currency.TRY,
    )
    warehouse_city = models.CharField(
        max_length=3, choices=WarehouseCity.choices, default=WarehouseCity.INE
    )
    listing_price_eur_override = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    pricing_overrides = models.JSONField(default=dict, blank=True)
    otto_category_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        db_index=True,
    )
    otto_category_group_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        db_index=True,
    )
    otto_category_name = models.CharField(
        max_length=255,
        blank=True,
    )
    otto_category_group_name = models.CharField(
        max_length=255,
        blank=True,
    )
    otto_attributes = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    ean_jv = models.CharField(
        max_length=14,
        blank=True,
        validators=[
            RegexValidator(
                regex=r"^\d{8,14}$",
                message="Use an EAN containing 8 to 14 digits.",
            )
        ],
        db_index=True,
    )
    ean_xl = models.CharField(
        max_length=14,
        blank=True,
        validators=[
            RegexValidator(
                regex=r"^\d{8,14}$",
                message="Use an EAN containing 8 to 14 digits.",
            )
        ],
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
    deactivation_requested_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    deactivated_at = models.DateTimeField(null=True, blank=True)
    catalog_revision = models.PositiveIntegerField(default=1)
    pending_changes = models.JSONField(default=dict, blank=True)
    pending_changes_submitted_at = models.DateTimeField(null=True, blank=True)
    # Last rejected pending request, or the snapshot from a rejected/withdrawn
    # resubmission — so managers still see seller comment + field diffs on the card.
    seller_change_review = models.JSONField(default=dict, blank=True)

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


class PriceNegotiation(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"
        SUPERSEDED = "superseded", "Superseded"

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="price_negotiations",
    )
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="price_negotiations",
    )
    currency = models.CharField(max_length=3, choices=Product.Currency.choices)
    current_unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    proposed_unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    message = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    seller_comment = models.TextField(blank=True, default="")
    responded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(
                fields=("product", "status", "-created_at"),
                name="price_neg_product_status_idx",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"Price negotiation #{self.pk} for product {self.product_id} "
            f"({self.status})"
        )


class ProductVariant(models.Model):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="variants",
    )
    # The mobile app sends a free-form colour name (Turkish, English, etc.).
    # German marketplace colour lives on the listing after AI translation.
    color = models.CharField(max_length=80, db_index=True)
    materials = models.JSONField(default=list)
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
        return f"{self.product.title} — {self.color} / {', '.join(self.materials)}"


class ProductImage(models.Model):
    class ProcessingStatus(models.TextChoices):
        IDLE = "idle", "Idle"
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        RESULT_RECEIVED = "result_received", "Result received"

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
        default=ProcessingStatus.IDLE,
    )
    processing_error = models.TextField(blank=True)
    processing_result = models.JSONField(
        default=dict,
        blank=True,
    )
    processing_claimed_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
    )
    processing_finished_at = models.DateTimeField(
        null=True,
        blank=True,
    )
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


class ProductGeneratedImage(models.Model):
    class Mode(models.TextChoices):
        WHITE = "white", "White background"
        INTERIOR = "interior", "Interior"
        HUMAN = "human", "Human"

    source_image = models.ForeignKey(
        ProductImage,
        on_delete=models.CASCADE,
        related_name="generated_images",
    )
    mode = models.CharField(
        max_length=20,
        choices=Mode.choices,
    )
    image = models.ImageField(upload_to=generated_image_upload_to)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("mode",)
        constraints = [
            models.UniqueConstraint(
                fields=("source_image", "mode"),
                name="unique_generated_image_mode",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.source_image_id}: {self.mode}"


class ExchangeRate(models.Model):
    as_of = models.DateField()
    eur_to_usd = models.DecimalField(max_digits=12, decimal_places=6)
    eur_to_try = models.DecimalField(max_digits=12, decimal_places=6)
    source = models.CharField(max_length=32, default="frankfurter")
    fetched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-fetched_at",)

    def __str__(self) -> str:
        return f"{self.as_of} EUR→USD {self.eur_to_usd} EUR→TRY {self.eur_to_try}"
