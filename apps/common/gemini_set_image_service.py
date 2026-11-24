from __future__ import annotations

from io import BytesIO
from math import ceil
from typing import Any, Callable

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from PIL import Image, ImageDraw, ImageFont, ImageOps

from apps.marketplace.set_listing import iter_set_parts

SET_LISTING_IMAGE_MODES = ("white", "interior", "human")
_MAX_REFERENCE_IMAGES = 20
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
                types.Part.from_text(text=f"Seller photo {index} of {len(references)}:")
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


# --- Single-slot regeneration -------------------------------------------------

REGENERATABLE_MODES = ("white", "interior", "human")
_MAX_EXTRA_SELLER_PHOTOS = 4
_REGEN_ATTEMPTS = 2
_MIN_OUTPUT_EDGE = 512
# Aspect ratios accepted by Gemini image models.
_GEMINI_ASPECT_RATIOS = {
    "1:1": 1.0,
    "2:3": 2 / 3,
    "3:2": 3 / 2,
    "3:4": 3 / 4,
    "4:3": 4 / 3,
    "4:5": 4 / 5,
    "5:4": 5 / 4,
    "9:16": 9 / 16,
    "16:9": 16 / 9,
}

_SINGLE_SCENE_INSTRUCTIONS = {
    "white": (
        "Scene: e-commerce catalog packshot. Remove the room, floor, walls and "
        "every object that is not part of the product. The background must be "
        "perfectly pure white, RGB (255, 255, 255), uniform across the whole "
        "frame: no gray, off-white, gradient, texture, horizon line or floor. "
        "No cast or contact shadows on the background; keep natural shading on "
        "the product itself so its shape stays readable. Front three-quarter "
        "view at eye level. The whole product is centered, fully visible and "
        "uncropped, filling about 80-85% of the frame."
    ),
    "interior": (
        "Scene: a real, lived-in modern European interior whose style and "
        "colours suit this product. Soft natural window light, realistic "
        "shadows and reflections. The product is the clear hero: in sharp "
        "focus, fully visible, uncropped, at true real-world scale for the "
        "room. At most a few tasteful decor items that never cover the "
        "product. No other furniture of the same type as the product."
    ),
    "human": (
        "Scene: a real modern European interior that suits this product, with "
        "one adult person using or standing next to the product in a natural, "
        "relaxed way, so a buyer can judge its real size. The person is at "
        "true human scale and must not hide the product; the product stays "
        "fully visible, uncropped and in sharp focus. Natural anatomy, hands "
        "and face. No other furniture of the same type as the product."
    ),
}


def _product_spec_lines(product) -> list[str]:
    """Describe the product so the model can keep scale and materials."""
    lines = [f"Product title: {product.title}", f"Product type: {product.product_type}"]
    variant = product.variants.order_by("id").first()
    if variant is not None:
        size = " x ".join(
            f"{label} {_format_cm(value)}"
            for label, value in (
                ("W", variant.width_cm),
                ("H", variant.height_cm),
                ("L", variant.length_cm),
            )
            if _format_cm(value)
        )
        if size:
            lines.append(f"Real size: {size} cm")
        if variant.color:
            lines.append(f"Colour: {variant.color}")
        if variant.materials:
            lines.append(f"Materials: {', '.join(variant.materials)}")
    return lines


def build_single_regen_prompt(*, product, mode: str, has_white_reference: bool) -> str:
    """Build the Gemini prompt for one marketplace photo of a single product."""
    if mode not in _SINGLE_SCENE_INSTRUCTIONS:
        raise GeminiSetImageServiceError(f"Unsupported listing image mode: {mode}.")
    reference_note = (
        "The first attached image is the approved white-background packshot of "
        "this exact product: it is the most accurate reference for shape, "
        "colour, materials and proportions. The seller photos after it show "
        "the same product from other angles."
        if has_white_reference
        else "The attached seller photos all show this one product. The first "
        "seller photo is the main reference."
    )
    return (
        "TASK: create ONE new photorealistic marketplace photo of the exact "
        "furniture product shown in the attached images.\n"
        + "\n".join(_product_spec_lines(product))
        + "\n\n"
        + reference_note
        + "\n\n"
        + _SINGLE_SCENE_INSTRUCTIONS[mode]
        + "\n\nKeep the product identical to the references: same design, "
        "colour, materials, proportions, construction and details. Do not "
        "redesign, mirror, simplify or add parts. Show exactly one product. "
        "Create a fresh composition rather than copying any reference photo. "
        "No logos, watermarks, captions or text overlay."
    )


