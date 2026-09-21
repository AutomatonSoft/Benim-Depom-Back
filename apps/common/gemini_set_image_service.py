from __future__ import annotations

from io import BytesIO
from math import ceil
from typing import Any, Callable

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from PIL import Image, ImageDraw, ImageFont, ImageOps

from apps.marketplace.set_listing import iter_set_parts

SET_LISTING_IMAGE_MODES = ("white", "interior", "human")
_MAX_REFERENCE_IMAGES = 10
_MAX_REFERENCE_EDGE = 1600

_SCENE_INSTRUCTIONS = {
    "white": (
        "Scene: seamless pure white studio background, even catalog lighting, "
        "no props, no textured floor, no room. Packshot of EVERY piece of the "
        "set standing together in one group."
    ),
    "interior": (
        "Scene: a real modern European interior that matches this furniture. "
        "Natural window light. Arrange EVERY unique piece together as one room "
        "group. No extra furniture that is not in the set."
    ),
    "human": (
        "Scene: the same kind of modern interior, with one adult person "
        "standing next to the complete set. Photorealistic. The person must "
        "not hide the furniture. No extra furniture."
    ),
}


class GeminiSetImageServiceError(Exception):
    """Safe error for set-image generation; never contains API credentials."""


def _format_cm(value: Any) -> str:
    """Format a centimetre value without trailing zeros."""
    text = str(value or "").strip()
    if not text:
        return ""
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _jpeg_bytes(image: Image.Image, *, quality: int = 88) -> bytes:
    """Encode a RGB image as JPEG bytes for the Gemini request."""
    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


def _piece_lines(product) -> list[str]:
    """Build the unique-piece list for the prompt."""
    lines: list[str] = []
    variant = product.variants.order_by("id").first()
    if variant is not None:
        lines.append(
            "1. Main piece — "
            f"{product.product_type}: "
            f"W {_format_cm(variant.width_cm)} x "
            f"H {_format_cm(variant.height_cm)} x "
            f"L {_format_cm(variant.length_cm)} cm, "
            f"colour {variant.color}, "
            f"materials {', '.join(variant.materials or [])}."
        )
    for index, part in enumerate(iter_set_parts(product), start=2):
        lines.append(
            f"{index}. {part.description.strip()}: "
            f"W {_format_cm(part.width_cm)} x "
            f"H {_format_cm(part.height_cm)} x "
            f"L {_format_cm(part.length_cm)} cm."
        )
    return lines


def build_set_image_prompt(*, product, mode: str, reference_count: int) -> str:
    """Build the Gemini prompt for one listing scene of a furniture set."""
    if mode not in _SCENE_INSTRUCTIONS:
        raise GeminiSetImageServiceError(f"Unsupported listing image mode: {mode}.")

    pieces = _piece_lines(product)
    piece_count = len(pieces) or 1
    piece_block = "\n".join(pieces) or "1. One furniture set."
    return (
        "TASK: create ONE photorealistic marketplace photo of a COMPLETE "
        "furniture SET in a single frame.\n"
        f"Product title: {product.title}\n"
        f"Product type: {product.product_type}\n"
        f"Required unique pieces: {piece_count}\n"
        f"Seller reference photos attached: {reference_count} "
        "(plus one combined reference sheet).\n\n"
        "Each attached photo is usually ONE piece of the set. Do not copy a "
        "single photo. Combine the unique pieces from ALL photos into one "
        "new scene.\n"
        "If two photos show the same piece, use that piece only once.\n\n"
        f"Every piece below MUST be visible in the photo, together:\n"
        f"{piece_block}\n\n"
        f"{_SCENE_INSTRUCTIONS[mode]}\n\n"
        "FAIL if the photo shows only the main piece or only the first photo. "
        "FAIL if any listed piece is missing. FAIL if you invent extra furniture.\n"
        "Keep real colours, materials, shapes and proportions from the photos. "
        "No logos, no watermarks, no captions, no text overlay."
    )


