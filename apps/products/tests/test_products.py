from io import BytesIO

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework import status
from rest_framework.test import APIClient

from apps.catalog.models import Color, Material, ProductType
from apps.products.models import Product, ProductVariant


User = get_user_model()

PRODUCTS_URL = "/api/v1/products/"


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def seller():
    return User.objects.create_user(
        username="seller",
        password="N7$kP4mQ2#zL",
    )


@pytest.fixture
def second_seller():
    return User.objects.create_user(
        username="second_seller",
        password="N7$kP4mQ2#zL",
    )


@pytest.fixture
def manager():
    return User.objects.create_user(
        username="manager",
        password="N7$kP4mQ2#zL",
        role=User.Role.MANAGER,
    )


@pytest.fixture
def catalog_data():
    return {
        "product_type": ProductType.objects.create(name="Chair"),
        "color": Color.objects.create(
            name="Black",
            hex_code="#000000",
        ),
        "material": Material.objects.create(name="Wood"),
    }


def create_product(*, owner, catalog_data, status_value=Product.Status.DRAFT):
    product = Product.objects.create(
        owner=owner,
        title="Wooden chair",
        product_type=catalog_data["product_type"],
        status=status_value,
    )

    ProductVariant.objects.create(
        product=product,
        color=catalog_data["color"],
        material=catalog_data["material"],
        width_cm="50.00",
        height_cm="90.00",
        length_cm="55.00",
        quantity=3,
    )

    return product


def get_test_image():
    buffer = BytesIO()
    Image.new("RGB", (1, 1), color="white").save(buffer, format="PNG")

    return SimpleUploadedFile(
        "chair.png",
        buffer.getvalue(),
        content_type="image/png",
    )


@pytest.mark.django_db
def test_seller_can_create_product(api_client, seller, catalog_data):
    api_client.force_authenticate(user=seller)

    response = api_client.post(
        PRODUCTS_URL,
        {
            "title": "Wooden chair",
            "product_type": catalog_data["product_type"].id,
            "variants": [
                {
                    "color": catalog_data["color"].id,
                    "material": catalog_data["material"].id,
                    "width_cm": "50.00",
                    "height_cm": "90.00",
                    "length_cm": "55.00",
                    "quantity": 3,
                }
            ],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED

    product = Product.objects.get(title="Wooden chair")
    assert product.owner == seller
    assert product.status == Product.Status.DRAFT
    assert product.variants.count() == 1


@pytest.mark.django_db
def test_seller_can_only_see_own_products(
    api_client,
    seller,
    second_seller,
    catalog_data,
):
    own_product = create_product(
        owner=seller,
        catalog_data=catalog_data,
    )
    create_product(
        owner=second_seller,
        catalog_data=catalog_data,
    )

    api_client.force_authenticate(user=seller)

    response = api_client.get(PRODUCTS_URL)

    assert response.status_code == status.HTTP_200_OK
    assert response.data["count"] == 1
    assert response.data["results"][0]["id"] == own_product.id


@pytest.mark.django_db
def test_seller_uploads_image_and_submits_product(
    api_client,
    seller,
    catalog_data,
):
    product = create_product(
        owner=seller,
        catalog_data=catalog_data,
    )

    api_client.force_authenticate(user=seller)

    upload_response = api_client.post(
        f"/api/v1/products/{product.id}/images/",
        {
            "image": get_test_image(),
            "is_primary": True,
        },
        format="multipart",
    )

    assert upload_response.status_code == status.HTTP_201_CREATED
    assert product.images.count() == 1

    submit_response = api_client.post(
        f"/api/v1/products/{product.id}/submit/",
        format="json",
    )

    assert submit_response.status_code == status.HTTP_200_OK

    product.refresh_from_db()
    assert product.status == Product.Status.SUBMITTED


@pytest.mark.django_db
def test_manager_can_reject_submitted_product(
    api_client,
    seller,
    manager,
    catalog_data,
):
    product = create_product(
        owner=seller,
        catalog_data=catalog_data,
        status_value=Product.Status.SUBMITTED,
    )

    api_client.force_authenticate(user=manager)

    response = api_client.post(
        f"/api/v1/manager/products/{product.id}/reject/",
        {
            "comment": "Please upload a clearer image.",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK

    product.refresh_from_db()
    assert product.status == Product.Status.REJECTED
    assert product.moderation_decisions.count() == 1
