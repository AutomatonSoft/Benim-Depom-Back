from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from django.conf import settings


SHIPPING_PROFILES_FILE = (
    settings.BASE_DIR / "data" / "otto" / "shipping_profiles.json"
)


class OttoShippingProfilesError(Exception):
    """Local OTTO shipping profiles are unavailable or malformed."""


@lru_cache(maxsize=1)
def get_otto_shipping_profiles() -> dict[str, Any]:
    try:
        payload = json.loads(
            SHIPPING_PROFILES_FILE.read_text(encoding="utf-8")
        )
    except FileNotFoundError as exc:
        raise OttoShippingProfilesError(
            "OTTO shipping profiles JSON was not found."
        ) from exc
    except json.JSONDecodeError as exc:
        raise OttoShippingProfilesError(
            "OTTO shipping profiles JSON is invalid."
        ) from exc

    accounts = payload.get("accounts")

    if not isinstance(accounts, dict):
        raise OttoShippingProfilesError(
            "OTTO shipping profiles have an unexpected structure."
        )

    profiles_by_account: dict[str, list[dict[str, Any]]] = {}
    profiles_by_id_by_account: dict[
        str,
        dict[str, dict[str, Any]],
    ] = {}

    for account in ("jv", "xl"):
        profiles = accounts.get(account, [])

        if not isinstance(profiles, list):
            raise OttoShippingProfilesError(
                f"Profiles for account '{account}' must be a list."
            )

        profiles_by_account[account] = profiles
        profiles_by_id_by_account[account] = {}

        for profile in profiles:
            profile_id = profile.get("shippingProfileId")
            profile_name = profile.get("shippingProfileName")
            delivery_type = profile.get("deliveryType")
            transport_time = profile.get("transportTime")

            if (
                not isinstance(profile_id, str)
                or not profile_id
                or not isinstance(profile_name, str)
                or not profile_name
                or not isinstance(delivery_type, str)
                or not isinstance(transport_time, int)
                or transport_time < 1
            ):
                raise OttoShippingProfilesError(
                    f"Invalid OTTO shipping profile for account '{account}'."
                )

            if profile_id in profiles_by_id_by_account[account]:
                raise OttoShippingProfilesError(
                    f"Duplicate shipping profile ID for account '{account}'."
                )

            profiles_by_id_by_account[account][profile_id] = profile

    return {
        "profiles_by_account": profiles_by_account,
        "profiles_by_id_by_account": profiles_by_id_by_account,
    }


def get_otto_shipping_profile(
    *,
    account: str,
    shipping_profile_id: str,
) -> dict[str, Any] | None:
    catalog = get_otto_shipping_profiles()

    return catalog["profiles_by_id_by_account"].get(
        account,
        {},
    ).get(shipping_profile_id)


def clear_otto_shipping_profiles_cache() -> None:
    get_otto_shipping_profiles.cache_clear()