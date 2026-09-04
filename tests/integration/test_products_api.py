import json

import pytest

from apps.notifications.models import Notification
from apps.products.models import Product, ProductImage


def authenticate(client, user):
    client.force_authenticate(user=user)
    return client


def product_payload(product_type, **overrides):
    payload = {
        "title": "API chair",
        "product_type": product_type,
        "unit_price": "1000.00",
        "currency": "TRY",
        "warehouse_city": "INE",
        "otto_category_id": 26822,
        "otto_category_group_id": 3593,
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


def multipart_product_payload(product_type, image_file, **overrides):
    payload = product_payload(product_type, **overrides)
    payload["variants"] = json.dumps(payload["variants"])
    payload["images"] = [image_file()]
    return payload


@pytest.mark.integration
@pytest.mark.django_db
def test_seller_creates_product_and_other_seller_cannot_access_it(
    api_client, seller, second_seller, product_type, image_file
):
    authenticate(api_client, seller)
    response = api_client.post(
        "/api/v1/products/",
        multipart_product_payload(product_type, image_file),
        format="multipart",
    )
    assert response.status_code == 201
    product_id = response.data["id"]
    assert response.data["status"] == Product.Status.SUBMITTED
    assert response.data["variants"][0]["color_hex"] == "#5B91C8"

    authenticate(api_client, second_seller)
    assert api_client.get(f"/api/v1/products/{product_id}/").status_code == 404
    assert (
        api_client.patch(
            f"/api/v1/products/{product_id}/", {"title": "Steal"}, format="json"
        ).status_code
        == 404
    )


@pytest.mark.integration
@pytest.mark.django_db
def test_seller_creates_product_with_initial_images_in_one_multipart_request(
    api_client,
    seller,
    product_type,
    image_file,
):
    authenticate(api_client, seller)
    payload = product_payload(product_type)
    payload.pop("otto_category_id")
    payload.pop("otto_category_group_id")
    payload["variants"] = json.dumps(payload["variants"])
    payload["images"] = [image_file("first.png"), image_file("second.png")]

    response = api_client.post(
        "/api/v1/products/",
        payload,
        format="multipart",
    )

    assert response.status_code == 201
    assert response.data["otto_category_id"] is None
    assert response.data["status"] == Product.Status.SUBMITTED
    assert len(response.data["images"]) == 2
    assert response.data["images"][0]["is_primary"] is True
    assert response.data["images"][1]["is_primary"] is False
    assert ProductImage.objects.filter(product_id=response.data["id"]).count() == 2


@pytest.mark.integration
@pytest.mark.django_db
def test_product_update_ignores_empty_otto_category_payload(
    api_client, seller, product_factory
):
    product = product_factory(owner=seller)
    authenticate(api_client, seller)
    response = api_client.patch(
        f"/api/v1/products/{product.id}/",
        {
            "warehouse_city": "IZM",
            "otto_category_id": None,
            "otto_category_group_id": None,
            "otto_attributes": {},
        },
        format="json",
    )
    assert response.status_code == 200
    assert response.data["warehouse_city"] == "IZM"
    assert response.data["otto_category_id"] == product.otto_category_id
    assert response.data["otto_category_group_id"] == product.otto_category_group_id


@pytest.mark.integration
@pytest.mark.django_db
def test_multipart_product_create_rejects_invalid_variants_and_too_many_images(
    api_client,
    seller,
    product_type,
    image_file,
):
    authenticate(api_client, seller)
    invalid_variants_response = api_client.post(
        "/api/v1/products/",
        {
            "title": "Multipart chair",
            "product_type": product_type,
            "unit_price": "1000.00",
            "currency": "TRY",
            "warehouse_city": "INE",
            "variants": "not-json",
            "images": [image_file("invalid.png")],
        },
        format="multipart",
    )

    assert invalid_variants_response.status_code == 400
    assert Product.objects.count() == 0

    too_many_images_response = api_client.post(
        "/api/v1/products/",
        {
            "title": "Multipart chair",
            "product_type": product_type,
            "unit_price": "1000.00",
            "currency": "TRY",
            "warehouse_city": "INE",
            "variants": json.dumps(product_payload(product_type)["variants"]),
            "images": [image_file(f"image-{index}.png") for index in range(11)],
        },
        format="multipart",
    )

    assert too_many_images_response.status_code == 400
    assert Product.objects.count() == 0


@pytest.mark.integration
@pytest.mark.django_db
def test_product_api_rejects_invalid_variant_and_non_seller_create(
    api_client, manager, seller, product_type, image_file
):
    authenticate(api_client, seller)
    response = api_client.post(
        "/api/v1/products/",
        multipart_product_payload(product_type, image_file, variants=[]),
        format="multipart",
    )
    assert response.status_code == 400

    response = api_client.post(
        "/api/v1/products/",
        multipart_product_payload(
            product_type,
            image_file,
            variants=[
                {**product_payload(product_type)["variants"][0], "color_hex": "blue"}
            ],
        ),
        format="multipart",
    )
    assert response.status_code == 400

    authenticate(api_client, manager)
    assert (
        api_client.post(
            "/api/v1/products/",
            multipart_product_payload(product_type, image_file),
            format="multipart",
        ).status_code
        == 403
    )


@pytest.mark.integration
@pytest.mark.django_db
def test_product_api_rejects_direct_ean_assignment(
    api_client, seller, product_type, image_file
):
    authenticate(api_client, seller)

    response = api_client.post(
        "/api/v1/products/",
        multipart_product_payload(
            product_type,
            image_file,
            ean_jv="4012345678901",
        ),
        format="multipart",
    )

    assert response.status_code == 400
    assert "ean_jv" in response.data


@pytest.mark.integration
@pytest.mark.django_db
def test_product_image_operations_and_edit_lock(
    api_client, seller, product_factory, product_image_factory, image_file
):
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
    assert list(
        ProductImage.objects.filter(product=product)
        .order_by("position")
        .values_list("id", flat=True)
    ) == [second.id, first.id]

    response = api_client.post(
        f"/api/v1/products/{product.id}/images/{second.id}/make-primary/"
    )
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
        f"/api/v1/products/{product.id}/images/reorder/",
        {"image_ids": [first.id]},
        format="json",
    )
    assert response.status_code == 400

    product.status = Product.Status.SUBMITTED
    product.save(update_fields=["status"])
    assert (
        api_client.post(
            f"/api/v1/products/{product.id}/images/",
            {"image": image_file()},
            format="multipart",
        ).status_code
        == 404
    )


