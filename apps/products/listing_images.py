from __future__ import annotations

from urllib.parse import urlparse

LISTING_IMAGE_MODES = ("white", "interior", "human")
MISSING_LISTING_IMAGES = (
    "Generate AI images for the cover photo before publishing. "
    "Seller source photos are not sent to marketplaces."
)


def _iter_related(value) -> list:
    if value is None:
        return []
    if hasattr(value, "all"):
        return list(value.all())
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _cover_source_image(product):
    images = _iter_related(getattr(product, "images", None))
    for image in images:
        if getattr(image, "is_primary", False):
            return image
    return images[0] if images else None


def _public_http_url(file_field) -> str | None:
    if not file_field:
        return None
    url = str(getattr(file_field, "url", "") or file_field).strip()
    parsed = urlparse(url)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return url
    return None


def public_generated_listing_urls(product) -> list[str]:
    """Public URLs of AI images for the cover photo, never seller originals."""
    cover = _cover_source_image(product)
    if cover is None:
        return []

    by_mode = {
        generated.mode: generated
        for generated in _iter_related(getattr(cover, "generated_images", None))
    }
    urls: list[str] = []
    seen: set[str] = set()
    for mode in LISTING_IMAGE_MODES:
        generated = by_mode.get(mode)
        url = _public_http_url(getattr(generated, "image", None) if generated else None)
        if url and url not in seen:
            urls.append(url)
            seen.add(url)
    return urls
