from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image

from apps.products.serializers import ProductImageUploadSerializer


def image_upload(
    *,
    image_format="PNG",
    content_type="image/png",
    size=(4, 4),
):
    buffer = BytesIO()
    Image.new("RGB", size, color="white").save(
        buffer,
        format=image_format,
    )

    return SimpleUploadedFile(
        f"image.{image_format.lower()}",
        buffer.getvalue(),
        content_type=content_type,
    )


@pytest.mark.unit
def test_accepts_safe_image_and_restores_file_position():
    uploaded_image = image_upload()

    result = ProductImageUploadSerializer().validate_image(
        uploaded_image
    )

    assert result is uploaded_image
    assert uploaded_image.tell() == 0


@pytest.mark.unit
def test_rejects_corrupted_image_file():
    uploaded_image = SimpleUploadedFile(
        "not-an-image.jpg",
        b"this is not an image",
        content_type="image/jpeg",
    )

    with pytest.raises(Exception, match="valid safe image"):
        ProductImageUploadSerializer().validate_image(uploaded_image)


@pytest.mark.unit
def test_rejects_declared_mime_type_that_differs_from_real_format():
    # Actual file is PNG, but client falsely declares JPEG.
    uploaded_image = image_upload(
        image_format="PNG",
        content_type="image/jpeg",
    )

    with pytest.raises(Exception, match="does not match"):
        ProductImageUploadSerializer().validate_image(uploaded_image)


@pytest.mark.unit
@override_settings(PRODUCT_IMAGE_MAX_PIXELS=10)
def test_rejects_image_with_too_many_pixels():
    # 4 x 4 = 16 pixels; configured test limit is 10.
    uploaded_image = image_upload(size=(4, 4))

    with pytest.raises(Exception, match="dimensions are too large"):
        ProductImageUploadSerializer().validate_image(uploaded_image)


@pytest.mark.unit
@override_settings(PRODUCT_IMAGE_MAX_UPLOAD_BYTES=3)
def test_rejects_image_larger_than_allowed_file_size():
    uploaded_image = image_upload()

    with pytest.raises(Exception, match="must not exceed"):
        ProductImageUploadSerializer().validate_image(uploaded_image)