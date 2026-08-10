from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = (
        "id",
        "username",
        "email",
        "role",
        "phone",
        "is_phone_verified",
        "is_active",
    )
    list_filter = (
        "role",
        "preferred_language",
        "is_phone_verified",
        "is_active",
        "is_staff",
    )
    search_fields = ("username", "email", "phone")
    ordering = ("-date_joined",)

    fieldsets = BaseUserAdmin.fieldsets + (
        (
            "Marketplace profile",
            {
                "fields": (
                    "role",
                    "phone",
                    "is_phone_verified",
                    "preferred_language",
                ),
            },
        ),
    )