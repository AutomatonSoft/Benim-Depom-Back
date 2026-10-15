from django.conf import settings
from django.db import models


class EbayConnection(models.Model):
    """Хранит подключение JV/XL/Depotum и зашифрованные токены с их сроками."""

    class Environment(models.TextChoices):
        SANDBOX = "sandbox", "Sandbox"
        PRODUCTION = "production", "Production"

    class Account(models.TextChoices):
        JV = "jv", "JV"
        XL = "xl", "XL"
        DEPOTUM = "depotum", "Depotum"

    environment = models.CharField(max_length=10, choices=Environment.choices)
    account = models.CharField(max_length=7, choices=Account.choices)
    ebay_user_id = models.CharField(max_length=255)
    access_token_encrypted = models.TextField()
    refresh_token_encrypted = models.TextField()
    access_token_expires_at = models.DateTimeField()
    refresh_token_expires_at = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("environment", "account"), name="ebay_connection_env_account"
            ),
            models.UniqueConstraint(
                fields=("environment", "ebay_user_id"), name="ebay_connection_env_user"
            ),
        ]


class EbayOAuthAttempt(models.Model):
    """Связывает одноразовый OAuth state с менеджером, аккаунтом и окружением."""

    state_hash = models.CharField(max_length=64, primary_key=True)
    environment = models.CharField(
        max_length=10, choices=EbayConnection.Environment.choices
    )
    account = models.CharField(max_length=7, choices=EbayConnection.Account.choices)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    expires_at = models.DateTimeField(db_index=True)
