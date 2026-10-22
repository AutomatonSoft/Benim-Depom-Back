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
    dimension_names = ("Breite", "Länge", "Höhe", "Tiefe", "Durchmesser")
    material_names = ("Material", "Polsterstoff")
    set_dimensions = [
        (name, values)
        for name, values in draft.aspects.items()
        if name.startswith("Maße ")
    ]
    return render_to_string(
        "ebay/listing_description.html",
        {
            "title": draft.title,
            "description": draft.description,
            "ean": ean,
            "images": images,
            "set_dimensions": set_dimensions,
            "dimensions": [
                (name, draft.aspects[name])
                for name in dimension_names
                if draft.aspects.get(name)
            ],
            "materials": [
                (name, draft.aspects[name])
                for name in material_names
                if draft.aspects.get(name)
            ],
            "aspects": sorted(
                (name, values)
                for name, values in draft.aspects.items()
                if name not in (*dimension_names, *material_names, "Farbe")
                and not name.startswith("Maße ")
            ),
            "colors": draft.aspects.get("Farbe", []),
        },
    ).strip()
