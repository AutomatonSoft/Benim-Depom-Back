"""Local Depotum draft preparation shared by the manager form and common AI apply."""

import re
from decimal import Decimal

from django.conf import settings
from rest_framework.exceptions import ValidationError

from apps.marketplace.set_listing import _format_cm, iter_set_parts
from apps.products.listing_images import public_generated_listing_urls
from apps.products.pricing import safe_listing_price_eur

COMPANY = {
    "companyName": "AEA GmbH & Co. KG",
    "addressLine1": "Am Flugplatz 28",
    "city": "Burgrieden",
    "postalCode": "88483",
    "country": "DE",
    "email": "info@jvmoebel.de",
}


def listing_title(value):
    base = re.sub(r"\s*(?:\(BD\)|\bBD)\s*$", "", value, flags=re.I).strip()
    if len(base) > 75:
        raise ValidationError(
            {"title": "eBay title must contain at most 75 characters before BD."}
        )
    return f"{base} BD" if base else ""


def manager_draft_values(product, *, draft=None, values=None):
    """Use card facts and agreed Depotum settings; never invent category/aspect choices."""
    values = dict(values or {})
    aspects = dict(values.get("aspects", getattr(draft, "aspects", {})))
    category_id = values.get("category_id", getattr(draft, "category_id", ""))
    variants = list(product.variants.all())
    main = variants[0] if variants else None
    if category_id and main:
        from .catalog import get_category_aspects

        aspect_names = {
            item["localizedAspectName"]
            for item in get_category_aspects(category_id=category_id)["aspects"]
        }
        for name, field in (
            ("Breite", "width_cm"),
            ("Länge", "length_cm"),
            ("Höhe", "height_cm"),
        ):
            value = getattr(main, field, None)
            if name in aspect_names and value is not None and Decimal(str(value)) > 0:
                aspects[name] = [f"{_format_cm(value)} cm"]
        # Keep manager labels while replacing only our generated factual dimension rows.
        for name in list(aspects):
            if re.fullmatch(r"Maße Teil \d+ \(B × L × H\)", name):
                del aspects[name]
        for index, part in enumerate(iter_set_parts(product), start=2):
            sizes = [
                getattr(part, field, None)
                for field in ("width_cm", "length_cm", "height_cm")
            ]
            if all(value is not None and Decimal(str(value)) > 0 for value in sizes):
                dimensions = " × ".join(_format_cm(value) for value in sizes) + " cm"
                if not any(
                    name.startswith("Maße ")
                    and not re.fullmatch(r"Maße Teil \d+ \(B × L × H\)", name)
                    and dimensions in values
                    for name, values in aspects.items()
                ):
                    aspects[f"Maße Teil {index} (B × L × H)"] = [dimensions]
    title = values.get("title", getattr(draft, "title", ""))
    values.update(
        title=listing_title(title),
        image_urls=public_generated_listing_urls(product, account="jv"),
        price_eur=safe_listing_price_eur(product),
        quantity=sum(variant.quantity for variant in variants),
        aspects=aspects,
        regulatory={
            "manufacturer": dict(COMPANY),
            "responsiblePersons": [
                {
                    **COMPANY,
                    "phone": "+49 7392 9378440",
                    "types": ["EU_RESPONSIBLE_PERSON"],
                }
            ],
        },
        **settings.EBAY_DEPOTUM_LISTING_DEFAULTS,
    )
    return values


def apply_ai_content(product, content, *, overwrite):
    """Save common AI text locally, without contacting eBay or changing live listings."""
    from .models import EbayDraft, EbayOfferPreparation

    draft, created = EbayDraft.objects.select_for_update().get_or_create(
        product=product, environment=settings.EBAY_ENVIRONMENT, account="depotum"
    )
    if EbayOfferPreparation.objects.filter(
        draft=draft, status__in=("processing", "uncertain")
    ).exists():
        raise ValidationError("eBay operation is in progress or needs reconciliation.")
    changed = created
    skipped = []
    for field, value in (
        ("title", content.get("ebay_title", content["title"])),
        ("description", content["description"]),
    ):
        if not overwrite and getattr(draft, field):
            skipped.append(field)
            continue
        if field == "title":
            value = listing_title(value)
        if getattr(draft, field) != value:
            setattr(draft, field, value)
            changed = True
    if content.get("color"):
        if overwrite or not draft.aspects.get("Farbe"):
            draft.aspects = {**draft.aspects, "Farbe": [content["color"]]}
            changed = True
        else:
            skipped.append("color")
    # Category is deliberately left for the manager; card facts/settings sync on form save.
    if changed:
        draft.save()
    return changed, skipped
