import secrets
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import validate_password
from django.core import signing
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)

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


def issue_password_reset_code(*, user: User) -> str:
    code = str(secrets.randbelow(900_000) + 100_000)
    now = timezone.now()

    user.password_reset_code_hash = make_password(code)
    user.password_reset_expires_at = now + timedelta(
        minutes=settings.PASSWORD_RESET_CODE_TTL_MINUTES
    )
    user.password_reset_attempts = 0
    user.password_reset_sent_at = now
    user.password_reset_verified_at = None
    user.save(
        update_fields=(
            "password_reset_code_hash",
            "password_reset_expires_at",
            "password_reset_attempts",
            "password_reset_sent_at",
            "password_reset_verified_at",
        )
    )

    return code


@transaction.atomic
def request_password_reset(*, email: str) -> tuple[User, str] | None:
    user = (
        User.objects.select_for_update()
        .filter(email__iexact=email.strip().lower(), is_active=True)
        .first()
    )

    if not user:
        return None

    now = timezone.now()
    if user.password_reset_sent_at:
        elapsed_seconds = (now - user.password_reset_sent_at).total_seconds()
        if elapsed_seconds < settings.PASSWORD_RESET_RESEND_COOLDOWN_SECONDS:
            return None

    return user, issue_password_reset_code(user=user)


def verify_password_reset_code(*, email: str, code: str) -> str:
    invalid_error = ValidationError({"code": "Invalid or expired reset code."})

    with transaction.atomic():
        user = (
            User.objects.select_for_update()
            .filter(email__iexact=email.strip().lower(), is_active=True)
            .first()
        )
        now = timezone.now()

        if (
            not user
            or not user.password_reset_code_hash
            or not user.password_reset_expires_at
            or user.password_reset_expires_at <= now
            or user.password_reset_attempts >= settings.PASSWORD_RESET_MAX_ATTEMPTS
        ):
            raise invalid_error

        if not check_password(code, user.password_reset_code_hash):
            user.password_reset_attempts += 1
            user.save(update_fields=("password_reset_attempts",))
            reset_token = None
        else:
            user.password_reset_verified_at = now
            user.save(update_fields=("password_reset_verified_at",))
            reset_token = signing.dumps(
                {"user_id": user.pk, "verified_at": now.isoformat()},
                salt="accounts.password-reset",
            )

    if reset_token is None:
        raise invalid_error

    return reset_token


def revoke_refresh_tokens(*, user: User) -> None:
    for token in OutstandingToken.objects.filter(user=user):
        BlacklistedToken.objects.get_or_create(token=token)


def complete_password_reset(*, reset_token: str, new_password: str) -> None:
    invalid_error = ValidationError({"reset_token": "Invalid or expired reset token."})
    try:
        payload = signing.loads(
            reset_token,
            salt="accounts.password-reset",
            max_age=settings.PASSWORD_RESET_TOKEN_TTL_MINUTES * 60,
        )
    except signing.BadSignature as exc:
        raise invalid_error from exc

    with transaction.atomic():
        user = (
            User.objects.select_for_update().filter(pk=payload.get("user_id")).first()
        )
        if (
            not user
            or not user.is_active
            or not user.password_reset_verified_at
            or user.password_reset_verified_at.isoformat() != payload.get("verified_at")
        ):
            raise invalid_error

        try:
            validate_password(new_password, user)
        except DjangoValidationError as exc:
            raise ValidationError({"new_password": list(exc.messages)}) from exc
        user.set_password(new_password)
        user.password_reset_code_hash = ""
        user.password_reset_expires_at = None
        user.password_reset_attempts = 0
        user.password_reset_sent_at = None
        user.password_reset_verified_at = None
        user.save(
            update_fields=(
                "password",
                "password_reset_code_hash",
                "password_reset_expires_at",
                "password_reset_attempts",
                "password_reset_sent_at",
                "password_reset_verified_at",
            )
        )
        revoke_refresh_tokens(user=user)