def _closest_aspect_ratio(width: int, height: int) -> str:
    """Pick the Gemini aspect ratio closest to the image being replaced."""
    if width <= 0 or height <= 0:
        return "4:3"
    ratio = width / height
    return min(
        _GEMINI_ASPECT_RATIOS,
        key=lambda name: abs(_GEMINI_ASPECT_RATIOS[name] - ratio),
    )


def _open_upload(file_field) -> Image.Image | None:
    """Load one stored image as RGB, shrunk for the model input limit."""
    if not file_field:
        return None
    file_field.open("rb")
    try:
        loaded = ImageOps.exif_transpose(Image.open(file_field)).convert("RGB")
    finally:
        file_field.close()
    loaded.thumbnail(
        (_MAX_REFERENCE_EDGE, _MAX_REFERENCE_EDGE), Image.Resampling.LANCZOS
    )
    return loaded


def _white_border_ratio(image: Image.Image) -> float:
    """Share of near-white pixels along the frame edges."""
    small = image.convert("RGB")
    small.thumbnail((256, 256))
    width, height = small.size
    band = max(2, round(min(width, height) * 0.03))
    pixels = small.load()
    total = white = 0
    for y in range(height):
        for x in range(width):
            if band <= x < width - band and band <= y < height - band:
                continue
            total += 1
            if min(pixels[x, y]) >= 235:
                white += 1
    return white / total if total else 0.0


def validate_regenerated_image(data: bytes, *, mode: str) -> None:
    """Reject broken, tiny or (for white) non-white-background results."""
    try:
        with Image.open(BytesIO(data)) as probe:
            probe.verify()
        with Image.open(BytesIO(data)) as image:
            image.load()
            if min(image.size) < _MIN_OUTPUT_EDGE:
                raise GeminiSetImageServiceError("Gemini returned a too small image.")
            if mode == "white" and _white_border_ratio(image) < 0.9:
                raise GeminiSetImageServiceError(
                    "Gemini did not return a pure white background."
                )
    except GeminiSetImageServiceError:
        raise
    except Exception as error:
        raise GeminiSetImageServiceError(
            "Gemini returned an unreadable image."
        ) from error


def regenerate_listing_image(
    *,
    product,
    source_image,
    mode: str,
    current_image,
    white_cover=None,
    is_set: bool = False,
) -> bytes:
    """Generate one replacement marketplace photo for a single slot.

    ``current_image`` is the stored file being replaced (used for the aspect
    ratio only). ``white_cover`` is the stored JV cover, used as the cleanest
    product reference for interior and human photos.
    """
    if mode not in REGENERATABLE_MODES:
        raise GeminiSetImageServiceError(f"Unsupported listing image mode: {mode}.")
    if not settings.GOOGLE_API_KEY:
        raise ImproperlyConfigured(
            "Image regeneration is not configured. Set GOOGLE_API_KEY."
        )
    try:
        from google import genai
        from google.genai import types
    except ImportError as error:
        raise ImproperlyConfigured(
            "The google-genai package is required for image regeneration."
        ) from error

    aspect_ratio = "4:3"
    current = _open_upload(current_image)
    if current is not None:
        aspect_ratio = _closest_aspect_ratio(*current.size)

    # The old white cover would only be copied back, so it is never a
    # reference for regenerating the white slot itself.
    white = _open_upload(white_cover) if mode != "white" else None

    parts: list = []
    if is_set:
        references = _reference_images(product)
        parts.append(
            types.Part.from_text(
                text=build_set_image_prompt(
                    product=product, mode=mode, reference_count=len(references)
                )
                + "\nCreate a fresh composition; do not repeat an earlier result."
            )
        )
        if white is not None:
            parts.append(
                types.Part.from_text(
                    text=(
                        "Approved white-background packshot of the complete set. "
                        "It is the most accurate reference for every piece:"
                    )
                )
            )
            parts.append(
                types.Part.from_bytes(data=_jpeg_bytes(white), mime_type="image/jpeg")
            )
        parts.append(
            types.Part.from_text(
                text=(
                    "Combined reference sheet. Each cell is a different seller "
                    "photo. Use every unique piece from this sheet."
                )
            )
        )
        parts.append(
            types.Part.from_bytes(
                data=_jpeg_bytes(_reference_sheet(references)), mime_type="image/jpeg"
            )
        )
    else:
        cover = _open_upload(source_image.image)
        if cover is None:
            raise GeminiSetImageServiceError("The product has no source photos.")
        extras = [
            loaded
            for other in product.images.exclude(pk=source_image.pk).order_by(
                "position", "id"
            )[:_MAX_EXTRA_SELLER_PHOTOS]
            if (loaded := _open_upload(other.image)) is not None
        ]
        references = [cover, *extras]
        parts.append(
            types.Part.from_text(
                text=build_single_regen_prompt(
                    product=product, mode=mode, has_white_reference=white is not None
                )
            )
        )
        if white is not None:
            parts.append(
                types.Part.from_text(text="Approved white-background packshot:")
            )
            parts.append(
                types.Part.from_bytes(data=_jpeg_bytes(white), mime_type="image/jpeg")
            )

    for index, reference in enumerate(references, start=1):
        parts.append(
            types.Part.from_text(text=f"Seller photo {index} of {len(references)}:")
        )
        parts.append(
            types.Part.from_bytes(data=_jpeg_bytes(reference), mime_type="image/jpeg")
        )

    last_error: GeminiSetImageServiceError | None = None
    with genai.Client(api_key=settings.GOOGLE_API_KEY) as client:
        for _attempt in range(_REGEN_ATTEMPTS):
            try:
                response = client.models.generate_content(
                    model=settings.GEMINI_IMAGE_MODEL,
                    contents=parts,
                    config=types.GenerateContentConfig(
                        response_modalities=["IMAGE"],
                        image_config=types.ImageConfig(aspect_ratio=aspect_ratio),
                    ),
                )
                data = _image_bytes_from_response(response)
                validate_regenerated_image(data, mode=mode)
                return data
            except GeminiSetImageServiceError as error:
                last_error = error
            except Exception as error:
                last_error = GeminiSetImageServiceError("Gemini image request failed.")
                last_error.__cause__ = error

    raise last_error or GeminiSetImageServiceError("Gemini returned no image.")


