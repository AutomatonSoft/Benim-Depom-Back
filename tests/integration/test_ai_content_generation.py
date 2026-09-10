from unittest.mock import patch

import pytest

from apps.common.openai_text_service import (
    OpenAITextResult,
    OpenAITextServiceError,
)
from apps.orchestrator.models import (
    MarketplaceContentGeneration,
    MarketplaceListingConfiguration,
)
from apps.orchestrator.tasks import generate_marketplace_content
from apps.products.models import Product

SUCCESSFUL_AI_CONTENT = {
    "title": "Holzstuhl mit Stoffbezug",
    "description": (
        "Ein stabiler Holzstuhl mit blauem Stoffbezug.\n\n"
        "Die Maße betragen 55 × 50 × 90 cm."
    ),
    "bullet_points": [
        "Holzgestell",
        "Stoffbezug",
        "Farbe: Blau",
    ],
    "materials": [],
    "color": "Blau",
}


@pytest.mark.integration
@pytest.mark.django_db
def test_manager_creates_ai_generation_and_queues_task(
    api_client,
    manager,
    seller,
    product_factory,
    django_capture_on_commit_callbacks,
):
    product = product_factory(
        owner=seller,
        status=Product.Status.SUBMITTED,
    )
    api_client.force_authenticate(manager)

    with (
        patch("apps.orchestrator.views.generate_marketplace_content.delay") as delay,
        django_capture_on_commit_callbacks(execute=True),
    ):
        response = api_client.post(
            f"/api/v1/orchestrator/products/{product.id}/ai-content/generate/",
            {
                "targets": [
                    {"marketplace": "otto", "account": "jv"},
                    {"marketplace": "hood", "account": "jv"},
                ]
            },
            format="json",
        )

    assert response.status_code == 202

    generation = MarketplaceContentGeneration.objects.get(pk=response.data["id"])
    assert generation.status == MarketplaceContentGeneration.Status.QUEUED
    assert generation.requested_by == manager
    assert generation.input_snapshot["product_id"] == product.id
    assert generation.input_snapshot["seller_title"] == product.title
    assert generation.targets == [
        {"marketplace": "otto", "account": "jv"},
        {"marketplace": "hood", "account": "jv"},
    ]
    delay.assert_called_once_with(str(generation.id))


@pytest.mark.integration
@pytest.mark.django_db
def test_seller_cannot_start_ai_generation(
    api_client,
    seller,
    product_factory,
):
    product = product_factory(
        owner=seller,
        status=Product.Status.SUBMITTED,
    )
    api_client.force_authenticate(seller)

    response = api_client.post(
        f"/api/v1/orchestrator/products/{product.id}/ai-content/generate/",
        {
            "targets": [
                {"marketplace": "otto", "account": "jv"},
            ]
        },
        format="json",
    )

    assert response.status_code == 403
    assert MarketplaceContentGeneration.objects.count() == 0


@pytest.mark.integration
@pytest.mark.django_db
def test_ai_task_saves_successful_universal_draft(
    manager,
    product_factory,
):
    product = product_factory(
        owner=manager,
        status=Product.Status.APPROVED,
    )
    generation = MarketplaceContentGeneration.objects.create(
        product=product,
        requested_by=manager,
        targets=[{"marketplace": "otto", "account": "jv"}],
        input_snapshot={
            "product_id": product.id,
            "seller_title": product.title,
            "variants": [],
        },
    )

    with patch("apps.orchestrator.tasks.OpenAITextService") as service_class:
        service_class.return_value.generate_json.return_value = OpenAITextResult(
            model="fake-openai-model",
            data=SUCCESSFUL_AI_CONTENT,
        )

        result = generate_marketplace_content.run(str(generation.id))

    generation.refresh_from_db()

    assert result["status"] == MarketplaceContentGeneration.Status.SUCCEEDED
    assert generation.status == MarketplaceContentGeneration.Status.SUCCEEDED
    assert generation.model == "fake-openai-model"
    assert generation.error == {}
    assert generation.result["universal"]["content"] == SUCCESSFUL_AI_CONTENT


