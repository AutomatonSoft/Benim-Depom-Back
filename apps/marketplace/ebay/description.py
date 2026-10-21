from urllib.parse import urlsplit

from django.template.loader import render_to_string
from rest_framework.exceptions import ValidationError


def render_listing_description(draft, *, ean):
    """Собирает HTML Depotum с экранированием данных и CSS-галереей без внешних стилей."""
    images = []
    for url in draft.image_urls:
        try:
            parsed = urlsplit(url)
            valid = (
                parsed.scheme == "https"
                and parsed.hostname
                and not parsed.username
                and not parsed.password
                and parsed.port in (None, 443)
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValidationError(
                {"image_urls": "Use HTTPS image URLs without credentials."}
            )
        images.append(url)
    return render_to_string(
        "ebay/listing_description.html",
        {
            "title": draft.title,
            "description": draft.description,
            "ean": ean,
            "images": images,
            "aspects": sorted(draft.aspects.items()),
            "colors": draft.aspects.get("Farbe", []),
        },
    ).strip()
