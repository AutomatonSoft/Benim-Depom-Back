import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from apps.common.gemini_set_image_service import (
    GeminiSetImageServiceError,
    build_set_image_prompt,
    generate_set_listing_images,
)
from apps.products.models import ProductSetPart


@pytest.mark.unit
@pytest.mark.django_db
def test_set_image_prompt_lists_pieces_and_forbids_duplicates(product_factory, seller):
    product = product_factory(owner=seller)
    ProductSetPart.objects.create(
        product=product,
        position=0,
        description="Wardrobe with 4 doors",
        width_cm="220.00",
        height_cm="210.00",
        length_cm="60.00",
    )

    prompt = build_set_image_prompt(product=product, mode="white", reference_count=4)

    assert "Required unique pieces: 2" in prompt
    assert "Wardrobe with 4 doors" in prompt
    assert "FAIL if the photo shows only the main piece" in prompt
    assert "seamless pure white studio" in prompt


@pytest.mark.unit
@pytest.mark.django_db
@override_settings(GOOGLE_API_KEY="")
def test_set_image_generation_requires_api_key(product_factory, seller):
    product = product_factory(owner=seller)
    with pytest.raises(ImproperlyConfigured, match="GOOGLE_API_KEY"):
        generate_set_listing_images(product=product)


@pytest.mark.unit
@pytest.mark.django_db
def test_unknown_set_image_mode_is_rejected(product_factory, seller):
    product = product_factory(owner=seller)
    with pytest.raises(GeminiSetImageServiceError, match="Unsupported"):
        build_set_image_prompt(product=product, mode="collage", reference_count=1)
