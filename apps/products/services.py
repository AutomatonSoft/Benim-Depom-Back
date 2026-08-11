from typing import Any

from django.db import transaction
from django.db.models import F, Max
from rest_framework.exceptions import ValidationError

from .models import Product, ProductImage, ProductVariant


@transaction.atomic
def create_product(
    *,
    owner,
    data: dict[str, Any],
    variants_data: list[dict[str, Any]]
) -> Product:
    product = Product.objects.create(owner=owner, **data)

    ProductVariant.objects.bulk_create(
        [
            ProductVariant(product=product, **variant_data)
            for variant_data in variants_data
        ]
    )

    return product


@transaction.atomic
def update_product(
    *,
    product: Product,
    data: dict[str, Any],
    variants_data: list[dict[str, Any]] | None,
) -> Product:
    for field, value in data.items():
        setattr(product, field, value)

    product.save()

    if variants_data is not None:
        product.variants.all().delete()

        ProductVariant.objects.bulk_create(
            [
                ProductVariant(product=product, **variant_data)
                for variant_data in variants_data
            ]
        )

    return product

EDITABLE_PRODUCT_STATUSES = {
    Product.Status.DRAFT,
    Product.Status.REJECTED
}

def ensure_product_is_editable(product: Product) -> None:
    if product.status not in EDITABLE_PRODUCT_STATUSES:
        raise ValidationError(
            {
                "detail": (
                    "Only draft or rejected products can be changed"
                )
            }
        )


@transaction.atomic
def upload_product_image(
    *,
    product: Product,
    image_file,
    is_primary: bool,
) -> ProductImage:

    locked_product = Product.objects.select_for_update().get(pk=product.pk)
    ensure_product_is_editable(locked_product)

    images_queryset = ProductImage.objects.select_for_update().filter(
        product=locked_product
    )

    if images_queryset.count() >= 10:
        raise ValidationError(
            {"image": "A product cannot have more than 10 images"}
        )

    max_position = images_queryset.aggregate(
        max_position=Max("position")
    )["max_position"]

    position = 0 if max_position is None else max_position + 1
    has_primary = images_queryset.filter(is_primary=True).exists()

    if is_primary or not has_primary:
        images_queryset.update(is_primary=False)
        is_primary=True

    return ProductImage.objects.create(
        product = locked_product,
        image = image_file,
        position = position,
        is_primary=is_primary
    )

@transaction.atomic
def delete_product_image(
    *,
    product: Product,
    image: ProductImage,
) -> None:
    locked_product = Product.objects.select_for_update().get(pk=product.pk)
    ensure_product_is_editable(locked_product)

    image = ProductImage.objects.select_for_update().get(
        pk=image.pk,
        product=locked_product
    )

    image_files = [
        image.image,
        image.processed_image,
    ]
    was_primary = image.is_primary

    image.delete()

    if was_primary:
        next_image = (
            ProductImage.objects.filter(product=locked_product)
            .order_by("position", "id")
            .first()
        )

        if next_image:
            next_image.is_primary = True
            next_image.save(update_fields=("is_primary",))

    def remove_files():
        for image_file in image_files:
            if image_file:
                image_file.delete(save=False)


    transaction.on_commit(remove_files)


@transaction.atomic
def make_product_image_primary(
    *,
    product: Product,
    image: ProductImage
) -> ProductImage:
    locked_product = Product.objects.select_for_update().get(pk=product.pk)
    ensure_product_is_editable(locked_product)

    image = ProductImage.objects.select_for_update().get(
        pk=image.pk,
        product=locked_product
    )

    ProductImage.objects.filter(product=locked_product).update(
        is_primary=False
    )

    image.is_primary = True
    image.save(update_fields=("is_primary",))

    return image

@transaction.atomic
def reorder_product_images(
    *,
    product: Product,
    image_ids: list[int],
) -> None:
    locked_product = Product.objects.select_for_update().get(pk=product.pk)
    ensure_product_is_editable(locked_product)


    images = list(
        ProductImage.objects.select_for_update()
        .filter(product=locked_product)
        .order_by("position", "id")
    )

    current_image_ids = {image.id for image in images}

    if set(image_ids) != current_image_ids:
        raise ValidationError(
            {
                "image_ids": (
                    "The list must contain every image of this product"
                    "exactly once."
                )
            }
        )

    max_position = max(
        (image.position for image in images),
        default=0
    )

    ProductImage.objects.filter(
        product=locked_product
    ).update(position=F("position") + max_position + len(images) + 1)

    for position, image_id in enumerate(image_ids):
        ProductImage.objects.filter(
            product=locked_product,
            pk=image_id,
        ).update(position=position)

        
