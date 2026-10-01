from io import BytesIO
from pathlib import Path

from django.core.files.base import ContentFile
from PIL import Image, ImageOps

PRODUCT_IMAGE_MAX_EDGE = 1920
PRODUCT_IMAGE_THUMBNAIL_EDGE = 320


def _read_rgb_image(file) -> Image.Image:
    file.seek(0)
    with Image.open(file) as source:
        image = ImageOps.exif_transpose(source)
        if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
            rgba = image.convert("RGBA")
            image = Image.new("RGB", rgba.size, "white")
            image.paste(rgba, mask=rgba.getchannel("A"))
        else:
            image = image.convert("RGB")
    file.seek(0)
    return image


def _jpeg_content(image: Image.Image, *, filename: str, quality: int) -> ContentFile:
    output = BytesIO()
    image.save(output, format="JPEG", quality=quality, optimize=True, progressive=True)
    return ContentFile(output.getvalue(), name=filename)


def make_product_image_variants(file) -> tuple[ContentFile, ContentFile]:
    image = _read_rgb_image(file)
    image.thumbnail(
        (PRODUCT_IMAGE_MAX_EDGE, PRODUCT_IMAGE_MAX_EDGE),
        Image.Resampling.LANCZOS,
    )
    stem = Path(getattr(file, "name", "product-image")).stem
    full = _jpeg_content(image, filename=f"{stem}.jpg", quality=82)

    thumbnail = image.copy()
    thumbnail.thumbnail(
        (PRODUCT_IMAGE_THUMBNAIL_EDGE, PRODUCT_IMAGE_THUMBNAIL_EDGE),
        Image.Resampling.LANCZOS,
    )
    small = _jpeg_content(thumbnail, filename=f"{stem}-thumb.jpg", quality=68)
    file.seek(0)
    return full, small
