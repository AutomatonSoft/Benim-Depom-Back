from django.contrib import admin

from .models import Product, ProductImage, ProductVariant

class ProductVariantInLine(admin.TabularInline):
    model = ProductVariant
    extra = 0


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 0


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "title",
        "owner",
        "product_type",
        "status",
        "created_at",
    )

    list_filter = ("status", "product_type", "category")
    search_fields = ("title", "owner__username", "owner__email")
    list_select_related = ("owner", "product_type", "category")
    readonly_fields = ("created_at", "updated_at")
    inlines = (ProductVariantInLine, ProductImageInline)


