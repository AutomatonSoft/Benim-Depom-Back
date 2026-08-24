from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q


class User(AbstractUser):
    class Role(models.TextChoices):
        SELLER = "seller", "Seller"
        MANAGER = "manager", "Manager"
        ADMIN = "admin", "Admin"

    class Language(models.TextChoices):
        RUSSIAN = "ru", "Русский"
        TURKISH = "tr", "Türkçe"
        GERMAN = "de", "Deutsch"
        ENGLISH = "en", "English"

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.SELLER)
    phone = models.CharField(max_length=32, blank=True)
    is_email_verified = models.BooleanField(default=False)

    email_verification_code_hash = models.CharField(
        max_length=128,
        blank=True,
    )
    email_verification_expires_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    email_verification_attempts = models.PositiveSmallIntegerField(
        default=0,
    )
    email_verification_sent_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    preferred_language = models.CharField(
        max_length=5, choices=Language.choices, default=Language.RUSSIAN
    )

    class Meta:
        ordering = ("-date_joined",)
        constraints = [
            models.UniqueConstraint(
                fields=("email",),
                condition=~Q(email=""),
                name="accounts_user_unique_nonempty_email",
            ),
        ]

    def __str__(self) -> str:
        return self.username