def generate_xl_cover(*, white_cover: bytes, title: str, is_set: bool = False) -> bytes:
    """Create a clearly distinct white-background packshot for the XL account."""
    if not settings.GOOGLE_API_KEY:
        raise ImproperlyConfigured(
            "XL cover generation is not configured. Set GOOGLE_API_KEY."
        )

    try:
        from google import genai
        from google.genai import types
    except ImportError as error:
        raise ImproperlyConfigured(
            "The google-genai package is required for XL cover generation."
        ) from error

    try:
        with Image.open(BytesIO(white_cover)) as reference:
            reference = ImageOps.exif_transpose(reference).convert("RGB")
            reference.thumbnail(
                (_MAX_REFERENCE_EDGE, _MAX_REFERENCE_EDGE),
                Image.Resampling.LANCZOS,
            )
            white_cover = _jpeg_bytes(reference)

        with genai.Client(api_key=settings.GOOGLE_API_KEY) as client:
            response = client.models.generate_content(
                model=settings.GEMINI_IMAGE_MODEL,
                contents=[
                    types.Part.from_text(
                        text=(
                            "Create one photorealistic e-commerce catalog packshot of "
                            f"the exact same furniture product: {title}. Use the attached "
                            "image as the only product reference. Keep its design, color, "
                            "materials, proportions, and construction unchanged. The input is "
                            "view A. Create a genuinely different view B: move the camera "
                            "horizontally at least 50 degrees around the product to the other "
                            "front corner, opposite the side emphasized in view A, and raise "
                            "the camera so it looks down about 15 degrees. The opposite side "
                            "of the chair and more of the seat surface must be clearly visible; "
                            "the silhouette and visible sides must differ from view A. Use a "
                            "different camera composition while keeping the whole product "
                            "centered and uncropped. Do not produce a near-identical view: a "
                            "change in lighting, brightness, shadows, or background alone is "
                            "not a valid result. Rotate the camera around the same real "
                            "product; do not mirror, redesign, or alter its details. The "
                            "background must be perfectly pure white, RGB (255, 255, 255), "
                            "uniform across the entire background with no gray, off-white, "
                            "gradient, texture, room, or interior. No cast or contact shadows "
                            "on the background; keep natural shading on the product itself "
                            "so its shape and details remain clear. No props, "
                            "text, logos, watermark, extra objects, or people."
                            + (
                                " This product is a furniture set. Preserve every item "
                                "shown in the reference, with the same item count and "
                                "relative sizes. Show the entire set together, fully "
                                "visible and uncropped, from the new camera angle. "
                                "Do not remove, duplicate, merge or add pieces."
                                if is_set
                                else ""
                            )
                        )
                    ),
                    types.Part.from_bytes(data=white_cover, mime_type="image/jpeg"),
                ],
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    image_config=types.ImageConfig(aspect_ratio="4:3"),
                ),
            )
    except Exception as error:
        raise GeminiSetImageServiceError("XL cover generation failed.") from error

    return _image_bytes_from_response(response)
