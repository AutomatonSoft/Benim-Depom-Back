from decimal import Decimal
from unittest.mock import patch

import pytest

from apps.marketplace.ebay.models import EbayDraft, EbayOfferPreparation
from apps.marketplace.ebay.preparation import _save_state
from apps.orchestrator.models import MarketplaceJob, MarketplacePublication
from apps.orchestrator.tasks import execute_marketplace_job


@pytest.mark.django_db
@pytest.mark.parametrize("environment", ["sandbox", "production"])
def test_ebay_publish_is_queued_without_external_calls(
    api_client,
    manager,
    product_factory,
    settings,
    django_capture_on_commit_callbacks,
    environment,
):
    settings.EBAY_ENVIRONMENT = environment
    product = product_factory(owner=manager, status="approved", ean_jv="4071489789959")
    api_client.force_authenticate(manager)
    with (
        patch("apps.orchestrator.views.execute_marketplace_job.delay") as delay,
        django_capture_on_commit_callbacks(execute=True),
    ):
        response = api_client.post(
            f"/api/v1/orchestrator/products/{product.pk}/publish/",
            {"targets": [{"marketplace": "ebay", "account": "depotum"}]},
            format="json",
        )
    assert response.status_code == 202, response.data
    job = MarketplaceJob.objects.get(pk=response.data["id"])
    assert job.status == "queued"
    assert job.request_payload["ebay_environment"] == environment
    delay.assert_called_once_with(str(job.pk))


@pytest.mark.django_db
def test_ebay_failure_does_not_skip_other_marketplaces(
    manager, product_factory, settings
):
    product = product_factory(owner=manager, ean_jv="4071489789959")
    job = MarketplaceJob.objects.create(
        product=product,
        requested_by=manager,
        operation="publish",
        requested_channels=["ebay", "hood"],
        request_payload={
            "ebay_environment": settings.EBAY_ENVIRONMENT,
            "targets": [
                {"marketplace": "ebay", "account": "depotum"},
                {"marketplace": "hood", "account": "jv"},
            ],
        },
    )
    with (
        patch("apps.orchestrator.tasks.build_target_payloads", return_value={}),
        patch(
            "apps.marketplace.ebay.orchestration.execute",
            return_value={
                "ok": False,
                "status_code": 400,
                "details": {"detail": "eBay rejected"},
            },
        ),
        patch(
            "apps.orchestrator.tasks.execute_hood",
            return_value={
                "ok": True,
                "status_code": 200,
                "details": {"item_id": "hood-1"},
            },
        ) as hood,
        patch("apps.orchestrator.tasks.consume_ean_code", return_value=False),
    ):
        execute_marketplace_job.run(str(job.pk))
    job.refresh_from_db()
    assert job.status == "partial"
    assert [row["ok"] for row in job.results] == [False, True]
    hood.assert_called_once()


@pytest.mark.django_db
def test_ebay_worker_rejects_a_job_from_another_environment(
    manager, product_factory, settings
):
    settings.EBAY_ENVIRONMENT = "sandbox"
    product = product_factory(owner=manager, ean_jv="4071489789959")
    job = MarketplaceJob.objects.create(
        product=product,
        requested_by=manager,
        operation="publish",
        requested_channels=["ebay"],
        request_payload={
            "ebay_environment": "production",
            "targets": [{"marketplace": "ebay", "account": "depotum"}],
        },
    )
    with patch("apps.marketplace.ebay.orchestration.execute") as send:
        execute_marketplace_job.run(str(job.pk))
    job.refresh_from_db()
    assert job.status == "failed"
    send.assert_not_called()


@pytest.mark.django_db
def test_successful_ebay_job_records_listing_and_uses_jv_ean(
    manager, product_factory, settings
):
    from apps.orchestrator.job_services import create_marketplace_job

    product = product_factory(owner=manager, ean_jv="4071489789959")
    job = create_marketplace_job(
        product=product,
        requested_by=manager,
        operation="publish",
        targets=[{"marketplace": "ebay", "account": "depotum"}],
    )
    with (
        patch(
            "apps.marketplace.ebay.orchestration.execute",
            return_value={
                "ok": True,
                "status_code": 200,
                "details": {"listingId": "12345"},
            },
        ),
        patch("apps.orchestrator.tasks.consume_ean_code", return_value=True) as consume,
    ):
        execute_marketplace_job.run(str(job.pk))
    job.refresh_from_db()
    publication = MarketplacePublication.objects.get(product=product)
    assert job.status == "succeeded"
    assert publication.status == "active"
    assert publication.environment == settings.EBAY_ENVIRONMENT
    assert publication.ean == product.ean_jv
    assert publication.external_id == "12345"
    consume.assert_called_once_with(product=product, account="jv")