@pytest.mark.integration
@pytest.mark.django_db
def test_ai_task_marks_generation_failed_without_real_openai_call(
    manager,
    product_factory,
):
    product = product_factory(
        owner=manager,
        status=Product.Status.APPROVED,
    )
    generation = MarketplaceContentGeneration.objects.create(
        product=product,
        requested_by=manager,
        targets=[{"marketplace": "otto", "account": "jv"}],
        input_snapshot={"product_id": product.id},
    )

    with patch("apps.orchestrator.tasks.OpenAITextService") as service_class:
        service_class.return_value.generate_json.side_effect = OpenAITextServiceError(
            "provider unavailable"
        )

        generate_marketplace_content.run(str(generation.id))

    generation.refresh_from_db()

    assert generation.status == MarketplaceContentGeneration.Status.FAILED
    assert generation.error["code"] == "generation_failed"
    assert "provider unavailable" in generation.error["detail"]


@pytest.mark.integration
@pytest.mark.django_db
def test_manager_applies_draft_without_overwriting_manual_text(
    api_client,
    manager,
    product_factory,
):
    product = product_factory(
        owner=manager,
        status=Product.Status.APPROVED,
    )
    generation = MarketplaceContentGeneration.objects.create(
        product=product,
        requested_by=manager,
        status=MarketplaceContentGeneration.Status.SUCCEEDED,
        targets=[{"marketplace": "otto", "account": "jv"}],
        result={
            "universal": {
                "model": "fake-openai-model",
                "content": SUCCESSFUL_AI_CONTENT,
            }
        },
    )
    configuration = MarketplaceListingConfiguration.objects.create(
        product=product,
        marketplace="otto",
        account="jv",
        configuration={
            "description": "Текст, который менеджер написал вручную.",
        },
    )

    api_client.force_authenticate(manager)

    response = api_client.post(
        (
            f"/api/v1/orchestrator/products/{product.id}/ai-content/"
            f"generations/{generation.id}/apply/"
        ),
        {
            "targets": [
                {"marketplace": "otto", "account": "jv"},
            ],
            "overwrite": False,
        },
        format="json",
    )

    assert response.status_code == 200

    configuration.refresh_from_db()

    assert (
        configuration.configuration["product_line"]
        == f"{SUCCESSFUL_AI_CONTENT['title']} (BD)"
    )
    assert (
        configuration.configuration["bullet_points"]
        == (SUCCESSFUL_AI_CONTENT["bullet_points"])
    )
    assert configuration.configuration["color"] == SUCCESSFUL_AI_CONTENT["color"]
    assert configuration.configuration["description"] == (
        "Текст, который менеджер написал вручную."
    )
    assert response.data["skipped_fields"]["otto:jv"] == ["description"]


@pytest.mark.integration
@pytest.mark.django_db
def test_cannot_apply_a_generation_that_is_not_finished(
    api_client,
    manager,
    product_factory,
):
    product = product_factory(
        owner=manager,
        status=Product.Status.APPROVED,
    )
    generation = MarketplaceContentGeneration.objects.create(
        product=product,
        requested_by=manager,
        status=MarketplaceContentGeneration.Status.QUEUED,
        targets=[{"marketplace": "hood", "account": "jv"}],
    )

    api_client.force_authenticate(manager)

    response = api_client.post(
        (
            f"/api/v1/orchestrator/products/{product.id}/ai-content/"
            f"generations/{generation.id}/apply/"
        ),
        {
            "targets": [
                {"marketplace": "hood", "account": "jv"},
            ],
            "overwrite": True,
        },
        format="json",
    )

    assert response.status_code == 409
    assert MarketplaceListingConfiguration.objects.count() == 0
