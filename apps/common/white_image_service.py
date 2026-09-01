import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


class WhiteImageServiceError(Exception):
    pass


def _summarize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _summarize_value(item) for key, item in list(value.items())[:50]
        }

    if isinstance(value, list):
        return [_summarize_value(item) for item in value[:50]]

    if isinstance(value, str):
        return value[:2000]

    if value is None or isinstance(value, (bool, int, float)):
        return value

    return str(value)[:2000]


def generate_white_background(*, image_file, title: str, product_type: str) -> dict:
    if not settings.BULK_WHITE_IMAGE_SERVICE_URL:
        raise ImproperlyConfigured(
            "Image generation is not configured on this server. "
            "Set BULK_WHITE_IMAGE_SERVICE_URL in the backend environment."
        )

    if not settings.BULK_WHITE_IMAGE_SERVICE_TOKEN:
        raise ImproperlyConfigured("BULK_WHITE_IMAGE_SERVICE_TOKEN is not configured")

    filename = Path(image_file.name).name
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    image_file.open("rb")

    try:
        response = requests.post(
            settings.BULK_WHITE_IMAGE_SERVICE_URL,
            headers={
                "Authorization": (f"Bearer {settings.BULK_WHITE_IMAGE_SERVICE_TOKEN}"),
            },
            data={"title": title, "produktart": product_type},
            files={
                "image": (filename, image_file.file, content_type),
            },
            timeout=settings.BULK_WHITE_IMAGE_SERVICE_TIMEOUT_SECONDS,
        )
        response.raise_for_status()

    except requests.RequestException as error:
        raise WhiteImageServiceError(
            f"White image service request failed: {error}"
        ) from error

    finally:
        image_file.close()

    try:
        payload = response.json()
    except ValueError:
        payload = {
            "body_preview": response.text[:2000],
        }

    return {
        "http_status": response.status_code,
        "content_type": response.headers.get("Content-Type", ""),
        "payload": _summarize_value(payload),
    }


def get_generation_results(
    *,
    product_id: int,
    result_url: str | None = None,
) -> dict:
    if not settings.BULK_WHITE_IMAGE_SERVICE_RESULTS_URL:
        raise ImproperlyConfigured(
            "BULK_WHITE_IMAGE_SERVICE_RESULTS_URL is not configured."
        )

    if not settings.BULK_WHITE_IMAGE_SERVICE_TOKEN:
        raise ImproperlyConfigured("BULK_WHITE_IMAGE_SERVICE_TOKEN is not configured.")

    url = result_url or settings.BULK_WHITE_IMAGE_SERVICE_RESULTS_URL.format(
        product_id=product_id,
    )

    # status_url is supplied by the generation service. Do not allow a
    # compromised/unexpected response to turn the worker into an SSRF client.
    service_url = urlparse(settings.BULK_WHITE_IMAGE_SERVICE_URL)
    parsed_result_url = urlparse(url)
    if (
        parsed_result_url.scheme != "https"
        or parsed_result_url.netloc != service_url.netloc
    ):
        raise WhiteImageServiceError("Generation result URL is not trusted.")

    try:
        response = requests.get(
            url,
            headers={
                "Authorization": (f"Bearer {settings.BULK_WHITE_IMAGE_SERVICE_TOKEN}"),
            },
            timeout=settings.BULK_WHITE_IMAGE_SERVICE_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as error:
        raise WhiteImageServiceError(
            f"Unable to retrieve generation result: {error}"
        ) from error
