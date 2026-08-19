from io import BytesIO

import ftplib
from itertools import count

import pytest
import requests
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APIClient

from apps.catalog.models import Category
from apps.products.models import Product, ProductImage, ProductVariant


User = get_user_model()


@pytest.fixture(autouse=True)
def block_external_network(monkeypatch):
    """A test must explicitly mock every external HTTP or FTP interaction."""

    def blocked(*args, **kwargs):
        raise AssertionError("External network access is forbidden in tests.")

    monkeypatch.setattr(requests.sessions.Session, "request", blocked)
    monkeypatch.setattr(ftplib.FTP, "connect", blocked)
    monkeypatch.setattr(ftplib.FTP_TLS, "connect", blocked)


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def password():
    return "N7$kP4mQ2#zL"


@pytest.fixture
def user_factory(db, password):
    sequence = count(1)

    def create(*, role=User.Role.SELLER, username=None, **kwargs):
        username = username or f"user_{next(sequence)}"
        return User.objects.create_user(
            username=username,
            password=kwargs.pop("password", password),
            role=role,
            **kwargs,
        )

    return create


@pytest.fixture
def seller(user_factory):
    return user_factory(username="seller")


@pytest.fixture
def second_seller(user_factory):
    return user_factory(username="second_seller")


@pytest.fixture
def manager(user_factory):
    return user_factory(username="manager", role=User.Role.MANAGER)


@pytest.fixture
def admin_user(user_factory):
    return user_factory(
        username="admin",
        role=User.Role.ADMIN,
        is_staff=True,
        is_superuser=True,
    )


@pytest.fixture
def product_type():
    return "Chair"


@pytest.fixture
def category(db):
    return Category.objects.create(name="Living room furniture")


@pytest.fixture
def product_factory(db, product_type):
    def create(
        *,
        owner,
        status=Product.Status.DRAFT,
        title="Wooden chair",
        with_variant=True,
        **kwargs,
    ):
        product = Product.objects.create(
            owner=owner,
            title=title,
            product_type=product_type,
            unit_price=kwargs.pop("unit_price", "100.00"),
            currency=kwargs.pop("currency", Product.Currency.TRY),
            # A valid local OTTO category is required when a product is sent
            # to moderation. Tests which are not about categories should not
            # fail because of an incomplete product fixture.
            otto_category_id=kwargs.pop("otto_category_id", 26822),
            otto_category_group_id=kwargs.pop(
                "otto_category_group_id",
                3593,
            ),
            otto_category_name=kwargs.pop(
                "otto_category_name",
                "Esszimmerstuhl",
            ),
            otto_category_group_name=kwargs.pop(
                "otto_category_group_name",
                "Stühle",
            ),
            status=status,
            **kwargs,
        )
        if with_variant:
            ProductVariant.objects.create(
                product=product,
                color_hex="#5B91C8",
                materials=["Wood", "Fabric"],
                width_cm="50.00",
                height_cm="90.00",
                length_cm="55.00",
                quantity=3,
            )
        return product

    return create


@pytest.fixture
def image_file():
    def create(name="product.png"):
        buffer = BytesIO()
        Image.new("RGB", (2, 2), color="white").save(buffer, format="PNG")
        return SimpleUploadedFile(
            name,
            buffer.getvalue(),
            content_type="image/png",
        )

    return create


@pytest.fixture
def product_image_factory(db, image_file):
    def create(*, product, is_primary=True, **kwargs):
        position = kwargs.pop(
            "position",
            ProductImage.objects.filter(product=product).count(),
        )
        return ProductImage.objects.create(
            product=product,
            image=image_file(),
            is_primary=is_primary,
            position=position,
            **kwargs,
        )

    return create
