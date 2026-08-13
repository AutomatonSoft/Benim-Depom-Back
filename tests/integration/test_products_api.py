import pytest

from apps.products.models import Product, ProductImage


def authenticate(client, user):
    client.force_authenticate(user=user)
    return client


def product_payload(product_type_id, **overrides):
    payload = {
        "title": "API chair",
        "product_type": product_type_id,
        "variants": [
            {
                "color_hex": "#5B91C8",
                "materials": ["Wood", "Fabric"],
                "width_cm": "50.00",
                "height_cm": "90.00",
                "length_cm": "55.00",
                "quantity": 3,
            }
        ],
    }
    payload.update(overrides)
    return payload


@pytest.mark.integration
@pytest.mark.django_db
def test_seller_creates_product_and_other_seller_cannot_access_it(
    api_client, seller, second_seller, product_type
):
    authenticate(api_client, seller)
    response = api_client.post("/api/v1/products/", product_payload(product_type.id), format="json")
    assert response.status_code == 201
    product_id = response.data["id"]
    assert response.data["status"] == Product.Status.DRAFT
    assert response.data["variants"][0]["color_hex"] == "#5B91C8"

    authenticate(api_client, second_seller)
    assert api_client.get(f"/api/v1/products/{product_id}/").status_code == 404
    assert api_client.patch(
        f"/api/v1/products/{product_id}/", {"title": "Steal"}, format="json"
    ).status_code == 404


@pytest.mark.integration
@pytest.mark.django_db
def test_product_api_rejects_invalid_variant_and_non_seller_create(api_client, manager, seller, product_type):
    authenticate(api_client, seller)
    response = api_client.post(
        "/api/v1/products/",
        product_payload(product_type.id, variants=[]),
        format="json",
    )
    assert response.status_code == 400

    response = api_client.post(
        "/api/v1/products/",
        product_payload(product_type.id, variants=[{**product_payload(product_type.id)["variants"][0], "color_hex": "blue"}]),
        format="json",
    )
    assert response.status_code == 400

    authenticate(api_client, manager)
    assert api_client.post("/api/v1/products/", product_payload(product_type.id), format="json").status_code == 403


@pytest.mark.integration
@pytest.mark.django_db
def test_product_image_operations_and_edit_lock(api_client, seller, product_factory, product_image_factory, image_file):
    product = product_factory(owner=seller)
    first = product_image_factory(product=product, is_primary=True)
    second = product_image_factory(product=product, is_primary=False)
    authenticate(api_client, seller)

    response = api_client.post(
        f"/api/v1/products/{product.id}/images/reorder/",
        {"image_ids": [second.id, first.id]},
        format="json",
    )
    assert response.status_code == 204
    assert list(ProductImage.objects.filter(product=product).order_by("position").values_list("id", flat=True)) == [second.id, first.id]

    response = api_client.post(f"/api/v1/products/{product.id}/images/{second.id}/make-primary/")
    assert response.status_code == 200
    second.refresh_from_db()
    assert second.is_primary is True

    response = api_client.post(
        f"/api/v1/products/{product.id}/images/",
        {"image": image_file("new.png"), "is_primary": False},
        format="multipart",
    )
    assert response.status_code == 201

    response = api_client.post(
        f"/api/v1/products/{product.id}/images/reorder/", {"image_ids": [first.id]}, format="json"
    )
    assert response.status_code == 400

    product.status = Product.Status.SUBMITTED
    product.save(update_fields=["status"])
    assert api_client.post(
        f"/api/v1/products/{product.id}/images/", {"image": image_file()}, format="multipart"
    ).status_code == 404


@pytest.mark.integration
@pytest.mark.django_db
def test_seller_cannot_deactivate_draft_or_delete_submitted_product(api_client, seller, product_factory):
    product = product_factory(owner=seller)
    authenticate(api_client, seller)
    assert api_client.post(f"/api/v1/products/{product.id}/deactivate/").status_code == 400

    product.status = Product.Status.SUBMITTED
    product.save(update_fields=["status"])
    assert api_client.delete(f"/api/v1/products/{product.id}/").status_code == 403


@pytest.mark.integration
@pytest.mark.django_db
def test_seller_confirms_availability_only_for_approved_product(api_client, seller, product_factory):
    product = product_factory(owner=seller)
    authenticate(api_client, seller)
    assert api_client.post(
        f"/api/v1/products/{product.id}/availability/", {"is_available": False}, format="json"
    ).status_code == 400

    product.status = Product.Status.APPROVED
    product.save(update_fields=["status"])
    response = api_client.post(
        f"/api/v1/products/{product.id}/availability/", {"is_available": False}, format="json"
    )
    assert response.status_code == 200
    product.refresh_from_db()
    assert product.is_available is False
