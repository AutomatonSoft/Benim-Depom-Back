from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from apps.ebay_bulk_end.storage import ACCOUNT_LOCK_SECONDS, acquire_account_lock
from apps.ebay_bulk_end.workflows import csv_content, report_rows


class AccountLockTests(SimpleTestCase):
    @patch("apps.ebay_bulk_end.storage.redis_client")
    def test_renews_only_when_the_lua_compare_and_expire_matches_the_owner(
        self, redis_client
    ):
        client = Mock()
        client.eval.return_value = 1
        redis_client.return_value = client

        acquired = acquire_account_lock("production", "jv", "job-1")

        self.assertTrue(acquired)
        client.eval.assert_called_once_with(
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end",
            1,
            "ebay-bulk-end:v1:account-lock:production:jv",
            "job-1",
            ACCOUNT_LOCK_SECONDS,
        )
        client.set.assert_not_called()

    @patch("apps.ebay_bulk_end.storage.redis_client")
    def test_does_not_claim_an_account_lock_owned_by_another_job(self, redis_client):
        client = Mock()
        client.eval.return_value = 0
        client.set.return_value = False
        redis_client.return_value = client

        acquired = acquire_account_lock("production", "xl", "job-2")

        self.assertFalse(acquired)
        client.set.assert_called_once_with(
            "ebay-bulk-end:v1:account-lock:production:xl",
            "job-2",
            nx=True,
            ex=ACCOUNT_LOCK_SECONDS,
        )


class ReportTests(SimpleTestCase):
    def setUp(self):
        self.job = {
            "account": "jv",
            "eans": ["01234565", "4006381333931", "9780201379624"],
            "unmatched_eans": ["9780201379624"],
            "finished_at": "2026-11-10T12:00:00+00:00",
            "matches": [
                {
                    "item_id": "123456789012",
                    "title": '=HYPERLINK("https://example.invalid")',
                    "matches": [{"ean": "01234565", "type": "listing"}],
                },
                {
                    "item_id": "123456789013",
                    "title": "Found but not selected",
                    "matches": [{"ean": "4006381333931", "type": "listing"}],
                },
            ],
            "results": [
                {
                    "item_id": "123456789012",
                    "title": '=HYPERLINK("https://example.invalid")',
                    "eans": ["01234565"],
                    "status": "ended",
                    "message": "Ended",
                    "error_code": "",
                    "end_time": "2026-11-10T11:59:00Z",
                    "finished_at": "2026-11-10T12:00:00+00:00",
                }
            ],
        }

    def test_report_contains_selected_unselected_and_unmatched_inputs(self):
        rows = report_rows(self.job)

        self.assertEqual(
            [row["status"] for row in rows], ["ended", "not_selected", "not_found"]
        )
        self.assertEqual(rows[1]["eans"], ["4006381333931"])
        self.assertEqual(rows[2]["eans"], ["9780201379624"])

    def test_csv_has_account_and_original_eans_and_escapes_formula_cells(self):
        output = csv_content(self.job)

        self.assertIn("Original EANs,Account,Item ID,Title,Status", output)
        self.assertIn("01234565,JV,123456789012", output)
        self.assertIn("not_selected", output)
        self.assertIn("not_found", output)
        self.assertIn("'=HYPERLINK", output)
