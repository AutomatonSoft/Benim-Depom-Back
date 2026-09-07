from types import SimpleNamespace

import pytest

from apps.products.listing_images import public_generated_listing_urls

pytestmark = pytest.mark.unit


def test_listing_urls_use_cover_generated_images_only():
    seller_source = SimpleNamespace(url="https://cdn.example/seller.jpg")
    cover = SimpleNamespace(
        is_primary=True,
        image=seller_source,
        generated_images=SimpleNamespace(
            all=lambda: [
                SimpleNamespace(
                    mode="interior",
                    image=SimpleNamespace(url="https://cdn.example/interior.jpg"),
                ),
                SimpleNamespace(
                    mode="white",
                    image=SimpleNamespace(url="https://cdn.example/white.jpg"),
                ),
            ]
        ),
    )
    extra = SimpleNamespace(
        is_primary=False,
        image=seller_source,
        generated_images=SimpleNamespace(
            all=lambda: [
                SimpleNamespace(
                    mode="white",
                    image=SimpleNamespace(url="https://cdn.example/other-white.jpg"),
                )
            ]
        ),
    )
    product = SimpleNamespace(images=SimpleNamespace(all=lambda: [cover, extra]))

    assert public_generated_listing_urls(product) == [
        "https://cdn.example/white.jpg",
        "https://cdn.example/interior.jpg",
    ]
