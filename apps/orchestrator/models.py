import uuid

from django.conf import settings
from django.db import models


class MarketplaceJob(models.Model):
    class Operation(models.TextChoices):
        SEARCH = "search", "Search"
        PUBLISH = "publish", "Publish"
        UPDATE = "update", "Update"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
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
        max_length=16,
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
            models.Index(fields=("product", "created_at"), name="marketplace_job_product_idx"),
            models.Index(fields=("status", "created_at"), name="marketplace_job_status_idx"),
        )

# Create your models here.