def _reference_images(product) -> list[Image.Image]:
    """Load seller photos, shrinking them for the model input limit."""
    images: list[Image.Image] = []
    for product_image in product.images.all().order_by("position", "id")[
        :_MAX_REFERENCE_IMAGES
    ]:
        if not product_image.image:
            continue
        product_image.image.open("rb")
        try:
            loaded = Image.open(product_image.image)
            loaded = ImageOps.exif_transpose(loaded)
            loaded = loaded.convert("RGB")
            loaded.thumbnail(
                (_MAX_REFERENCE_EDGE, _MAX_REFERENCE_EDGE),
                Image.Resampling.LANCZOS,
            )
            images.append(loaded)
        finally:
            product_image.image.close()
    if not images:
        raise GeminiSetImageServiceError("The product has no source photos.")
    return images


def _reference_sheet(images: list[Image.Image]) -> Image.Image:
    """Tile every seller photo into one sheet so the model sees all pieces."""
    count = len(images)
    columns = 2 if count > 1 else 1
    rows = ceil(count / columns)
    cell = 768
    sheet = Image.new("RGB", (cell * columns, cell * rows), (255, 255, 255))
    try:
        font = ImageFont.load_default(size=28)
    except TypeError:
        font = ImageFont.load_default()
    for index, source in enumerate(images):
        tile = source.copy()
        tile.thumbnail((cell - 24, cell - 24), Image.Resampling.LANCZOS)
        left = (index % columns) * cell + (cell - tile.width) // 2
        top = (index // columns) * cell + (cell - tile.height) // 2
        sheet.paste(tile, (left, top))
        ImageDraw.Draw(sheet).text(
            ((index % columns) * cell + 10, (index // columns) * cell + 8),
            f"{index + 1}",
            fill=(20, 20, 20),
            font=font,
        )
    return sheet


def _image_bytes_from_response(response) -> bytes:
    """Read the first inline image from a Gemini response."""
    parts = list(getattr(response, "parts", None) or [])
    if not parts:
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            content = getattr(candidates[0], "content", None)
            parts = list(getattr(content, "parts", None) or [])

    for part in parts:
        inline = getattr(part, "inline_data", None)
        data = getattr(inline, "data", None) if inline is not None else None
        if data:
            if isinstance(data, str):
                from base64 import b64decode

                return b64decode(data)
            return bytes(data)
        as_image = getattr(part, "as_image", None)
        if callable(as_image):
            rendered = as_image()
            if rendered is not None:
                return _jpeg_bytes(rendered, quality=90)

    raise GeminiSetImageServiceError("Gemini returned no image.")


def generate_set_listing_images(
    *,
    product,
    on_mode_start: Callable[[str], None] | None = None,
) -> dict[str, bytes]:
    """Generate white, interior and human photos of the complete set."""
    if not settings.GOOGLE_API_KEY:
        raise ImproperlyConfigured(
            "Set image generation is not configured. Set GOOGLE_API_KEY."
        )

    try:
        from google import genai
        from google.genai import types
    except ImportError as error:
        raise ImproperlyConfigured(
            "The google-genai package is required for set image generation."
        ) from error

    client = genai.Client(api_key=settings.GOOGLE_API_KEY)
    references = _reference_images(product)
    sheet = _reference_sheet(references)
    generated: dict[str, bytes] = {}

    for mode in SET_LISTING_IMAGE_MODES:
        if on_mode_start is not None:
            on_mode_start(mode)
        parts = [
            types.Part.from_text(
                text=build_set_image_prompt(
                    product=product,
                    mode=mode,
                    reference_count=len(references),
                )
            ),
            types.Part.from_text(
                text=(
                    "Combined reference sheet. Each cell is a different seller "
                    "photo. Use every unique piece from this sheet."
                )
            ),
            types.Part.from_bytes(data=_jpeg_bytes(sheet), mime_type="image/jpeg"),
        ]
        for index, reference in enumerate(references, start=1):
            parts.append(
                types.Part.from_text(
                    text=f"Seller photo {index} of {len(references)}:"
                )
            )
            parts.append(
                types.Part.from_bytes(
                    data=_jpeg_bytes(reference),
                    mime_type="image/jpeg",
                )
            )
        try:
            response = client.models.generate_content(
                model=settings.GEMINI_IMAGE_MODEL,
                contents=parts,
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    image_config=types.ImageConfig(aspect_ratio="4:3"),
                ),
            )
        except Exception as error:
            raise GeminiSetImageServiceError(
                "Gemini set image request failed."
            ) from error
        generated[mode] = _image_bytes_from_response(response)

    return generated
