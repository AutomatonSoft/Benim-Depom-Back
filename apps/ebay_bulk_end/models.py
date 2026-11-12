import uuid

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.db import models


class EbayBulkEndJob(models.Model):
    ACCOUNTS = (("jv", "JV"), ("xl", "XL"), ("depotum", "Depotum"))
    ENVIRONMENTS = (("sandbox", "Sandbox"), ("production", "Production"))

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="ebay_bulk_end_jobs",
    )
    account = models.CharField(max_length=16, choices=ACCOUNTS)
    environment = models.CharField(max_length=16, choices=ENVIRONMENTS)
    ebay_seller_id = models.CharField(max_length=255, blank=True)
    kind = models.CharField(max_length=8, default="scan")
    status = models.CharField(max_length=24, default="queued")
    discovery_strategy = models.CharField(max_length=40, blank=True)
    discovery_complete = models.BooleanField(default=False)
    discovery_details = models.JSONField(default=dict, blank=True)
    workflow_state = models.JSONField(default=dict, blank=True)
    cancel_requested = models.BooleanField(default=False)
    inventory_snapshot = models.ForeignKey(
        "EbayInventorySnapshot",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="jobs",
    )
    input_eans = models.JSONField(default=list, blank=True)
    progress = models.JSONField(default=dict, blank=True)
    unique_ean_count = models.PositiveIntegerField(default=0)
    matched_item_count = models.PositiveIntegerField(default=0)
    selected_item_count = models.PositiveIntegerField(default=0)
    ended_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    uncertain_count = models.PositiveIntegerField(default=0)
    not_found_count = models.PositiveIntegerField(default=0)
    report_task_id = models.CharField(max_length=255, blank=True)
    preview_expires_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    scan_started_at = models.DateTimeField(null=True, blank=True)
    scan_finished_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    execution_started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=("account", "environment", "status", "created_at"),
                name="ebulk_job_scope_status_idx",
            ),
            models.Index(
                fields=("created_by", "created_at"), name="ebulk_job_owner_created_idx"
            ),
        ]


class EbayBulkEndJobItem(models.Model):
    job = models.ForeignKey(
        EbayBulkEndJob, on_delete=models.CASCADE, related_name="items"
    )
    item_id = models.CharField(max_length=32)
    sku = models.CharField(max_length=255, blank=True)
    matched_eans = models.JSONField(default=list, blank=True)
    match_type = models.CharField(max_length=32, blank=True)
    details = models.JSONField(default=dict, blank=True)
    selected = models.BooleanField(default=False)
    selection_order = models.PositiveIntegerField(null=True, blank=True)
    risky_variations = models.BooleanField(default=False)
    auction_risk = models.BooleanField(default=False)
    managed_by_benim = models.BooleanField(default=False)
    status = models.CharField(max_length=24, default="matched")
    ebay_error_code = models.CharField(max_length=64, blank=True)
    ebay_error_message = models.TextField(blank=True)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    batch_id = models.UUIDField(null=True, blank=True)
    ebay_correlation_id = models.CharField(max_length=255, blank=True)
    remote_end_time = models.DateTimeField(null=True, blank=True)
    last_verified_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("job", "item_id"), name="ebulk_job_item_unique"
            )
        ]
        indexes = [
            models.Index(fields=("job", "status"), name="ebulk_item_job_status_idx"),
            models.Index(fields=("item_id", "job"), name="ebulk_item_id_job_idx"),
        ]


class EbayBulkEndInFlight(models.Model):
    job = models.ForeignKey(
        EbayBulkEndJob, on_delete=models.CASCADE, related_name="in_flight_items"
    )
    job_item = models.ForeignKey(
        EbayBulkEndJobItem, on_delete=models.CASCADE, related_name="in_flight_attempts"
    )
    seller_id = models.CharField(max_length=255)
    environment = models.CharField(max_length=16)
    item_id = models.CharField(max_length=32)
    batch_id = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("environment", "seller_id", "item_id"),
                name="ebulk_inflight_item_unique",
            )
        ]


class EbayBulkEndEvent(models.Model):
    job = models.ForeignKey(
        EbayBulkEndJob, on_delete=models.CASCADE, related_name="events"
    )
    item = models.ForeignKey(
        EbayBulkEndJobItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
    )
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ebay_bulk_end_events",
    )
    event_type = models.CharField(max_length=40)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(
                fields=("job", "created_at"), name="ebulk_event_job_created_idx"
            ),
            models.Index(
                fields=("event_type", "created_at"), name="ebulk_event_type_created_idx"
            ),
        ]


