from typing import Any
from .models import User

from django.db import transaction
from rest_framework.exceptions import ValidationError

def register_user(*, data: dict[str, Any]) -> User:
    user_data = data.copy()
    password = user_data.pop("password")

    return User.objects.create_user(
        password=password,
        **user_data,
    )


@transaction.atomic
def verify_user_phone(*, user: User, phone_number: str) -> User:
    if user.phone and user.phone != phone_number:
        raise ValidationError(
            {
                "id_token": (
                    "The Firebase phone number does not match "
                    "the phone number in your profile."
                )
            }
        )

    user.phone = phone_number
    user.is_phone_verified = True
    user.save(
        update_fields=(
            "phone",
            "is_phone_verified",
        )
    )

    return user