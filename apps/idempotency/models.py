from django.db import models

from django.conf import settings


class IdempotencyRecord(models.Model):
    class Status(models.TextChoices):
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"


    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="idempotency_records"
    )
    endpoint = models.CharField(max_length=255)
    key=models.CharField(max_length=128)
    request_hash = models.CharField(max_length=64)

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PROCESSING,
    )

    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    response_body = models.JSONField(default=dict, blank=True)

    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("user", "endpoint", "key"),
                name="unique_user_endpoint_idempotency_key"
            )
        ]
        indexes = [
            models.Index(
                fields=("status", "expires_at"),
                name="idempotency_status_expiry_idx",
            )
        ]

    def __str__(self):
        return f"{self.user_id} / {self.endpoint} / {self.key}"


