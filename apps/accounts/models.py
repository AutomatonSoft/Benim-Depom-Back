from django.contrib.auth.models import AbstractUser
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

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.SELLER
    )
    phone = models.CharField(max_length=32, blank=True)
    is_phone_verified = models.BooleanField(default=False)
    preferred_language = models.CharField(
        max_length=5,
        choices=Language.choices,
        default=Language.RUSSIAN
    )

    class Meta:
        ordering = ("-date_joined",)

    def __str__(self) -> str:
        return self.username