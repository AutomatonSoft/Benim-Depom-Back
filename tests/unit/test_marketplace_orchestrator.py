from unittest.mock import patch

import pytest

from apps.marketplace.hood.models import HoodProductSnapshot
from apps.marketplace.hood.services import execute as execute_hood
from apps.orchestrator.models import MarketplaceJob, MarketplacePublication
from apps.orchestrator.tasks import (
    check_otto_marketplace_status,
    extract_otto_process_id,
    get_expected_otto_marketplace_statuses,
    get_otto_process_result,
    request_for_non_hood_channel,
    get_otto_async_process_payload,
    is_otto_process_pending,
)


@pytest.mark.django_db
def test_marketplace_job_is_queued_for_a_manager(
    api_client, manager, product_factory, django_capture_on_commit_callbacks
):
    product = product_factory(owner=manager)
    api_client.force_authenticate(manager)

    with patch("apps.orchestrator.views.execute_marketplace_job.delay") as delay, django_capture_on_commit_callbacks(execute=True):
        response = api_client.post(
            f"/api/v1/orchestrator/products/{product.id}/search/",
            {
                "channels": ["kaufland"],
                "accounts": {"kaufland": "jv"},
            },
            format="json",
        )

    assert response.status_code == 202
    job = MarketplaceJob.objects.get(pk=response.data["id"])
    assert job.status == MarketplaceJob.Status.QUEUED
    assert job.requested_channels == ["kaufland"]
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
    assert job.results[0]["details"]["code"] == "marketplace_dispatch_failed"
    assert "does not have an EAN" in job.results[0]["details"]["reason"]


class CapturingClient:
    def __init__(self):
        self.calls = []

    def request(self, base_url, method, path, **kwargs):
        self.calls.append((base_url, method, path, kwargs))
        return {"ok": True, "status_code": 200, "details": {"ok": True}}


def test_extracts_otto_process_id_from_pending_response():
    response = {
        "links": [
            {
                "rel": "self",
                "href": "/v5/products/update-tasks/05654b90-50c2-4a90-a37b-3f54c40b1892",
            }
        ],
        "state": "pending",
    }

    assert extract_otto_process_id(response) == "05654b90-50c2-4a90-a37b-3f54c40b1892"

def test_otto_deactivation_nested_pending_response_is_supported():
    response_payload = {
        "success": True,
        "ean": "4071489789768",
        "active": False,
        "response": {
            "state": "pending",
            "pingAfter": "2026-08-17T09:12:41.476127Z",
            "links": [
                {
                    "rel": "self",
                    "href": (
                        "/v5/products/update-tasks/"
                        "ef844153-cc84-40a2-b563-167668e8f5ff"
                    ),
                }
            ],
        },
    }

    assert get_otto_async_process_payload(response_payload)["state"] == (
        "pending"
    )

    assert extract_otto_process_id(response_payload) == (
        "ef844153-cc84-40a2-b563-167668e8f5ff"
    )

    assert is_otto_process_pending(
        {
            "ok": True,
            "status_code": 202,
            "details": response_payload,
        }
    )

def test_otto_process_checks_final_endpoints_until_a_result_is_found():
    class ProcessClient:
        def __init__(self):
            self.paths = []

        def request(self, base_url, method, path, **kwargs):
            self.paths.append(path)
            if path.endswith("/succeeded"):
                return {"ok": True, "status_code": 200, "details": {"results": [{"variation": "/v5/products/1"}]}}
            return {"ok": False, "status_code": 404, "details": {"detail": "Not ready"}}

    client = ProcessClient()
    final_result = get_otto_process_result(
        client,
        process_id="process-123",
        account="jv",
    )

    assert final_result["outcome"] == "succeeded"
    assert client.paths == [
        "/v1/products/otto/update-tasks/process-123/failed",
        "/v1/products/otto/update-tasks/process-123/succeeded",
    ]


def test_expected_otto_marketplace_statuses_depend_on_operation():
    assert get_expected_otto_marketplace_statuses(
        MarketplaceJob.Operation.ACTIVATE
    ) == ("ONLINE",)
    assert get_expected_otto_marketplace_statuses(
        MarketplaceJob.Operation.DEACTIVATE
    ) == ("INACTIVE",)


