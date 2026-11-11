from datetime import timedelta
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from apps.ebay_bulk_end.auth import ALLOWED_ACCOUNTS, connection_for, connection_status
from apps.ebay_bulk_end.errors import BulkEndError, ManagedListingError
from apps.ebay_bulk_end.managed_publications import managed_depotum_item_ids
from apps.ebay_bulk_end.workflows import _send_batch, confirm_job
from apps.marketplace.ebay.views import EbayAuthorizeSerializer


class DepotumAuthorizationTests(SimpleTestCase):
    def test_existing_oauth_serializer_accepts_all_three_accounts(self):
        self.assertEqual(ALLOWED_ACCOUNTS, ("jv", "xl", "depotum"))
        for account in ALLOWED_ACCOUNTS:
            with self.subTest(account=account):
                serializer = EbayAuthorizeSerializer(data={"account": account})
                self.assertTrue(serializer.is_valid(), serializer.errors)

    @override_settings(EBAY_ENVIRONMENT="production")
    @patch("apps.ebay_bulk_end.auth.EbayConnection.objects.filter")
    def test_depotum_connection_uses_the_current_environment(self, filter_connections):
        connection = Mock()
        filter_connections.return_value.first.return_value = connection

        self.assertIs(connection_for("depotum"), connection)
        filter_connections.assert_called_once_with(
            account="depotum", environment="production"
        )

    @override_settings(EBAY_ENVIRONMENT="sandbox")
    @patch("apps.ebay_bulk_end.auth.EbayConnection.objects.filter")
    def test_status_lists_all_accounts_for_the_current_environment(
        self, filter_connections
    ):
        now = timezone.now()
        filter_connections.return_value.values.return_value = [
            {
                "account": "depotum",
                "access_token_expires_at": now + timedelta(hours=1),
                "refresh_token_expires_at": now + timedelta(days=1),
                "updated_at": now,
            }
        ]

        status = connection_status()

        self.assertEqual(status["environment"], "sandbox")
        self.assertEqual(
            [row["account"] for row in status["accounts"]],
            ["jv", "xl", "depotum"],
        )
        self.assertFalse(status["accounts"][0]["connected"])
        self.assertFalse(status["accounts"][1]["connected"])
        self.assertTrue(status["accounts"][2]["connected"])
        filter_connections.assert_called_once_with(
            account__in=ALLOWED_ACCOUNTS, environment="sandbox"
        )


