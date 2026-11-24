import sys
from io import BytesIO
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest
from django.test import override_settings
from PIL import Image

from apps.common.gemini_set_image_service import (
    GeminiSetImageServiceError,
    _closest_aspect_ratio,
    build_single_regen_prompt,
    regenerate_listing_image,
    validate_regenerated_image,
)
from apps.notifications.tasks import (
    recover_stale_product_image_processing,
    regenerate_product_generated_image,
)
from apps.products.models import Product, ProductGeneratedImage, ProductImage

Regen = ProductGeneratedImage.RegenStatus


def png_bytes(size=(800, 600), color="white", center=None) -> bytes:
    image = Image.new("RGB", size, color=color)
    if center:
        width, height = size
        image.paste(center, (width // 4, height // 4, width * 3 // 4, height * 3 // 4))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def authenticate(client, user):
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def cover_with_slots(seller, product_factory, product_image_factory, image_file):
    product = product_factory(owner=seller, status=Product.Status.APPROVED)
    cover = product_image_factory(product=product, is_primary=True)
    cover.processing_status = ProductImage.ProcessingStatus.SUCCEEDED
    cover.save(update_fields=["processing_status"])
    slots = {}
    for mode in ("white", "interior", "human"):
        generated = ProductGeneratedImage.objects.create(source_image=cover, mode=mode)
        generated.image.save(f"{mode}.png", image_file(f"{mode}.png"), save=True)
        slots[mode] = generated
    return product, cover, slots


def regen_url(product, cover, generated):
    return (
        f"/api/v1/products/{product.id}/images/{cover.id}"
        f"/generated/{generated.id}/regenerate/"
    )


# --- API ----------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.django_db
def test_manager_starts_single_slot_regeneration(
    api_client, seller, manager, cover_with_slots, monkeypatch
):
    product, cover, slots = cover_with_slots
    delay = Mock()
    monkeypatch.setattr(
        "apps.notifications.tasks.regenerate_product_generated_image.delay", delay
    )

    authenticate(api_client, seller)
    assert api_client.post(regen_url(product, cover, slots["human"])).status_code == 403

    authenticate(api_client, manager)
    response = api_client.post(regen_url(product, cover, slots["human"]))
    assert response.status_code == 202
    assert response.json()["regen_status"] == "pending"
    slots["human"].refresh_from_db()
    assert slots["human"].regen_status == Regen.PENDING
    assert slots["human"].regen_claimed_at is not None

    # The same slot cannot be started twice; another slot can.
    assert api_client.post(regen_url(product, cover, slots["human"])).status_code == 400
    assert (
        api_client.post(regen_url(product, cover, slots["interior"])).status_code == 202
    )


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_regeneration_enqueues_task_after_commit(
    api_client, manager, cover_with_slots, monkeypatch
):
    product, cover, slots = cover_with_slots
    delay = Mock()
    monkeypatch.setattr(
        "apps.notifications.tasks.regenerate_product_generated_image.delay", delay
    )
    authenticate(api_client, manager)
    assert api_client.post(regen_url(product, cover, slots["white"])).status_code == 202
    delay.assert_called_once_with(slots["white"].id)


@pytest.mark.integration
@pytest.mark.django_db
def test_regeneration_is_blocked_by_batch_and_rejects_other_modes(
    api_client, manager, cover_with_slots, image_file, monkeypatch
):
    product, cover, slots = cover_with_slots
    monkeypatch.setattr(
        "apps.notifications.tasks.regenerate_product_generated_image.delay", Mock()
    )
    monkeypatch.setattr("apps.notifications.tasks.process_product_image.delay", Mock())
    authenticate(api_client, manager)

    xl = ProductGeneratedImage.objects.create(source_image=cover, mode="xl_cover")
    xl.image.save("xl.png", image_file("xl.png"), save=True)
    assert api_client.post(regen_url(product, cover, xl)).status_code == 400

    cover.processing_status = ProductImage.ProcessingStatus.PROCESSING
    cover.save(update_fields=["processing_status"])
    assert api_client.post(regen_url(product, cover, slots["white"])).status_code == 400

    # And the other way round: the 3-photo batch waits for a running slot.
    cover.processing_status = ProductImage.ProcessingStatus.SUCCEEDED
    cover.save(update_fields=["processing_status"])
    assert api_client.post(regen_url(product, cover, slots["white"])).status_code == 202
    response = api_client.post(
        f"/api/v1/products/{product.id}/images/{cover.id}/process/"
    )
    assert response.status_code == 400

    # XL cover is built from the JV cover, so it waits too.
    assert (
        api_client.post(
            f"/api/v1/products/{product.id}/images/{cover.id}/xl-cover/"
        ).status_code
        == 400
    )


@pytest.mark.integration
@pytest.mark.django_db
def test_regeneration_requires_approved_product(api_client, manager, cover_with_slots):
    product, cover, slots = cover_with_slots
    Product.objects.filter(pk=product.pk).update(status=Product.Status.SUBMITTED)
    authenticate(api_client, manager)
    assert api_client.post(regen_url(product, cover, slots["white"])).status_code == 400


# --- Task ---------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.django_db
def test_task_replaces_only_the_selected_slot(
    cover_with_slots, monkeypatch, django_capture_on_commit_callbacks
):
    _product, cover, slots = cover_with_slots
    white = slots["white"]
    old_name = white.image.name
    storage = white.image.storage
    untouched = {mode: slots[mode].image.name for mode in ("interior", "human")}
    ProductGeneratedImage.objects.filter(pk=white.pk).update(regen_status=Regen.PENDING)
    service = Mock(return_value=png_bytes())
    monkeypatch.setattr(
        "apps.common.gemini_set_image_service.regenerate_listing_image", service
    )

    with django_capture_on_commit_callbacks(execute=True):
        result = regenerate_product_generated_image.run(white.id)

    assert result == {"status": "completed", "generated_id": white.id}
    white.refresh_from_db()
    cover.refresh_from_db()
    assert white.regen_status == Regen.SUCCEEDED
    assert white.image.name != old_name
    assert not storage.exists(old_name)
    assert cover.processed_image.name == white.image.name
    assert service.call_args.kwargs["mode"] == "white"
    assert service.call_args.kwargs["white_cover"] is None
    for mode, name in untouched.items():
        slots[mode].refresh_from_db()
        assert slots[mode].image.name == name


@pytest.mark.integration
@pytest.mark.django_db
def test_task_uses_white_cover_as_reference_for_interior(cover_with_slots, monkeypatch):
    _product, _cover, slots = cover_with_slots
    ProductGeneratedImage.objects.filter(pk=slots["interior"].pk).update(
        regen_status=Regen.PENDING
    )
    service = Mock(return_value=png_bytes())
    monkeypatch.setattr(
        "apps.common.gemini_set_image_service.regenerate_listing_image", service
    )

    regenerate_product_generated_image.run(slots["interior"].id)

    assert service.call_args.kwargs["white_cover"].name == slots["white"].image.name
    assert service.call_args.kwargs["is_set"] is False


@pytest.mark.integration
@pytest.mark.django_db
def test_task_failure_keeps_the_old_photo(cover_with_slots, monkeypatch):
    _product, _cover, slots = cover_with_slots
    human = slots["human"]
    old_name = human.image.name
    ProductGeneratedImage.objects.filter(pk=human.pk).update(regen_status=Regen.PENDING)
    monkeypatch.setattr(
        "apps.common.gemini_set_image_service.regenerate_listing_image",
        Mock(side_effect=GeminiSetImageServiceError("boom")),
    )

    assert regenerate_product_generated_image.run(human.id) == {"status": "failed"}

    human.refresh_from_db()
    assert human.regen_status == Regen.FAILED
    assert human.regen_error == "Photo regeneration failed. Try again."
    assert human.image.name == old_name
    assert human.image.storage.exists(old_name)


@pytest.mark.integration
@pytest.mark.django_db
def test_task_skips_when_not_pending(cover_with_slots):
    _product, _cover, slots = cover_with_slots
    assert regenerate_product_generated_image.run(slots["white"].id) == {
        "status": "skipped",
        "reason": "not_pending",
    }


@pytest.mark.integration
@pytest.mark.django_db
def test_recovery_fails_abandoned_slot_regeneration(cover_with_slots):
    from datetime import timedelta

    from django.utils import timezone

    _product, _cover, slots = cover_with_slots
    old = timezone.now() - timedelta(hours=1)
    ProductGeneratedImage.objects.filter(pk=slots["white"].pk).update(
        regen_status=Regen.PROCESSING, regen_claimed_at=old
    )
    ProductGeneratedImage.objects.filter(pk=slots["human"].pk).update(
        regen_status=Regen.PROCESSING, regen_claimed_at=timezone.now()
    )

    result = recover_stale_product_image_processing.run()

    assert result["regen_failed"] == 1
    slots["white"].refresh_from_db()
    slots["human"].refresh_from_db()
    assert slots["white"].regen_status == Regen.FAILED
    assert slots["human"].regen_status == Regen.PROCESSING


# --- Gemini service -----------------------------------------------------------


def test_output_validation():
    validate_regenerated_image(png_bytes(center=(120, 80, 40)), mode="white")
    validate_regenerated_image(png_bytes(color=(90, 90, 90)), mode="interior")
    with pytest.raises(GeminiSetImageServiceError):
        validate_regenerated_image(png_bytes(color=(200, 200, 200)), mode="white")
    with pytest.raises(GeminiSetImageServiceError):
        validate_regenerated_image(png_bytes(size=(300, 200)), mode="human")
    with pytest.raises(GeminiSetImageServiceError):
        validate_regenerated_image(b"not an image", mode="human")


def test_aspect_ratio_follows_the_replaced_photo():
    assert _closest_aspect_ratio(1024, 768) == "4:3"
    assert _closest_aspect_ratio(1000, 1000) == "1:1"
    assert _closest_aspect_ratio(768, 1024) == "3:4"


@pytest.mark.integration
@pytest.mark.django_db
def test_single_prompt_mentions_white_reference_only_when_given(cover_with_slots):
    product, _cover, _slots = cover_with_slots
    with_white = build_single_regen_prompt(
        product=product, mode="interior", has_white_reference=True
    )
    without = build_single_regen_prompt(
        product=product, mode="white", has_white_reference=False
    )
    assert product.title in with_white
    assert "approved white-background packshot" in with_white
    assert "approved white-background packshot" not in without
    assert "RGB (255, 255, 255)" in without


def install_fake_genai(monkeypatch, outputs: list[bytes]) -> list[dict]:
    """Replace the google-genai SDK with a fake that returns ``outputs``."""
    calls: list[dict] = []

    def generate_content(**kwargs):
        calls.append(kwargs)
        data = outputs[min(len(calls), len(outputs)) - 1]
        return SimpleNamespace(
            parts=[SimpleNamespace(inline_data=SimpleNamespace(data=data))]
        )

    class FakeClient:
        def __init__(self, **_kwargs):
            self.models = SimpleNamespace(generate_content=generate_content)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    fake_types = ModuleType("google.genai.types")
    fake_types.Part = SimpleNamespace(
        from_text=lambda text: SimpleNamespace(text=text),
        from_bytes=lambda data, mime_type: SimpleNamespace(data=data),
    )
    fake_types.GenerateContentConfig = lambda **kwargs: kwargs
    fake_types.ImageConfig = lambda **kwargs: kwargs
    fake_genai = ModuleType("google.genai")
    fake_genai.Client = FakeClient
    fake_genai.types = fake_types

    google_pkg = sys.modules.get("google") or ModuleType("google")
    monkeypatch.setitem(sys.modules, "google", google_pkg)
    monkeypatch.setattr(google_pkg, "genai", fake_genai, raising=False)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", fake_types)
    return calls


@pytest.mark.integration
@pytest.mark.django_db
@override_settings(GOOGLE_API_KEY="test-key")
def test_service_retries_once_when_white_background_is_wrong(
    cover_with_slots, monkeypatch
):
    product, cover, slots = cover_with_slots
    outputs = [png_bytes(color=(180, 180, 180)), png_bytes(center=(120, 80, 40))]
    calls = install_fake_genai(monkeypatch, outputs)

    result = regenerate_listing_image(
        product=product,
        source_image=cover,
        mode="white",
        current_image=slots["white"].image,
        white_cover=slots["white"].image,
    )

    assert result == outputs[1]
    assert len(calls) == 2
    # The old white cover is never sent back as a reference for itself.
    texts = [getattr(part, "text", None) or "" for part in calls[0]["contents"]]
    assert not any("Approved white-background packshot" in text for text in texts)


@pytest.mark.integration
@pytest.mark.django_db
@override_settings(GOOGLE_API_KEY="test-key")
def test_service_sends_white_cover_for_interior_and_gives_up_after_two_bad_results(
    cover_with_slots, monkeypatch
):
    product, cover, slots = cover_with_slots
    calls = install_fake_genai(monkeypatch, [png_bytes((100, 100))])

    with pytest.raises(GeminiSetImageServiceError):
        regenerate_listing_image(
            product=product,
            source_image=cover,
            mode="interior",
            current_image=slots["interior"].image,
            white_cover=slots["white"].image,
        )

    assert len(calls) == 2
    texts = [getattr(part, "text", None) or "" for part in calls[0]["contents"]]
    assert any("Approved white-background packshot" in text for text in texts)
