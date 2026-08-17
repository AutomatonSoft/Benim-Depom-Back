from django.contrib import admin

from .models import MarketplaceJob, MarketplacePublication


@admin.register(MarketplaceJob)
class MarketplaceJobAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "product",
        "operation",
        "status",
        "requested_by",
        "created_at",
        "finished_at",
    )
    list_filter = ("operation", "status")
    search_fields = ("id", "product__title", "request_id")
    list_select_related = ("product", "requested_by")
    readonly_fields = (
        "id",
        "request_id",
        "created_at",
        "started_at",
        "finished_at",
    )


@admin.register(MarketplacePublication)
class MarketplacePublicationAdmin(admin.ModelAdmin):
    list_display = (
        "product",
        "marketplace",
        "account",
        "ean",
        "status",
        "attempt_count",
        "published_at",
        "updated_at",
    )
    list_filter = ("marketplace", "account", "status")
    search_fields = (
        "ean",
        "product__title",
        "external_id",
        "external_reference",
    )
    list_select_related = ("product", "last_job")
    readonly_fields = (
        "created_at",
        "updated_at",
        "published_at",
        "deactivated_at",
        "deleted_at",
        "last_attempt_at",
    )