class ManagedPublicationTests(SimpleTestCase):
    item_id = "123456789012"
    historical_item_id = "234567890123"

    @patch(
        "apps.ebay_bulk_end.managed_publications.MarketplacePublication.objects.filter"
    )
    def test_lookup_scopes_to_depotum_ebay_and_current_or_ambiguous_environment(
        self, filter_publications
    ):
        filter_publications.return_value.values_list.return_value = [
            self.item_id,
            "unrequested-id",
        ]
        with patch(
            "apps.ebay_bulk_end.managed_publications.EbayOfferPreparation.objects.filter"
        ) as filter_preparations:
            filter_preparations.return_value.values_list.return_value.iterator.return_value = iter(
                ()
            )

            result = managed_depotum_item_ids([self.item_id], "production")

        self.assertEqual(result, {self.item_id})
        filter_publications.assert_called_once_with(
            marketplace="ebay",
            account="depotum",
            environment__in=("production", ""),
            external_id__in={self.item_id},
        )
        filter_publications.return_value.values_list.assert_called_once_with(
            "external_id", flat=True
        )
        filter_preparations.assert_called_once_with(
            account="depotum", environment__in=("production", "")
        )
        filter_preparations.return_value.values_list.assert_called_once_with(
            "listing_id", "listing_id_history"
        )

    @patch(
        "apps.ebay_bulk_end.managed_publications.MarketplacePublication.objects.filter"
    )
    def test_depotum_current_and_historical_listing_ids_are_protected(
        self, filter_publications
    ):
        filter_publications.return_value.values_list.return_value = []
        with patch(
            "apps.ebay_bulk_end.managed_publications.EbayOfferPreparation.objects.filter"
        ) as filter_preparations:
            filter_preparations.return_value.values_list.return_value.iterator.return_value = iter(
                [(self.item_id, [self.historical_item_id, "999999999999"])]
            )

            result = managed_depotum_item_ids(
                [self.item_id, self.historical_item_id, "999999999999"], "production"
            )

        self.assertEqual(
            result, {self.item_id, self.historical_item_id, "999999999999"}
        )
        filter_preparations.assert_called_once_with(
            account="depotum", environment__in=("production", "")
        )

    @patch(
        "apps.ebay_bulk_end.managed_publications.MarketplacePublication.objects.filter"
    )
    def test_preparation_lookup_errors_propagate_to_fail_closed_callers(
        self, filter_publications
    ):
        filter_publications.return_value.values_list.return_value = []
        with patch(
            "apps.ebay_bulk_end.managed_publications.EbayOfferPreparation.objects.filter",
            side_effect=RuntimeError("database unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "database unavailable"):
                managed_depotum_item_ids([self.item_id], "production")

    @patch(
        "apps.ebay_bulk_end.managed_publications.MarketplacePublication.objects.filter"
    )
    def test_invalid_environment_fails_closed_without_querying(
        self, filter_publications
    ):
        with self.assertRaises(ValueError):
            managed_depotum_item_ids([self.item_id], "other")
        filter_publications.assert_not_called()

    @patch(
        "apps.ebay_bulk_end.workflows.managed_depotum_item_ids",
        return_value={"123456789012"},
    )
    @patch("apps.ebay_bulk_end.workflows.save_job")
    @patch("apps.ebay_bulk_end.tasks.end_ebay_job.apply_async")
    def test_confirmation_rejects_a_benim_publication_before_queueing(
        self, queue_job, save_job, check_managed
    ):
        job = {
            "id": "depotum-preview",
            "account": "depotum",
            "environment": "sandbox",
            "matches": [
                {
                    "item_id": self.item_id,
                    "title": "Managed listing",
                    "matches": [{"ean": "4006381333931", "type": "listing"}],
                }
            ],
        }

        with self.assertRaises(ManagedListingError):
            confirm_job(job, [self.item_id], False, False, True)

        self.assertEqual(job["account"], "depotum")
        save_job.assert_not_called()
        queue_job.assert_not_called()
        check_managed.assert_called_once_with([self.item_id], "sandbox")

    @patch(
        "apps.ebay_bulk_end.workflows.managed_depotum_item_ids",
        return_value={"123456789012"},
    )
    @patch("apps.ebay_bulk_end.workflows.save_job")
    @patch("apps.ebay_bulk_end.workflows.end_items")
    def test_last_check_blocks_managed_item_before_enditems(
        self, end_items, save_job, check_managed
    ):
        result = {"item_id": self.item_id, "status": "queued", "message": ""}
        job = {"account": "depotum", "environment": "sandbox"}

        _send_batch("token", [{"item_id": self.item_id}], [result], job)

        end_items.assert_not_called()
        self.assertEqual(result["status"], "blocked")
        self.assertIn("BENIM DEPOM", result["message"])
        check_managed.assert_called_once_with([self.item_id], "sandbox")

    @patch(
        "apps.ebay_bulk_end.workflows.managed_depotum_item_ids",
        side_effect=RuntimeError("database unavailable"),
    )
    @patch("apps.ebay_bulk_end.workflows.save_job")
    @patch("apps.ebay_bulk_end.workflows.end_items")
    def test_last_check_failure_blocks_the_batch_before_enditems(
        self, end_items, save_job, check_managed
    ):
        result = {"item_id": self.item_id, "status": "queued", "message": ""}
        job = {"account": "depotum", "environment": "sandbox"}

        _send_batch("token", [{"item_id": self.item_id}], [result], job)

        end_items.assert_not_called()
        self.assertEqual(result["status"], "blocked")
        self.assertIn("Не удалось проверить", result["message"])

    @patch(
        "apps.ebay_bulk_end.workflows.managed_depotum_item_ids",
        side_effect=RuntimeError("database unavailable"),
    )
    def test_confirmation_lookup_failure_fails_closed(self, check_managed):
        job = {
            "id": "depotum-preview",
            "account": "depotum",
            "environment": "sandbox",
            "matches": [
                {
                    "item_id": self.item_id,
                    "title": "External listing",
                    "matches": [{"ean": "4006381333931", "type": "listing"}],
                }
            ],
        }

        with self.assertRaises(BulkEndError):
            confirm_job(job, [self.item_id], False, False, True)

        self.assertEqual(job.get("status"), None)
        check_managed.assert_called_once_with([self.item_id], "sandbox")
