import re

from django.core.exceptions import ValidationError


def normalize_phone(value: str) -> str:
    """Use one international representation for login and uniqueness checks."""
    compact = re.sub(r"[\s().-]", "", value.strip())
    if compact.startswith("00"):
        compact = "+" + compact[2:]
    if not re.fullmatch(r"\+?[1-9][0-9]{7,14}", compact):
        raise ValidationError(
            "Enter a phone number with a country code and 8–15 digits."
        )
    return "+" + compact.lstrip("+")
