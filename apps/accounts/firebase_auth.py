from firebase_admin import auth
from rest_framework.exceptions import ValidationError

from apps.notifications.firebase import get_firebase_app


def get_verified_phone_from_id_token(*, id_token: str) -> str:
    firebase_app = get_firebase_app()

    if firebase_app is None:
        raise ValidationError(
            {
                "detail": (
                    "Phone verification is temporarily unavailable."
                )
            }
        )

    try:
        decoded_token = auth.verify_id_token(
            id_token,
            app=firebase_app,
            check_revoked=True,
        )
    except Exception as error:
        raise ValidationError(
            {"id_token": "Invalid or expired Firebase token."}
        ) from error

    phone_number = decoded_token.get("phone_number")

    if not phone_number:
        raise ValidationError(
            {
                "id_token": (
                    "Firebase token does not contain a verified phone number."
                )
            }
        )

    return phone_number