import pytest
from django.test import override_settings

from apps.common.external_json import compact_external_json
from apps.orchestrator.job_services import (
    create_marketplace_job,
    payload_contains_truncation_markers,
)
from apps.orchestrator.models import MarketplaceJob
from apps.orchestrator.tasks import execute_marketplace_job, path_with_ean


@pytest.mark.unit
def test_path_with_ean_substitutes_placeholder():
    assert (
        path_with_ean("/api/products/product/{ean}/", "4071489789997")
        == "/api/products/product/4071489789997/"
    )


@pytest.mark.unit
def test_detects_truncation_markers_in_nested_payload():
    assert payload_contains_truncation_markers(
        {
            "otto:jv": [
                {
                    "productDescription": {
                        "attributes": [
                            {
                                "name": "Lampentyp",
                                "values": [
                                    {
                                        "_truncated": True,
                                        "_reason": "maximum nesting depth reached",
                                    }
                                ],
                            }
                        ]
                    }
                }
            ]
        }
    )
    assert not payload_contains_truncation_markers(
        {"otto:jv": [{"productDescription": {"attributes": [{"values": ["LED"]}]}}]}
    )


@pytest.mark.unit
@pytest.mark.django_db
@override_settings(EXTERNAL_JSON_MAX_DEPTH=8)
def test_create_marketplace_job_keeps_deep_otto_attribute_values(
    product_factory, manager
):
    product = product_factory(owner=manager, ean_jv="4012345678901")
    deep_body = [
        {
            "sku": product.ean_jv,
            "productDescription": {
                "category": "Sofa",
                "productLine": "Weisses Sofa",
                "attributes": [
                    {
                        "name": "Lampentyp",
                        "values": ["LED"],
                    }
                ],
            },
        }
    ]

    # Prove the old compact wrapper would corrupt attribute strings.
    compacted = compact_external_json(
        {
            "payloads": {},
            "target_payloads": {"otto:jv": deep_body},
            "accounts": {},
            "targets": [{"marketplace": "otto", "account": "jv"}],
        }
    )
    assert compacted["target_payloads"]["otto:jv"][0]["productDescription"][
        "attributes"
    ][0]["values"][0] == {
        "_truncated": True,
        "_reason": "maximum nesting depth reached",
    }

    job = create_marketplace_job(
        product=product,
        requested_by=manager,
        operation=MarketplaceJob.Operation.PUBLISH,
        targets=[{"marketplace": "otto", "account": "jv"}],
        target_payloads={"otto:jv": deep_body},
    )

    stored_value = job.request_payload["target_payloads"]["otto:jv"][0][
        "productDescription"
    ]["attributes"][0]["values"][0]
    assert stored_value == "LED"


@pytest.mark.unit
@pytest.mark.django_db
def test_execute_rejects_truncated_marketplace_payload(product_factory, manager):
    product = product_factory(owner=manager, ean_jv="4012345678901")
    job = MarketplaceJob.objects.create(
        product=product,
        requested_by=manager,
        operation=MarketplaceJob.Operation.PUBLISH,
        requested_channels=["otto"],
        request_payload={
            "targets": [{"marketplace": "otto", "account": "jv"}],
            "target_payloads": {
                "otto:jv": [
                    {
                        "productDescription": {
                            "attributes": [
                                {
                                    "name": "Lampentyp",
                                    "values": [
                                        {
                                            "_truncated": True,
                                            "_reason": "maximum nesting depth reached",
                                        }
                                    ],
                                }
                            ]
                        }
                    }
                ]
            },
        },
    )

    execute_marketplace_job.run(str(job.id))
    job.refresh_from_db()
    assert job.status == MarketplaceJob.Status.FAILED
    assert job.error["code"] == "marketplace_payload_truncated"
