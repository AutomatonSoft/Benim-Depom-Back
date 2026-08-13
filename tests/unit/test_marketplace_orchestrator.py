from unittest.mock import patch

import pytest

from apps.marketplace.hood.models import HoodProductSnapshot
from apps.marketplace.hood.services import execute as execute_hood
from apps.orchestrator.models import MarketplaceJob
from apps.orchestrator.tasks import _request_for_channel


@pytest.mark.django_db
def test_marketplace_job_is_queued_for_a_manager(
    api_client, manager, product_factory, django_capture_on_commit_callbacks
):
    product = product_factory(owner=manager)
    api_client.force_authenticate(manager)

    with patch("apps.orchestrator.views.execute_marketplace_job.delay") as delay, django_capture_on_commit_callbacks(execute=True):
        response = api_client.post(
            f"/api/v1/orchestrator/products/{product.id}/publish/",
            {
                "channels": ["hood", "kaufland"],
                "payloads": {
                    "hood": {"title": "Chair"},
                    "kaufland": {"title": "Chair", "price": "19.99"},
                },
                "accounts": {"hood": "jv", "kaufland": "jv"},
            },
            format="json",
        )

    assert response.status_code == 202
    job = MarketplaceJob.objects.get(pk=response.data["id"])
    assert job.status == MarketplaceJob.Status.QUEUED
    assert job.requested_channels == ["hood", "kaufland"]
    delay.assert_called_once_with(str(job.id))


@pytest.mark.django_db
def test_marketplace_task_requires_an_ean(product_factory, manager):
    product = product_factory(owner=manager)
    job = MarketplaceJob.objects.create(
        product=product,
        requested_by=manager,
        operation=MarketplaceJob.Operation.SEARCH,
        requested_channels=["hood"],
    )

    from apps.orchestrator.tasks import execute_marketplace_job

    execute_marketplace_job.run(str(job.id))
    job.refresh_from_db()
    assert job.status == MarketplaceJob.Status.FAILED
    assert job.error["code"] == "product_has_no_ean"


class CapturingClient:
    def __init__(self):
        self.calls = []

    def request(self, base_url, method, path, **kwargs):
        self.calls.append((base_url, method, path, kwargs))
        return {"ok": True, "status_code": 200, "details": {"ok": True}}


@pytest.mark.parametrize(
    ("channel", "operation", "expected_method", "expected_path"),
    [
        ("kaufland", MarketplaceJob.Operation.UPDATE, "PATCH", "/api/products/ean/change/"),
        ("otto", MarketplaceJob.Operation.PUBLISH, "POST", "/extermal/create_or_update_product"),
    ],
)
def test_marketplace_routes_match_direct_api_contract(channel, operation, expected_method, expected_path):
    client = CapturingClient()
    _request_for_channel(
        client,
        channel=channel,
        operation=operation,
        ean="4012345678901",
        account="jv",
        payload={"title": "Chair"},
    )
    _base_url, method, path, _kwargs = client.calls[0]
    assert method == expected_method
    assert path == expected_path


@pytest.mark.django_db
def test_hood_search_persists_the_product_snapshot(product_factory, manager):
    product = product_factory(owner=manager, ean_jv="4012345678901")
    response = {"ok": True, "status_code": 200, "details": {"itemID": "123", "title": "Chair"}}

    with patch("apps.marketplace.hood.services.HoodClient.request", return_value=response):
        result = execute_hood(
            product=product,
            operation=MarketplaceJob.Operation.SEARCH,
            ean=product.ean_jv,
            account="jv",
            payload={},
            request_id="test-request-id",
        )

    snapshot = HoodProductSnapshot.objects.get(product=product, account="jv")
    assert result == response
    assert snapshot.ean == "4012345678901"
    assert snapshot.payload == {"itemID": "123", "title": "Chair"}
