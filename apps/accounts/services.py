from typing import Any
from .models import User

def register_user(*, data: dict[str, Any]) -> User:
    user_data = data.copy()
    password = user_data.pop("password")

    return User.objects.create_user(
        password=password,
        **user_data,
    )