@pytest.mark.django_db
@pytest.mark.parametrize("environment", ["sandbox", "production"])
def test_ebay_dispatch_refreshes_stale_stock_to_57(
    manager, product_factory, settings, environment
):
    from apps.marketplace.ebay.orchestration import execute
    from apps.marketplace.ebay.preview import build_draft_preview

    settings.EBAY_ENVIRONMENT = environment
    product = product_factory(owner=manager, status="approved", ean_jv="4071489789959")
    product.variants.update(quantity=57)
    draft = EbayDraft.objects.create(
        product=product,
        environment=environment,
        account="depotum",
        title="Sofa BD",
        description="Beschreibung",
        category_id="38208",
        quantity=1,
    )
    schema = {
        "aspects": [],
        "required_aspects": [],
        "category_tree_id": "77",
        "category_tree_version": "1",
    }
    captured = {}

    def prepare(product_id, *, account):
        draft.refresh_from_db()
        captured.update(build_draft_preview(draft))
        return EbayOfferPreparation.objects.create(
            draft=draft,
            account=account,
            environment=environment,
            sku=product.ean_jv,
            payload_hash="x",
            status="prepared",
            offer_id="offer-1",
            inventory_confirmed=True,
        )

    def publish(product_id, fingerprint, *, account):
        preparation = EbayOfferPreparation.objects.get(draft=draft)
        _save_state(preparation, status="published", listing_id="12345")
        return preparation

    with (
        patch("apps.marketplace.ebay.drafts._validate_listing_settings"),
        patch("apps.marketplace.ebay.drafts.get_category_aspects", return_value=schema),
        patch(
            "apps.marketplace.ebay.catalog.get_category_aspects", return_value=schema
        ),
        patch(
            "apps.marketplace.ebay.manager.public_generated_listing_urls",
            return_value=["https://example.com/a.jpg"],
        ),
        patch(
            "apps.marketplace.ebay.manager.safe_listing_price_eur",
            return_value=Decimal("100.00"),
        ),
        patch("apps.marketplace.ebay.preparation.prepare_offer", side_effect=prepare),
        patch(
            "apps.marketplace.ebay.publishing._publication_checks",
            return_value={"ready": True, "preview_hash": "x"},
        ),
        patch("apps.marketplace.ebay.publishing.publish_offer", side_effect=publish),
    ):
        result = execute(product=product, operation="publish")
    assert result["ok"], result
    assert (
        captured["inventory_item"]["body"]["availability"][
            "shipToLocationAvailability"
        ]["quantity"]
        == 57
    )
    assert captured["offer"]["body"]["availableQuantity"] == 57


@pytest.mark.django_db
def test_ebay_publications_count_with_legacy_and_keep_environments_separate(
    api_client, manager, product_factory, settings
):
    settings.EBAY_ENVIRONMENT = "sandbox"
    product = product_factory(owner=manager, ean_jv="4071489789959")
    for environment in ("sandbox", "production"):
        draft = EbayDraft.objects.create(
            product=product, environment=environment, account="depotum"
        )
        preparation = EbayOfferPreparation.objects.create(
            draft=draft,
            environment=environment,
            account="depotum",
            sku=product.ean_jv,
            payload_hash="x",
            status="prepared",
            offer_id=environment,
        )
        _save_state(
            preparation,
            status="published",
            listing_id="12345" if environment == "sandbox" else "67890",
        )
    for marketplace in ("otto", "hood", "kaufland"):
        for account in ("jv", "xl"):
            MarketplacePublication.objects.create(
                product=product,
                marketplace=marketplace,
                account=account,
                ean=product.ean_jv,
                status="active",
            )
    api_client.force_authenticate(manager)
    rows = api_client.get(
        f"/api/v1/orchestrator/products/{product.pk}/publications/"
    ).data
    assert len(rows) == 7
    ebay = next(row for row in rows if row["marketplace"] == "ebay")
    assert ebay["environment"] == "sandbox"
    assert ebay["listing_url"] == "https://www.sandbox.ebay.de/itm/12345"
    response = api_client.get("/api/v1/manager/products/")
    assert response.data["results"][0]["active_listing_count"] == 7
    dashboard = api_client.get("/api/v1/manager/dashboard/").data
    assert (
        next(
            row for row in dashboard["active_listings"] if row["marketplace"] == "ebay"
        )["count"]
        == 1
    )
    preparation = EbayOfferPreparation.objects.get(environment="sandbox")
    _save_state(preparation, status="withdrawn")
    assert (
        MarketplacePublication.objects.for_environment().filter(status="active").count()
        == 6
    )
    _save_state(preparation, status="published", listing_id="54321")
    assert (
        MarketplacePublication.objects.get(
            marketplace="ebay", environment="sandbox"
        ).external_id
        == "54321"
    )
    assert (
        MarketplacePublication.objects.get(
            marketplace="ebay", environment="production"
        ).external_id
        == "67890"
    )


