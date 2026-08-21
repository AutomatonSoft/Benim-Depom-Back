import socket
from unittest.mock import Mock

import pytest
from django.test import override_settings

from apps.common.safe_image_download import (
    GeneratedImageDownloadError,
    download_generated_image,
    validate_generated_image_url,
)


class FakeImageResponse:
    def __init__(
        self,
        *,
        chunks=None,
        content_type="image/jpeg",
        content_length=None,
        is_redirect=False,
    ):
        self.headers = {"Content-Type": content_type}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

        self.is_redirect = is_redirect
        self._chunks = chunks or [b"image-content"]
        self.close = Mock()

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        yield from self._chunks


def public_dns_result(hostname, port, type):
    return [
        (
            socket.AF_INET,
            socket.SOCK_STREAM,
            6,
            "",
            ("8.8.8.8", 443),
        )
    ]


@pytest.mark.unit
@override_settings(
    BULK_WHITE_IMAGE_SERVICE_URL="https://ai.example/api/generate/",
    BULK_WHITE_IMAGE_ALLOWED_IMAGE_HOSTS=("ai.example",),
    BULK_WHITE_IMAGE_MAX_DOWNLOAD_BYTES=1024,
)
def test_downloads_image_from_trusted_public_host(monkeypatch):
    response = FakeImageResponse(chunks=[b"first-", b"second"])

    monkeypatch.setattr(
        "apps.common.safe_image_download.socket.getaddrinfo",
        public_dns_result,
    )
    request = Mock(return_value=response)
    monkeypatch.setattr(
        "apps.common.safe_image_download.requests.get",
        request,
    )

    content = download_generated_image("https://ai.example/image.jpg")

    assert content == b"first-second"
    request.assert_called_once()
    response.close.assert_called_once()


@pytest.mark.unit
@override_settings(
    BULK_WHITE_IMAGE_SERVICE_URL="https://ai.example/api/generate/",
    BULK_WHITE_IMAGE_ALLOWED_IMAGE_HOSTS=("ai.example",),
)
@pytest.mark.parametrize(
    ("url", "message"),
    [
        (
            "http://ai.example/image.jpg",
            "must use HTTPS",
        ),
        (
            "https://evil.example/image.jpg",
            "not allowlisted",
        ),
        (
            "https://user:password@ai.example/image.jpg",
            "must not contain credentials",
        ),
        (
            "https://ai.example:8443/image.jpg",
            "port 443",
        ),
    ],
)
def test_rejects_unsafe_generated_image_urls(url, message):
    with pytest.raises(GeneratedImageDownloadError, match=message):
        validate_generated_image_url(url)


@pytest.mark.unit
@override_settings(
    BULK_WHITE_IMAGE_SERVICE_URL="https://ai.example/api/generate/",
    BULK_WHITE_IMAGE_ALLOWED_IMAGE_HOSTS=("ai.example",),
)
def test_rejects_host_resolving_to_private_ip(monkeypatch):
    monkeypatch.setattr(
        "apps.common.safe_image_download.socket.getaddrinfo",
        lambda hostname, port, type: [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("127.0.0.1", 443),
            )
        ],
    )

    with pytest.raises(
        GeneratedImageDownloadError,
        match="non-public IP",
    ):
        validate_generated_image_url("https://ai.example/image.jpg")


@pytest.mark.unit
@override_settings(
    BULK_WHITE_IMAGE_SERVICE_URL="https://ai.example/api/generate/",
    BULK_WHITE_IMAGE_ALLOWED_IMAGE_HOSTS=("ai.example",),
)
def test_rejects_redirect_from_trusted_host(monkeypatch):
    response = FakeImageResponse(is_redirect=True)

    monkeypatch.setattr(
        "apps.common.safe_image_download.socket.getaddrinfo",
        public_dns_result,
    )
    monkeypatch.setattr(
        "apps.common.safe_image_download.requests.get",
        Mock(return_value=response),
    )

    with pytest.raises(
        GeneratedImageDownloadError,
        match="redirects",
    ):
        download_generated_image("https://ai.example/image.jpg")

    response.close.assert_called_once()


@pytest.mark.unit
@override_settings(
    BULK_WHITE_IMAGE_SERVICE_URL="https://ai.example/api/generate/",
    BULK_WHITE_IMAGE_ALLOWED_IMAGE_HOSTS=("ai.example",),
    BULK_WHITE_IMAGE_MAX_DOWNLOAD_BYTES=4,
)
def test_rejects_generated_image_larger_than_limit(monkeypatch):
    response = FakeImageResponse(
        content_length=5,
        chunks=[b"12345"],
    )

    monkeypatch.setattr(
        "apps.common.safe_image_download.socket.getaddrinfo",
        public_dns_result,
    )
    monkeypatch.setattr(
        "apps.common.safe_image_download.requests.get",
        Mock(return_value=response),
    )

    with pytest.raises(
        GeneratedImageDownloadError,
        match="exceeds",
    ):
        download_generated_image("https://ai.example/image.jpg")