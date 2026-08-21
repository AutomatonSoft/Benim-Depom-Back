import secrets
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import User


@transaction.atomic
def register_user(*, data: dict[str, Any]) -> User:
    user_data = data.copy()
    password = user_data.pop("password")

    return User.objects.create_user(
        password=password,
        is_active=False,
        is_email_verified=False,
        **user_data,
    )


def issue_email_verification_code(*, user: User) -> str:
    code = str(secrets.randbelow(900_000) + 100_000)
    now = timezone.now()

    user.email_verification_code_hash = make_password(code)
    user.email_verification_expires_at = now + timedelta(
        minutes=settings.EMAIL_VERIFICATION_CODE_TTL_MINUTES
    )
    user.email_verification_attempts = 0
    user.email_verification_sent_at = now
    user.save(
        update_fields=(
            "email_verification_code_hash",
            "email_verification_expires_at",
            "email_verification_attempts",
            "email_verification_sent_at",
        )
    )

    return code


def verify_email_code(*, email: str, code: str) -> User:
    invalid_error = ValidationError({"code": "Invalid or expired verification code."})

    with transaction.atomic():
        user = (
            User.objects.select_for_update()
            .filter(email__iexact=email.strip().lower())
            .first()
        )

        if not user or user.is_email_verified:
            raise invalid_error

        now = timezone.now()

        if (
            not user.email_verification_code_hash
            or not user.email_verification_expires_at
            or user.email_verification_expires_at <= now
            or user.email_verification_attempts
            >= settings.EMAIL_VERIFICATION_MAX_ATTEMPTS
        ):
            raise invalid_error

        if not check_password(code, user.email_verification_code_hash):
            user.email_verification_attempts += 1
            user.save(update_fields=("email_verification_attempts",))
            is_invalid_code = True
        else:
            user.is_email_verified = True
            user.is_active = True
            user.email_verification_code_hash = ""
            user.email_verification_expires_at = None
            user.email_verification_attempts = 0
            user.save(
                update_fields=(
                    "is_email_verified",
                    "is_active",
                    "email_verification_code_hash",
                    "email_verification_expires_at",
                    "email_verification_attempts",
                )
            )
            is_invalid_code = False

    if is_invalid_code:
        raise invalid_error

    return user


@transaction.atomic
def resend_email_verification_code(*, email: str) -> tuple[User, str] | None:
    user = (
        User.objects.select_for_update()
        .filter(email__iexact=email.strip().lower())
        .first()
    )

    if not user or user.is_email_verified:
        return None

    now = timezone.now()

    if user.email_verification_sent_at:
        elapsed_seconds = (now - user.email_verification_sent_at).total_seconds()

        if elapsed_seconds < settings.EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS:
            raise ValidationError(
                {
                    "detail": "Please wait before requesting another code.",
                    "retry_after_seconds": int(
                        settings.EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS
                        - elapsed_seconds
                    ),
                }
            )

    return user, issue_email_verification_code(user=user)
