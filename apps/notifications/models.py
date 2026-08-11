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
    data = models.JSONField(default=dict, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

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