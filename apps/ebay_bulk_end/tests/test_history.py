from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.ebay_bulk_end.audit import persist_job, reserve_end_batch
from apps.ebay_bulk_end.errors import BulkEndError
from apps.ebay_bulk_end.models import (
    EbayBulkEndEvent,
    EbayBulkEndInFlight,
    EbayBulkEndJob,
    EbayBulkEndJobItem,
)
from apps.ebay_bulk_end.storage import (
    cancellation_requested,
    load_job,
    request_cancellation,
)


class DurableAuditTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(
            username="Manager",
            email="manager@example.test",
            phone="+15550000001",
            password="test-password",
            role=User.Role.MANAGER,
        )

    def test_job_and_item_are_reconstructed_from_postgres_without_redis(self):
        job_id = str(uuid4())
        job = {
            "id": job_id,
            "manager_id": self.manager.pk,
            "account": "jv",
            "environment": "sandbox",
            "seller_id": "seller-jv",
            "kind": "end",
            "status": "complete",
            "created_at": timezone.now().isoformat(),
            "eans": ["01234565"],
            "matches": [
                {
                    "item_id": "123456789012",
                    "title": "Test product",
                    "sku": "01234565",
                    "match_type": "exact_sku",
                    "matches": [
                        {"ean": "01234565", "source": "sku", "type": "listing"}
                    ],
                }
            ],
            "selected": [
                {
                    "item_id": "123456789012",
                    "title": "Test product",
                    "sku": "01234565",
                    "matches": [{"ean": "01234565"}],
                }
            ],
            "results": [
                {
                    "item_id": "123456789012",
                    "status": "ended",
                    "eans": ["01234565"],
                    "error_code": "",
                    "end_time": "2026-11-11T12:00:00+00:00",
                    "finished_at": "2026-11-11T12:00:01+00:00",
                }
            ],
            "progress": {"current": 1, "total": 1},
        }

        persist_job(job)
        with patch(
            "apps.ebay_bulk_end.storage.redis_client",
            side_effect=BulkEndError("Redis unavailable"),
        ):
            restored = load_job(job_id)

        self.assertEqual(restored["status"], "complete")
        self.assertEqual(restored["results"][0]["status"], "ended")
        self.assertEqual(restored["selected"][0]["item_id"], "123456789012")
        item = EbayBulkEndJobItem.objects.get(job_id=job_id)
        self.assertEqual(item.matched_eans, ["01234565"])
        self.assertEqual(item.remote_end_time.isoformat(), "2026-11-11T12:00:00+00:00")
        self.assertTrue(
            EbayBulkEndEvent.objects.filter(
                job_id=job_id, event_type="job_completed"
            ).exists()
        )

    def test_pre_request_reservation_is_unique_across_jobs(self):
        now = timezone.now().isoformat()
        first_id, second_id = str(uuid4()), str(uuid4())
        item = {
            "item_id": "123456789012",
            "title": "Test product",
            "matches": [{"ean": "01234565"}],
        }

        def job(job_id):
            return {
                "id": job_id,
                "manager_id": self.manager.pk,
                "account": "jv",
                "environment": "production",
                "seller_id": "seller-jv",
                "kind": "end",
                "status": "ending",
                "created_at": now,
                "eans": ["01234565"],
                "matches": [item],
                "selected": [item],
                "results": [
                    {
                        "item_id": item["item_id"],
                        "status": "sending",
                        "attempt_count": 1,
                    }
                ],
            }

        first, second = job(first_id), job(second_id)

        self.assertTrue(reserve_end_batch(first, [item["item_id"]], uuid4()))
        self.assertFalse(reserve_end_batch(second, [item["item_id"]], uuid4()))
        self.assertEqual(EbayBulkEndInFlight.objects.count(), 1)

    def test_cancellation_flag_survives_redis_loss(self):
        job_id = uuid4()
        EbayBulkEndJob.objects.create(
            id=job_id,
            created_by=self.manager,
            account="jv",
            environment="sandbox",
            status="ending",
        )

        with patch(
            "apps.ebay_bulk_end.storage.redis_client",
            side_effect=BulkEndError("Redis unavailable"),
        ):
            request_cancellation(job_id)
            self.assertTrue(cancellation_requested(job_id))