@pytest.mark.integration
@pytest.mark.django_db
def test_seller_cannot_deactivate_draft_or_delete_submitted_product(
    api_client, seller, product_factory
):
    product = product_factory(owner=seller)
    authenticate(api_client, seller)
    assert (
        api_client.post(f"/api/v1/products/{product.id}/deactivate/").status_code == 400
    )

    product.status = Product.Status.SUBMITTED
    product.save(update_fields=["status"])
    assert api_client.delete(f"/api/v1/products/{product.id}/").status_code == 403


@pytest.mark.integration
@pytest.mark.django_db
def test_seller_can_resubmit_a_rejected_product_with_patch(
    api_client,
    seller,
    product_factory,
    product_image_factory,
):
    product = product_factory(owner=seller, status=Product.Status.REJECTED)
    product_image_factory(product=product)
    authenticate(api_client, seller)

    response = api_client.patch(
        f"/api/v1/products/{product.id}/",
        {
            "title": "Corrected chair",
            "resubmit_for_moderation": True,
        },
        format="json",
    )

    assert response.status_code == 200
    assert response.data["status"] == Product.Status.SUBMITTED
    product.refresh_from_db()
    assert product.status == Product.Status.SUBMITTED


@pytest.mark.integration
@pytest.mark.django_db
def test_seller_confirms_availability_only_for_approved_product(
    api_client, seller, manager, product_factory
):
    product = product_factory(owner=seller)
    authenticate(api_client, seller)
    assert (
        api_client.post(
            f"/api/v1/products/{product.id}/availability/",
            {"is_available": False},
            format="json",
        ).status_code
        == 400
    )

    product.status = Product.Status.APPROVED
    product.save(update_fields=["status"])
    reminder = Notification.objects.create(
        user=seller,
        product=product,
        notification_type=Notification.Type.PRODUCT_AVAILABILITY_REMINDER,
        title="Product availability",
        body="Do you still have this product available?",
    )
    other = Notification.objects.create(
        user=seller,
        product=product,
        notification_type=Notification.Type.MANAGER_MESSAGE,
        title="Hello",
        body="Please check stock when you can.",
    )
    response = api_client.post(
        f"/api/v1/products/{product.id}/availability/",
        {"is_available": False},
        format="json",
    )
    assert response.status_code == 200
    product.refresh_from_db()
    assert product.is_available is False
    reminder.refresh_from_db()
    other.refresh_from_db()
    assert reminder.responded_at is not None
    assert other.responded_at is None

    listed = api_client.get("/api/v1/notifications/")
    assert listed.status_code == 200
    row = next(item for item in listed.data["results"] if item["id"] == reminder.id)
    assert row["responded_at"] is not None
    assert not any(
        item["notification_type"] == Notification.Type.PRODUCT_CONFIRMATION
        for item in listed.data["results"]
    )

    authenticate(api_client, manager)
    manager_inbox = api_client.get("/api/v1/notifications/")
    assert manager_inbox.status_code == 200
    confirmation = next(
        item
        for item in manager_inbox.data["results"]
        if item["notification_type"] == Notification.Type.PRODUCT_CONFIRMATION
    )
    assert confirmation["product_id"] == product.id
    assert confirmation["product_title"] == product.title
    assert "not available" in confirmation["body"]
