from unittest.mock import Mock

import pytest
from django.http import QueryDict
from rest_framework.exceptions import ValidationError

from apps.products.filters import filter_products
from apps.products.models import Product, ProductImage
from apps.products.services import (
    confirm_product_availability,
    create_product,
    deactivate_product,
    delete_product_image,
    make_product_image_primary,
    reorder_product_images,
    request_product_deactivation,
    request_product_image_processing,
    update_product,
    upload_product_image,
)


def variant_data(**overrides):
    data = {
        "color_hex": "#ABCDEF",
        "materials": ["Wood"],
        "width_cm": "10.00",
        "height_cm": "20.00",
        "length_cm": "30.00",
        "quantity": 2,
    }
    data.update(overrides)
    return data


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_product_create_update_and_status_guards(seller, product_type):
    product = create_product(
        owner=seller,
        data={
            "title": "Initial",
            "product_type": product_type,
            "unit_price": "1000.00",
            "currency": Product.Currency.TRY,
            "otto_category_id": 26822,
            "otto_category_group_id": 3593,
            "otto_category_name": "Esszimmerstuhl",
            "otto_category_group_name": "Stühle",
        },
        variants_data=[variant_data()],
    )
    assert product.variants.count() == 1
    product = update_product(
        product=product,
        data={"title": "Updated"},
        variants_data=[variant_data(materials=["Metal"], quantity=7)],
    )
    assert product.title == "Updated"
    assert product.variants.get().materials == ["Metal"]

    with pytest.raises(ValidationError, match="approved product"):
        confirm_product_availability(product=product, is_available=False)
    with pytest.raises(ValidationError, match="listing-state"):
        deactivate_product(product=product)


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_image_service_primary_delete_reorder_and_limit(
    seller, product_factory, product_image_factory, image_file
):
    product = product_factory(owner=seller)
    first = upload_product_image(
        product=product, image_file=image_file("first.png"), is_primary=False
    )
    second = upload_product_image(
        product=product, image_file=image_file("second.png"), is_primary=False
    )
    assert first.is_primary is True and second.is_primary is False

    make_product_image_primary(product=product, image=second)
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.is_primary is False and second.is_primary is True

    reorder_product_images(product=product, image_ids=[second.id, first.id])
    assert list(
        ProductImage.objects.filter(product=product).values_list("id", flat=True)
    ) == [second.id, first.id]
    with pytest.raises(ValidationError, match="every image"):
        reorder_product_images(product=product, image_ids=[first.id])

    delete_product_image(product=product, image=second)
    first.refresh_from_db()
    assert first.is_primary is True

    for index in range(9):
        upload_product_image(
            product=product,
            image_file=image_file(f"extra-{index}.png"),
            is_primary=False,
        )
    assert ProductImage.objects.filter(product=product).count() == 10
    with pytest.raises(ValidationError, match="more than 10"):
        upload_product_image(
            product=product, image_file=image_file("too-many.png"), is_primary=False
        )


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_processing_request_and_approved_availability_services(
    monkeypatch, seller, manager, product_factory, product_image_factory
):
    product = product_factory(owner=seller, status=Product.Status.SUBMITTED)
    image = product_image_factory(product=product)
    delay = Mock()
    monkeypatch.setattr("apps.notifications.tasks.process_product_image.delay", delay)

    with pytest.raises(ValidationError, match="after the product is approved"):
        request_product_image_processing(product=product, image=image)
    image.refresh_from_db()
    assert image.processing_status == ProductImage.ProcessingStatus.IDLE

    product.status = Product.Status.APPROVED
    product.save(update_fields=["status"])
    request_product_image_processing(product=product, image=image)
    image.refresh_from_db()
    assert image.processing_status == ProductImage.ProcessingStatus.PENDING
    delay.assert_called_once_with(image.id)
    secondary = product_image_factory(product=product, is_primary=False)
    with pytest.raises(ValidationError, match="cover photo"):
        request_product_image_processing(product=product, image=secondary)
    image.processing_status = ProductImage.ProcessingStatus.PROCESSING
    image.save(update_fields=["processing_status"])
    with pytest.raises(ValidationError, match="already in progress"):
        request_product_image_processing(product=product, image=image)

    product.status = Product.Status.APPROVED
    product.save(update_fields=["status"])
    confirm_product_availability(product=product, is_available=False)
    product.refresh_from_db()
    assert (
        product.is_available is False and product.availability_confirmed_at is not None
    )
    request_product_deactivation(product=product)
    with pytest.raises(ValidationError, match="listing-state"):
        deactivate_product(product=product)
    product.refresh_from_db()
    assert product.status == Product.Status.APPROVED
    assert product.deactivation_requested_at is not None


@pytest.mark.integration
@pytest.mark.django_db
def test_product_filters_apply_all_business_fields_and_reject_bad_values(
    seller, product_type, product_factory
):
    matching = product_factory(owner=seller, title="Blue chair")
    other = product_factory(
        owner=seller, title="Red table", status=Product.Status.APPROVED
    )
    other.variants.update(color_hex="#000000", materials=["Metal"])
    params = QueryDict(
        f"search=chair&status=draft&product_type={product_type}"
        "&color_hex=%235B91C8&material=Fabric&is_available=true&ordering=title"
    )
    assert list(
        filter_products(queryset=Product.objects.all(), query_params=params)
    ) == [matching]

    ean_match = product_factory(owner=seller, title="Oak stool", ean_jv="4006381333931")
    assert list(
        filter_products(
            queryset=Product.objects.all(),
            query_params=QueryDict("search=4006381333931"),
        )
    ) == [ean_match]

    for key, value in (
        ("status", "unknown"),
        ("color_hex", "blue"),
        ("is_available", "yes"),
        ("ordering", "price"),
    ):
        with pytest.raises(ValidationError):
            filter_products(
                queryset=Product.objects.all(), query_params=QueryDict(f"{key}={value}")
            )
