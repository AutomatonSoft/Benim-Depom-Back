from django.contrib.auth.models import AbstractUser
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.db import models


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

    class RegistrationStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    username = models.CharField(
        max_length=150,
        unique=False,
        validators=[UnicodeUsernameValidator()],
        help_text="Display name. Not unique; authentication uses email.",
    )
    email = models.EmailField(unique=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.SELLER)
    phone = models.CharField(max_length=32, blank=True)
    is_email_verified = models.BooleanField(default=False)

    registration_status = models.CharField(
        max_length=16,
        choices=RegistrationStatus.choices,
        default=RegistrationStatus.APPROVED,
    )
    registration_rejection_reason = models.TextField(blank=True)

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
    password_reset_code_hash = models.CharField(
        max_length=128,
        blank=True,
    )
    password_reset_expires_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    password_reset_attempts = models.PositiveSmallIntegerField(
        default=0,
    )
    password_reset_sent_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    password_reset_verified_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    preferred_language = models.CharField(
        max_length=5, choices=Language.choices, default=Language.RUSSIAN
    )

    class Meta:
        ordering = ("-date_joined",)

    def __str__(self) -> str:
        return self.username
