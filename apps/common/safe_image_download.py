import ipaddress
import socket

from urllib.parse import urlparse

import requests
from django.conf import settings


class GeneratedImageDownloadError(Exception):
    """A generated image URL is unsafe or cannot be downloaded."""


def _allowed_hosts() -> set[str]:
    hosts = set(settings.BULK_WHITE_IMAGE_ALLOWED_IMAGE_HOSTS)

    service_url = urlparse(settings.BULK_WHITE_IMAGE_SERVICE_URL)
    if service_url.hostname:
        hosts.add(service_url.hostname.lower())

    return hosts

def _validate_public_host(hostname: str) -> None:
    try:
        addresses = socket.getaddrinfo(
            hostname,
            443,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as error:
        raise GeneratedImageDownloadError(
            "Image host cannot be resolved."
        ) from error

    resolved_ips = {
        address_info[4][0]
        for address_info in addresses
    }

    if not resolved_ips:
        raise GeneratedImageDownloadError(
            "Image host did not resolve to an IP address."
        )

    for raw_ip in resolved_ips:
        ip = ipaddress.ip_address(raw_ip)

        if not ip.is_global:
            raise GeneratedImageDownloadError(
                "Image host resolves to a non-public IP address."
            )



def validate_generated_image_url(url: str) -> None:
    parsed = urlparse(url)

    if parsed.scheme != "https":
        raise GeneratedImageDownloadError(
            "Generated image URL must use HTTPS."
        )

    if not parsed.hostname:
        raise GeneratedImageDownloadError(
            "Generated image URL has no host."
        )

    if parsed.username or parsed.password:
        raise GeneratedImageDownloadError(
            "Generated image URL must not contain credentials."
        )

    if parsed.port not in (None, 443):
        raise GeneratedImageDownloadError(
            "Generated image URL must use HTTPS port 443."
        )

    hostname = parsed.hostname.lower()

    if hostname not in _allowed_hosts():
        raise GeneratedImageDownloadError(
            "Generated image host is not allowlisted."
        )

    _validate_public_host(hostname)



def download_generated_image(url: str) -> bytes:
    """
    Downloads only a public HTTPS image from an explicitly allowed host.

    Redirects are forbidden: otherwise a trusted URL could redirect the worker
    to a private/internal address.
    """
    validate_generated_image_url(url)

    try:
        response = requests.get(
            url,
            headers={"Accept": "image/*"},
            timeout=settings.BULK_WHITE_IMAGE_SERVICE_TIMEOUT_SECONDS,
            allow_redirects=False,
            stream=True,
        )
        response.raise_for_status()
    except requests.RequestException as error:
        raise GeneratedImageDownloadError(
            f"Unable to download generated image: {error}"
        ) from error

    if response.is_redirect:
        response.close()
        raise GeneratedImageDownloadError(
            "Generated image URL redirects to another location."
        )

    content_type = response.headers.get("Content-Type", "").lower()
    if not content_type.startswith("image/"):
        response.close()
        raise GeneratedImageDownloadError(
            "Generated file does not have an image content type."
        )

    content_length = response.headers.get("Content-Length")
    if (
        content_length is not None
        and int(content_length)
        > settings.BULK_WHITE_IMAGE_MAX_DOWNLOAD_BYTES
    ):
        response.close()
        raise GeneratedImageDownloadError(
            "Generated image exceeds the allowed download size."
        )

    chunks = []
    downloaded_bytes = 0

    try:
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue

            downloaded_bytes += len(chunk)

            if (
                downloaded_bytes
                > settings.BULK_WHITE_IMAGE_MAX_DOWNLOAD_BYTES
            ):
                raise GeneratedImageDownloadError(
                    "Generated image exceeds the allowed download size."
                )

            chunks.append(chunk)
    finally:
        response.close()

    if not chunks:
        raise GeneratedImageDownloadError(
            "Generated image file is empty."
        )

    return b"".join(chunks)