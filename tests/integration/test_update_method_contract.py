import pytest


@pytest.mark.integration
@pytest.mark.django_db
def test_profile_and_product_expose_patch_not_put(api_client, seller, product_factory):
    """Avoid duplicate full-replacement update endpoints in the public API."""
    api_client.force_authenticate(seller)
    product = product_factory(owner=seller)

    assert (
        api_client.put(
            "/api/v1/auth/me/", {"first_name": "New"}, format="json"
        ).status_code
        == 405
    )
    assert (
        api_client.put(
            f"/api/v1/products/{product.id}/", {"title": "New title"}, format="json"
        ).status_code
        == 405
    )