@pytest.mark.django_db
def test_otto_marketplace_online_confirms_update(
    product_factory,
    manager,
):
    product = product_factory(owner=manager, ean_jv="4012345678901")
    job = MarketplaceJob.objects.create(
        product=product,
        requested_by=manager,
        operation=MarketplaceJob.Operation.UPDATE,
        requested_channels=["otto"],
    )
    publication = MarketplacePublication.objects.create(
        product=product,
        marketplace="otto",
        account="jv",
        ean=product.ean_jv,
        status=MarketplacePublication.Status.PUBLISHING,
        external_reference="process-123",
        last_job=job,
    )
    online_response = {
        "ok": True,
        "status_code": 200,
        "details": {
            "marketPlaceStatus": [
                {
                    "sku": product.ean_jv,
                    "moin": "M2DV7ZJST2",
                    "status": "ONLINE",
                    "links": [
                        {
                            "rel": "shop",
                            "href": "https://www.otto.de/p/?moin=M2DV7ZJST2",
                        }
                    ],
                }
            ]
        },
    }

    with patch(
        "apps.orchestrator.tasks.get_otto_marketplace_status",
        return_value=online_response,
    ):
        check_otto_marketplace_status.run(publication.pk)

    publication.refresh_from_db()
    job.refresh_from_db()
    assert publication.status == MarketplacePublication.Status.ACTIVE
    assert publication.external_id == "M2DV7ZJST2"
    assert job.status == MarketplaceJob.Status.SUCCEEDED


@pytest.mark.django_db
def test_otto_marketplace_inactive_confirms_deactivation(
    product_factory,
    manager,
):
    product = product_factory(owner=manager, ean_jv="4012345678901")
    job = MarketplaceJob.objects.create(
        product=product,
        requested_by=manager,
        operation=MarketplaceJob.Operation.DEACTIVATE,
        requested_channels=["otto"],
    )
    publication = MarketplacePublication.objects.create(
        product=product,
        marketplace="otto",
        account="jv",
        ean=product.ean_jv,
        status=MarketplacePublication.Status.DEACTIVATING,
        external_reference="process-123",
        last_job=job,
    )
    inactive_response = {
        "ok": True,
        "status_code": 200,
        "details": {
            "marketPlaceStatus": [
                {
                    "sku": product.ean_jv,
                    "status": "INACTIVE",
                }
            ]
        },
    }

    with patch(
        "apps.orchestrator.tasks.get_otto_marketplace_status",
        return_value=inactive_response,
    ):
        check_otto_marketplace_status.run(publication.pk)

    publication.refresh_from_db()
    job.refresh_from_db()
    assert publication.status == MarketplacePublication.Status.DEACTIVATED
    assert job.status == MarketplaceJob.Status.SUCCEEDED


@pytest.mark.django_db
def test_pending_otto_process_is_not_marked_published_or_consumed(
    product_factory,
    manager,
):
    product = product_factory(owner=manager, ean_jv="4012345678901")
    job = MarketplaceJob.objects.create(
        product=product,
        requested_by=manager,
        operation=MarketplaceJob.Operation.PUBLISH,
        requested_channels=["otto"],
        request_payload={
            "targets": [{"marketplace": "otto", "account": "jv"}],
            "target_payloads": {"otto:jv": [{"sku": product.ean_jv}]},
        },
    )
    pending = {
        "ok": True,
        "status_code": 200,
        "details": {
            "state": "pending",
            "links": [
                {
                    "rel": "self",
                    "href": "/v5/products/update-tasks/process-123",
                }
            ],
        },
    }

    with patch(
        "apps.orchestrator.tasks.request_for_non_hood_channel",
        return_value=pending,
    ), patch(
        "apps.orchestrator.tasks.check_otto_publication_process.apply_async"
    ) as schedule:
        from apps.orchestrator.tasks import execute_marketplace_job

        execute_marketplace_job.run(str(job.id))

    job.refresh_from_db()
    publication = product.marketplace_publications.get(
        marketplace="otto",
        account="jv",
    )
    assert job.status == MarketplaceJob.Status.PENDING_CONFIRMATION
    assert publication.status == "publishing"
    assert publication.external_reference == "process-123"
    assert job.results[0]["ean_consumed"] is False
    assert job.results[0]["awaiting_marketplace_confirmation"] is True
    schedule.assert_called_once()


@pytest.mark.parametrize(
    ("channel", "operation", "expected_method", "expected_path"),
    [
        (
            "kaufland",
            MarketplaceJob.Operation.UPDATE,
            "PATCH",
            "/api/products/ean/change/",
        ),
        ("otto", MarketplaceJob.Operation.PUBLISH, "POST", "/extermal/create_or_update_product"),
    ],
)
def test_marketplace_routes_match_direct_api_contract(channel, operation, expected_method, expected_path):
    client = CapturingClient()
    request_for_non_hood_channel(
        client,
        marketplace=channel,
        operation=operation,
        ean="4012345678901",
        account="jv",
        payload={"title": "Chair"},
    )
    _base_url, method, path, _kwargs = client.calls[0]
    assert method == expected_method
    assert path == expected_path


def test_otto_publish_keeps_a_prebuilt_variations_list():
    client = CapturingClient()
    variations = [
        {
            "productReference": "product-1",
            "sku": "4012345678901",
            "ean": "4012345678901",
        }
    ]

    request_for_non_hood_channel(
        client,
        marketplace="otto",
        operation=MarketplaceJob.Operation.PUBLISH,
        ean="4012345678901",
        account="jv",
        payload=variations,
    )

    assert client.calls[0][3]["payload"] == variations


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