@pytest.mark.django_db
def test_existing_ebay_listings_are_imported_without_republishing(
    manager, product_factory
):
    from importlib import import_module
    from types import SimpleNamespace

    from django.apps import apps

    product = product_factory(owner=manager, ean_jv="4071489789959")
    for environment, status in (("sandbox", "published"), ("production", "withdrawn")):
        draft = EbayDraft.objects.create(
            product=product, environment=environment, account="depotum"
        )
        EbayOfferPreparation.objects.create(
            draft=draft,
            environment=environment,
            account="depotum",
            sku=product.ean_jv,
            payload_hash="x",
            status=status,
            offer_id=environment,
            listing_id="12345",
        )
    migrate = import_module(
        "apps.orchestrator.migrations.0010_ebay_publications"
    ).import_ebay_publications
    editor = SimpleNamespace(connection=SimpleNamespace(alias="default"))
    migrate(apps, editor)
    migrate(apps, editor)
    assert MarketplacePublication.objects.count() == 2
    assert MarketplacePublication.objects.get(environment="sandbox").status == "active"
    assert (
        MarketplacePublication.objects.get(environment="production").status
        == "deactivated"
    )


@pytest.mark.django_db
@pytest.mark.parametrize("active", [True, False])
def test_ebay_status_check_reconciles_remote_state(
    api_client,
    manager,
    product_factory,
    settings,
    active,
    django_capture_on_commit_callbacks,
):
    settings.EBAY_ENVIRONMENT = "sandbox"
    product = product_factory(owner=manager, ean_jv="4071489789959")
    draft = EbayDraft.objects.create(
        product=product, account="depotum", environment="sandbox"
    )
    preparation = EbayOfferPreparation.objects.create(
        draft=draft,
        account="depotum",
        environment="sandbox",
        sku=product.ean_jv,
        payload_hash="x",
        status="uncertain",
        offer_id="offer-1",
        listing_id="12345",
    )
    publication = MarketplacePublication.objects.create(
        product=product,
        marketplace="ebay",
        account="depotum",
        environment="sandbox",
        ean=product.ean_jv,
        status="failed",
    )
    api_client.force_authenticate(manager)
    with (
        patch("apps.orchestrator.views.execute_marketplace_job.delay"),
        django_capture_on_commit_callbacks(execute=True),
    ):
        response = api_client.post(
            f"/api/v1/orchestrator/publications/{publication.pk}/reconcile/",
            {"action": "check"},
            format="json",
        )
    assert response.status_code == 202, response.data
    snapshot = {
        "remote_status": "PUBLISHED" if active else "UNPUBLISHED",
        "listing_status": "ACTIVE" if active else "ENDED",
        "remote_listing_id": "54321" if active else "",
        "preparation": preparation,
    }
    with (
        patch(
            "apps.marketplace.ebay.listing.get_listing_snapshot", return_value=snapshot
        ),
        patch(
            "apps.orchestrator.publication_reconciliation.consume_ean_code",
            return_value=False,
        ),
    ):
        execute_marketplace_job.run(str(response.data["id"]))
    publication.refresh_from_db()
    preparation.refresh_from_db()
    assert MarketplaceJob.objects.get(pk=response.data["id"]).status == "succeeded"
    assert publication.status == ("active" if active else "deactivated")
    assert preparation.status == ("published" if active else "withdrawn")
    if active:
        assert publication.external_id == preparation.listing_id == "54321"
