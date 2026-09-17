from django.contrib import admin

from .models import (
    AfterbuyChannelStock,
    AfterbuyOrder,
    AfterbuyOrderItem,
    AfterbuySaleNotification,
)


class AfterbuyOrderItemInline(admin.TabularInline):
    model = AfterbuyOrderItem
    extra = 0
    readonly_fields = (
        "item_key",
        "anr",
        "sku",
        "ean",
        "title",
        "quantity",
        "marketplace",
        "matched_product",
    )


@admin.register(AfterbuyOrder)
class AfterbuyOrderAdmin(admin.ModelAdmin):
    list_display = (
        "afterbuy_order_id",
        "account",
        "marketplace",
        "paid_at",
        "total_amount",
        "platform_order_number",
    )
    list_filter = ("account", "marketplace")
    search_fields = ("afterbuy_order_id", "platform_order_number", "buyer_email")
    inlines = (AfterbuyOrderItemInline,)


@admin.register(AfterbuySaleNotification)
class AfterbuySaleNotificationAdmin(admin.ModelAdmin):
    list_display = ("id", "order_item", "status", "sent_at")
    list_filter = ("status",)


@admin.register(AfterbuyChannelStock)
class AfterbuyChannelStockAdmin(admin.ModelAdmin):
    list_display = ("product", "account", "marketplace", "quantity", "observed_at")
    list_filter = ("account", "marketplace")
    search_fields = ("product__title",)
