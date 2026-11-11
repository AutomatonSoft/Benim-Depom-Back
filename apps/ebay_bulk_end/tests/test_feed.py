import gzip
import io
import zipfile
from unittest.mock import MagicMock, Mock, patch

from django.test import SimpleTestCase
from requests.exceptions import ReadTimeout

from apps.ebay_bulk_end.errors import (
    BulkEndError,
    EBayRateLimitError,
    EBayTransportError,
)
from apps.ebay_bulk_end.feed import (
    MARKETPLACE_ID,
    create_inventory_task,
    download_inventory_report,
    get_inventory_task,
    get_inventory_tasks,
    iter_inventory_chunk,
    iter_inventory_rows,
)


class InventoryReportParserTests(SimpleTestCase):
    def test_preserves_leading_zeroes_and_duplicate_skus(self):
        report = io.BytesIO(
            b"SKU,ItemID,Channel\r\n001234565,10001,EBAY_DE\r\n"
            b"001234565,10002,EBAY_DE\r\n"
        )

        self.assertEqual(
            list(iter_inventory_rows(report)),
            [
                {"sku": "001234565", "item_id": "10001", "marketplace_id": "EBAY_DE"},
                {"sku": "001234565", "item_id": "10002", "marketplace_id": "EBAY_DE"},
            ],
        )

    def test_reads_csv_inside_a_zip(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zip_file:
            zip_file.writestr(
                "active-inventory.csv", "SKU,ItemID,Channel\n001234565,10001,EBAY_DE\n"
            )
        archive.seek(0)
        self.assertEqual(len(list(iter_inventory_rows(archive))), 1)

    def test_reads_gzip_compressed_csv(self):
        report = io.BytesIO(
            gzip.compress(b"SKU,ItemID,Channel\n001234565,10001,EBAY_DE\n")
        )

        rows = list(iter_inventory_rows(report))

        self.assertEqual(rows[0]["sku"], "001234565")
        self.assertEqual(rows[0]["item_id"], "10001")

    def test_chunk_parser_resumes_on_csv_record_boundaries(self):
        report = io.BytesIO(
            b"SKU,ItemID,Channel,Title\n001234565,10001,EBAY_DE,first\n"
            b'001234566,10002,EBAY_DE,"second\nline"\n001234567,10003,EBAY_DE,last\n'
        )
        first, cookie, eof = iter_inventory_chunk(report, "", 2)
        report.seek(0)
        second, next_cookie, done = iter_inventory_chunk(report, cookie, 2)

        self.assertEqual([row["item_id"] for row in first], ["10001", "10002"])
        self.assertFalse(eof)
        self.assertEqual([row["item_id"] for row in second], ["10003"])
        self.assertTrue(done)
        self.assertNotEqual(cookie, next_cookie)

    def test_rejects_missing_columns_invalid_ids_and_missing_marketplace(self):
        for value in (
            b"SKU,ItemID\n001234565,10001\n",
            b"SKU,ItemID,Channel\n001234565,bad,EBAY_DE\n",
            b"SKU,ItemID,Channel\n001234565,10001,\n",
            ("SKU,ItemID,Channel\n" + "S" * 51 + ",10001,EBAY_DE\n").encode(),
            b"SKU,ItemID,Channel\n001234565,10001,EBAY_DE,extra\n",
        ):
            with self.subTest(value=value), self.assertRaises(BulkEndError):
                list(iter_inventory_rows(io.BytesIO(value)))


class InventoryReportRequestTests(SimpleTestCase):
    @patch("apps.ebay_bulk_end.feed.build_marketplace_session")
    def test_creation_errors_preserve_safe_http_diagnostics(self, build_session):
        response = Mock(status_code=500, ok=False)
        response.headers = {
            "X-EBAY-C-REQUEST-ID": "request-123",
            "Authorization": "Bearer secret-token",
        }
        response.json.return_value = {
            "errors": [{"errorId": 160001, "message": "secret-token"}],
            "access_token": "secret-token",
        }
        build_session.return_value.__enter__.return_value.post.return_value = response

        with self.assertRaises(EBayTransportError) as caught:
            create_inventory_task("secret-token")

        self.assertEqual(
            caught.exception.diagnostics,
            {
                "operation": "create_inventory_task",
                "reason": "http_error",
                "http_status": 500,
                "x-ebay-c-request-id": "request-123",
                "ebay_error_ids": [160001],
            },
        )
        self.assertNotIn(
            "secret-token", str(caught.exception) + str(caught.exception.diagnostics)
        )

    @patch("apps.ebay_bulk_end.feed.build_marketplace_session")
    def test_creation_timeout_does_not_expose_request_details(self, build_session):
        build_session.return_value.__enter__.return_value.post.side_effect = (
            ReadTimeout("Bearer secret-token")
        )
        with self.assertRaises(EBayTransportError) as caught:
            create_inventory_task("secret-token")
        self.assertEqual(
            caught.exception.diagnostics,
            {
                "operation": "create_inventory_task",
                "reason": "network_error",
                "exception_type": "ReadTimeout",
            },
        )
        self.assertNotIn(
            "secret-token", str(caught.exception) + str(caught.exception.diagnostics)
        )

    @patch("apps.ebay_bulk_end.feed.build_marketplace_session")
    def test_missing_task_id_is_distinct_from_http_or_network_failure(
        self, build_session
    ):
        response = Mock(status_code=202, ok=True, headers={})
        build_session.return_value.__enter__.return_value.post.return_value = response
        with self.assertRaises(EBayTransportError) as caught:
            create_inventory_task("token")
        self.assertEqual(caught.exception.diagnostics["reason"], "missing_task_id")
        self.assertEqual(caught.exception.diagnostics["http_status"], 202)

    @patch(
        "apps.ebay_bulk_end.feed.api_base_url",
        return_value="https://api.sandbox.ebay.com",
    )
    @patch("apps.ebay_bulk_end.feed.build_marketplace_session")
    def test_creates_an_unfiltered_active_inventory_report(
        self, build_session, base_url
    ):
        response = Mock(status_code=202, ok=True)
        response.headers = {
            "Location": "https://api.sandbox.ebay.com/sell/feed/v1/inventory_task/123-task"
        }
        post = build_session.return_value.__enter__.return_value.post
        post.return_value = response

        task_id = create_inventory_task("seller-token")

        self.assertEqual(task_id, "123-task")
        args = post.call_args
        self.assertEqual(
            args.args[0], "https://api.sandbox.ebay.com/sell/feed/v1/inventory_task"
        )
        self.assertEqual(
            args.kwargs["json"],
            {"schemaVersion": "1.0", "feedType": "LMS_ACTIVE_INVENTORY_REPORT"},
        )
        self.assertEqual(
            args.kwargs["headers"]["X-EBAY-C-MARKETPLACE-ID"], MARKETPLACE_ID
        )
        self.assertEqual(args.kwargs["headers"]["Authorization"], "Bearer seller-token")
        self.assertNotIn("filterCriteria", args.kwargs["json"])
        base_url.assert_called_once()

    @patch(
        "apps.ebay_bulk_end.feed.api_base_url",
        return_value="https://api.sandbox.ebay.com",
    )
    @patch("apps.ebay_bulk_end.feed.build_marketplace_session")
    def test_polls_pending_and_completed_tasks(self, build_session, _base_url):
        session = build_session.return_value.__enter__.return_value
        response = Mock(status_code=200, ok=True)
        response.json.side_effect = [
            {
                "taskId": "task-1",
                "feedType": "LMS_ACTIVE_INVENTORY_REPORT",
                "status": "IN_PROCESS",
            },
            {
                "taskId": "task-1",
                "feedType": "LMS_ACTIVE_INVENTORY_REPORT",
                "status": "COMPLETED",
            },
        ]
        session.get.return_value = response

        self.assertEqual(get_inventory_task("token", "task-1")["status"], "IN_PROCESS")
        self.assertEqual(get_inventory_task("token", "task-1")["status"], "COMPLETED")
        self.assertEqual(session.get.call_count, 2)

    @patch(
        "apps.ebay_bulk_end.feed.api_base_url",
        return_value="https://api.sandbox.ebay.com",
    )
    @patch("apps.ebay_bulk_end.feed.build_marketplace_session")
    def test_lists_tasks_for_uncertain_report_request_recovery(
        self, build_session, _base_url
    ):
        session = build_session.return_value.__enter__.return_value
        response = Mock(status_code=200, ok=True)
        response.json.return_value = {"tasks": [{"taskId": "task-1"}], "total": 1}
        session.get.return_value = response

        tasks, total = get_inventory_tasks(
            "token", "2026-11-11T00:00:00.000Z..2026-11-11T00:01:00.000Z"
        )

        self.assertEqual(tasks, [{"taskId": "task-1"}])
        self.assertEqual(total, 1)
        self.assertEqual(
            session.get.call_args.args[0],
            "https://api.sandbox.ebay.com/sell/feed/v1/inventory_task",
        )
        self.assertEqual(
            session.get.call_args.kwargs["params"],
            {
                "feed_type": "LMS_ACTIVE_INVENTORY_REPORT",
                "date_range": "2026-11-11T00:00:00.000Z..2026-11-11T00:01:00.000Z",
                "limit": 500,
                "offset": 0,
            },
        )

    @patch(
        "apps.ebay_bulk_end.feed.api_base_url",
        return_value="https://api.sandbox.ebay.com",
    )
    @patch("apps.ebay_bulk_end.feed.build_marketplace_session")
    def test_rejects_wrong_task_identity_missing_permission_and_rate_limit(
        self, build_session, _base_url
    ):
        session = build_session.return_value.__enter__.return_value
        response = Mock(status_code=200, ok=True)
        response.json.return_value = {
            "taskId": "another-task",
            "feedType": "LMS_ACTIVE_INVENTORY_REPORT",
            "status": "COMPLETED",
        }
        session.get.return_value = response
        with self.assertRaises(EBayTransportError):
            get_inventory_task("token", "task-1")

        response.status_code = 403
        response.ok = False
        with self.assertRaisesRegex(BulkEndError, "sell.inventory"):
            get_inventory_task("token", "task-1")

        response.status_code = 429
        response.headers = {"Retry-After": "7"}
        with self.assertRaises(EBayRateLimitError) as error:
            get_inventory_task("token", "task-1")
        self.assertEqual(error.exception.retry_after, 7)

    @patch(
        "apps.ebay_bulk_end.feed.api_base_url",
        return_value="https://api.sandbox.ebay.com",
    )
    @patch("apps.ebay_bulk_end.feed.build_marketplace_session")
    def test_streams_downloaded_csv_to_the_parser(self, build_session, _base_url):
        session = build_session.return_value.__enter__.return_value
        response = MagicMock(status_code=200, ok=True)
        response.headers = {"Content-Length": "25"}
        response.iter_content.return_value = [
            b"SKU,ItemID,Channel\n001234565,10001,EBAY_DE\n"
        ]
        session.get.return_value.__enter__.return_value = response

        with download_inventory_report("token", "task-1") as report:
            rows = list(iter_inventory_rows(report))

        self.assertEqual(rows[0]["sku"], "001234565")
        self.assertEqual(session.get.call_args.kwargs["stream"], True)

    @patch("apps.ebay_bulk_end.feed.tempfile.SpooledTemporaryFile")
    @patch(
        "apps.ebay_bulk_end.feed.api_base_url",
        return_value="https://api.sandbox.ebay.com",
    )
    @patch("apps.ebay_bulk_end.feed.build_marketplace_session")
    def test_fails_closed_when_temporary_disk_cannot_stage_the_report(
        self, build_session, _base_url, spool_file
    ):
        session = build_session.return_value.__enter__.return_value
        response = MagicMock(status_code=200, ok=True)
        response.headers = {}
        response.iter_content.return_value = [b"report chunk"]
        session.get.return_value.__enter__.return_value = response
        spool_file.return_value.write.side_effect = OSError("disk full")

        with self.assertRaises(EBayTransportError):
            with download_inventory_report("token", "task-1"):
                self.fail("incomplete reports must not be yielded")

        spool_file.return_value.close.assert_called_once()