class HistoryAPITests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(
            username="Manager",
            email="manager-history@example.test",
            phone="+15550000002",
            password="test-password",
            role=User.Role.MANAGER,
        )
        self.other = User.objects.create_user(
            username="Other manager",
            email="other-history@example.test",
            phone="+15550000003",
            password="test-password",
            role=User.Role.MANAGER,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.manager)
        self.job = EbayBulkEndJob.objects.create(
            created_by=self.manager,
            account="jv",
            environment="sandbox",
            ebay_seller_id="seller-jv",
            kind="end",
            status="partial",
            discovery_strategy="sell_feed_active_inventory_sku_index",
            discovery_complete=True,
            input_eans=["01234565", "4006381333931"],
            unique_ean_count=2,
            matched_item_count=1,
            selected_item_count=1,
            ended_count=0,
            failed_count=1,
            uncertain_count=0,
            not_found_count=1,
            finished_at=timezone.now(),
        )
        self.item = EbayBulkEndJobItem.objects.create(
            job=self.job,
            item_id="123456789012",
            sku="01234565",
            matched_eans=["01234565"],
            match_type="exact_sku",
            details={"match": {"title": "=formula"}},
            selected=True,
            status="failed",
            ebay_error_code="21916765",
            ebay_error_message="Test failure",
            finished_at=timezone.now(),
        )
        EbayBulkEndEvent.objects.create(
            job=self.job,
            item=self.item,
            actor_user=self.manager,
            event_type="item_failed",
            metadata={"error_code": "21916765"},
        )

    def test_history_is_owner_scoped_paginated_and_filterable(self):
        EbayBulkEndJob.objects.create(
            created_by=self.manager,
            account="xl",
            environment="production",
            status="complete",
            input_eans=["9780201379624"],
            unique_ean_count=1,
        )
        EbayBulkEndJob.objects.create(
            created_by=self.other,
            account="xl",
            environment="production",
            status="complete",
            input_eans=["9780201379624"],
            unique_ean_count=1,
        )

        response = self.client.get(
            "/api/v2/ebay-bulk-end/history/?account=jv&page_size=1&result=failure"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["job_id"], str(self.job.pk))
        self.assertEqual(response.data["results"][0]["failed_count"], 1)

        page = self.client.get("/api/v2/ebay-bulk-end/history/?page_size=1")
        ean_query = self.client.get("/api/v2/ebay-bulk-end/history/?q=01234565")
        self.assertEqual(page.data["count"], 2)
        self.assertEqual(page.data["pages"], 2)
        self.assertEqual(ean_query.data["count"], 1)

    def test_items_events_and_csv_are_paginated_and_owner_only(self):
        other_job = EbayBulkEndJob.objects.create(
            created_by=self.other,
            account="xl",
            environment="sandbox",
            status="complete",
        )

        items = self.client.get(f"/api/v2/ebay-bulk-end/history/{self.job.pk}/items/")
        events = self.client.get(f"/api/v2/ebay-bulk-end/history/{self.job.pk}/events/")
        csv_response = self.client.get(
            f"/api/v2/ebay-bulk-end/history/{self.job.pk}/csv/"
        )
        hidden = self.client.get(f"/api/v2/ebay-bulk-end/history/{other_job.pk}/")

        self.assertEqual(items.data["results"][0]["match_type"], "exact_sku")
        self.assertEqual(events.data["results"][0]["event_type"], "item_failed")
        self.assertIn("not_found_in_sku_index", csv_response.content.decode())
        self.assertIn("'=formula", csv_response.content.decode())
        self.assertEqual(hidden.status_code, 404)

    def test_manager_filter_cannot_expose_another_managers_jobs(self):
        response = self.client.get(
            "/api/v2/ebay-bulk-end/history/?manager=" + str(self.other.pk)
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 0)
