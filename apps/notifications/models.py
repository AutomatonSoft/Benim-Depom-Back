from django.conf import settings
from django.db import models


class DeviceToken(models.Model):
    class Platform(models.TextChoices):
        ANDROID = "android", "Android"
        IOS = "ios", "iOS"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="device_tokens",
    )
    token = models.CharField(max_length=512, unique=True)
    platform = models.CharField(
        max_length=20,
        choices=Platform.choices,
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-last_seen_at",)

    def __str__(self) -> str:
        return f"{self.user.username} — {self.platform}"


class Notification(models.Model):
    class Type(models.TextChoices):
        PRODUCT_APPROVED = "product_approved", "Product approved"
        PRODUCT_REJECTED = "product_rejected", "Product rejected"
        PRODUCT_CONFIRMATION = "product_confirmation", "Product confirmation"
        IMAGE_PROCESSING_COMPLETED = (
            "image_processing_completed",
            "Image processing completed",
        )
        IMAGE_PROCESSING_FAILED = (
            "image_processing_failed",
            "Image processing failed",
        )
        MESSAGE_RECEIVED = "message_received", "Message received"
        PRODUCT_AVAILABILITY_REMINDER = (
            "product_availability_reminder",
            "Product availability reminder",
        )
        MANAGER_MESSAGE = "manager_message", "Manager message"
        PRODUCT_DEACTIVATED = "product_deactivated", "Product deactivated"
        PRODUCT_DEACTIVATION_REQUESTED = (
            "product_deactivation_requested",
            "Product deactivation requested",
        )
        PRODUCT_SUBMITTED_FOR_REVIEW = (
            "product_submitted_for_review",
            "Product submitted for review",
        )
        PRODUCT_WITHDRAWN_FROM_REVIEW = (
            "product_withdrawn_from_review",
            "Product withdrawn from review",
        )
        PRODUCT_CHANGE_REQUESTED = (
            "product_change_requested",
            "Product change requested",
        )
        PRICE_NEGOTIATION_OFFER = (
            "price_negotiation_offer",
            "Price negotiation offer",
        )
        PRICE_NEGOTIATION_RESPONSE = (
            "price_negotiation_response",
            "Price negotiation response",
        )
        PRODUCT_SOLD = "product_sold", "Product sold"

    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="sent_notifications",
        null=True,
        blank=True,
    )

    title = models.CharField(max_length=150, blank=True, default="")
    body = models.TextField(blank=True, default="")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    product = models.ForeignKey(
        "products.Product",
        on_delete=models.SET_NULL,
        related_name="notifications",
        null=True,
        blank=True,
    )
    notification_type = models.CharField(
        max_length=50,
        choices=Type.choices,
    )
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    price_negotiation = models.ForeignKey(
        "products.PriceNegotiation",
        on_delete=models.SET_NULL,
        related_name="notifications",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(
                fields=("user", "is_read", "created_at"),
                name="notification_user_read_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user.username}: {self.notification_type}"


class PushDelivery(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        INVALID = "invalid", "Invalid device token"

    notification = models.ForeignKey(
        Notification,
        on_delete=models.CASCADE,
        related_name="push_deliveries",
    )
    device_token = models.ForeignKey(
        DeviceToken,
        on_delete=models.CASCADE,
        related_name="push_deliveries",
    )

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    attempt_count = models.PositiveSmallIntegerField(default=0)
    last_error = models.CharField(max_length=500, blank=True, default="")
    sent_at = models.DateTimeField(null=True, blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("id",)
        constraints = [
            models.UniqueConstraint(
                fields=("notification", "device_token"),
                name="unique_notification_push_delivery",
            )
        ]
        indexes = [
            models.Index(
                fields=("status", "updated_at"),
                name="push_delivery_updated_idx",
            )
        ]

    def __str__(self):
        return (
            f"notification={self.notification_id} / "
            f"device={self.device_token_id} / {self.status}"
        )