class EbayInventorySnapshot(models.Model):
    ACCOUNTS = EbayBulkEndJob.ACCOUNTS
    ENVIRONMENTS = EbayBulkEndJob.ENVIRONMENTS

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.CharField(max_length=16, choices=ACCOUNTS)
    environment = models.CharField(max_length=16, choices=ENVIRONMENTS)
    ebay_seller_id = models.CharField(max_length=255)
    marketplace_id = models.CharField(max_length=16)
    task_id = models.CharField(max_length=255, blank=True)
    report_request_started_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=24, default="requested")
    complete = models.BooleanField(default=False)
    includes_unlabelled_items = models.BooleanField(default=True)
    record_count = models.PositiveBigIntegerField(default=0)
    error_message = models.TextField(blank=True)
    report_completed_at = models.DateTimeField(null=True, blank=True)
    downloaded_at = models.DateTimeField(null=True, blank=True)
    import_started_at = models.DateTimeField(null=True, blank=True)
    import_completed_at = models.DateTimeField(null=True, blank=True)
    report_path = models.CharField(max_length=1024, blank=True)
    report_size = models.PositiveBigIntegerField(default=0)
    report_sha256 = models.CharField(max_length=64, blank=True)
    report_mtime_ns = models.PositiveBigIntegerField(default=0)
    import_generation = models.PositiveBigIntegerField(default=0)
    report_download_attempts = models.PositiveSmallIntegerField(default=0)
    report_download_attempted_at = models.JSONField(default=list, blank=True)
    last_report_download_attempt_at = models.DateTimeField(null=True, blank=True)
    report_poll_attempts = models.PositiveSmallIntegerField(default=0)
    retry_count = models.PositiveSmallIntegerField(default=0)
    checkpoint_row = models.PositiveBigIntegerField(default=0)
    checkpoint_cookie = models.TextField(blank=True)
    owner_token = models.UUIDField(null=True, blank=True)
    owner_job_id = models.UUIDField(null=True, blank=True)
    ownership_generation = models.PositiveBigIntegerField(default=0)
    owner_expires_at = models.DateTimeField(null=True, blank=True)
    last_heartbeat_at = models.DateTimeField(null=True, blank=True)
    step_scheduled_at = models.DateTimeField(null=True, blank=True)
    cleanup_started_at = models.DateTimeField(null=True, blank=True)
    cleanup_completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("account", "environment", "ebay_seller_id", "marketplace_id"),
                condition=models.Q(
                    status__in=(
                        "requested",
                        "requesting",
                        "queued",
                        "in_progress",
                        "downloading",
                        "importing",
                        "verifying",
                    )
                ),
                name="ebulk_snapshot_active_unique",
            )
        ]
        indexes = [
            models.Index(
                fields=(
                    "account",
                    "environment",
                    "ebay_seller_id",
                    "marketplace_id",
                    "status",
                    "completed_at",
                ),
                name="ebulk_snapshot_scope_idx",
            ),
            models.Index(
                fields=("status", "owner_expires_at"), name="ebulk_snapshot_owner_idx"
            ),
            models.Index(
                fields=(
                    "environment",
                    "ebay_seller_id",
                    "marketplace_id",
                    "last_report_download_attempt_at",
                ),
                name="ebulk_snapshot_quota_idx",
            ),
        ]


class EbayInventoryListing(models.Model):
    snapshot = models.ForeignKey(
        EbayInventorySnapshot, on_delete=models.CASCADE, related_name="listings"
    )
    item_id = models.CharField(max_length=32)
    sku = models.CharField(max_length=255, blank=True)
    marketplace_id = models.CharField(max_length=16)
    variation = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("snapshot", "item_id", "marketplace_id", "sku", "variation"),
                name="ebulk_snapshot_sku_unique",
            )
        ]
        indexes = [
            models.Index(fields=("snapshot", "sku"), name="ebulk_snapshot_sku_idx"),
            models.Index(fields=("item_id",), name="ebulk_snapshot_item_idx"),
        ]


class EbayListingIdentifiers(models.Model):
    environment = models.CharField(max_length=16)
    seller_id = models.CharField(max_length=255)
    item_id = models.CharField(max_length=32)
    identifiers = models.JSONField(default=list)
    checked_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("environment", "seller_id", "item_id"),
                name="ebulk_identifier_seller_item",
            )
        ]
        indexes = [
            GinIndex(fields=("identifiers",), name="ebulk_identifier_values_idx")
        ]
