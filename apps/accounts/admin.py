from django.contrib import admin
from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


class StaffAuthenticationForm(AdminAuthenticationForm):
    """Keep staff email login usable after the primary identity becomes phone."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "Email or phone"
        self.fields["username"].max_length = 254
        self.fields["username"].widget.attrs["maxlength"] = 254


admin.site.login_form = StaffAuthenticationForm


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = (
        "id",
        "username",
        "email",
        "role",
        "phone",
        "is_email_verified",
        "is_active",
    )
    list_filter = (
        "role",
        "preferred_language",
        "is_email_verified",
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
                    "is_email_verified",
                    "preferred_language",
                ),
            },
        ),
    )
