from django.contrib import admin

from .models import EanCode


@admin.register(EanCode)
class EanCodeAdmin(admin.ModelAdmin):
    list_display = ("code", "account", "product", "assigned_at", "imported_at")
    list_filter = ("account",)
    search_fields = ("code", "product__title")
    list_select_related = ("product",)
