from django.contrib import admin

from .models import Category, Color, Material, ProductType


@admin.register(Category, ProductType, Material, Color)
class CatalogAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "is_active", "sort_order")
    list_filter = ("is_active",)
    search_fields = ("name",)
    ordering = ("sort_order", "name")

    