import io
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4
from xml.etree import ElementTree as ET

from django.test import SimpleTestCase, TestCase, override_settings

from apps.ebay_bulk_end.errors import BulkEndError
from apps.ebay_bulk_end.feed import (
    create_inventory_task,
    download_inventory_report_to_path,
    iter_inventory_chunk,
)
from apps.ebay_bulk_end.inventory import (
    advance_inventory_snapshot_step,
    run_inventory_scan_chunk,
)
from apps.ebay_bulk_end.models import EbayInventorySnapshot
from apps.ebay_bulk_end.xml_report import iter_xml_inventory_rows

XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<BulkDataExchangeResponses xmlns="urn:ebay:apis:eBLBaseComponents"><ActiveInventoryReport>
<SKUDetails><SKU>4071489791471</SKU><ItemID>110591477674</ItemID></SKUDetails>
<SKUDetails><ItemID>110591477867</ItemID><Variations>
<Variation><SKU>4071489789959</SKU></Variation>
<Variation><SKU>0000000000000</SKU></Variation></Variations></SKUDetails>
<Ack>Success</Ack></ActiveInventoryReport></BulkDataExchangeResponses>"""


class RealReportFormatTests(SimpleTestCase):
    @patch("apps.ebay_bulk_end.feed.build_marketplace_session")
    def test_creation_accepts_both_inventory_and_generic_task_location(
        self, build_session
    ):
        response = MagicMock(status_code=202, ok=True)
        build_session.return_value.__enter__.return_value.post.return_value = response
        for path in ("task", "inventory_task"):
            response.headers = {
                "Location": f"https://api.sandbox.ebay.com/sell/feed/v1/{path}/task-20-2998750543873"
            }
            self.assertEqual(create_inventory_task("token"), "task-20-2998750543873")

    @patch("apps.ebay_bulk_end.feed.build_marketplace_session")
    def test_zipped_xml_is_normalized_and_resumes_on_csv_checkpoints(
        self, build_session
    ):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("activeinventory.xml", XML)
        response = MagicMock(status_code=200, ok=True)
        response.iter_content.return_value = [archive.getvalue()]
        build_session.return_value.__enter__.return_value.get.return_value.__enter__.return_value = response
        with TemporaryDirectory() as directory:
            target = Path(directory, "normalized.csv")
            staged = download_inventory_report_to_path("token", "task-1", target)
            self.assertEqual(staged["size"], target.stat().st_size)
            with target.open("rb") as f:
                first, cookie, eof = iter_inventory_chunk(f, "", 2)
                self.assertFalse(eof)
                f.seek(0)
                second, _, eof = iter_inventory_chunk(f, cookie, 2)
            self.assertTrue(eof)
            self.assertEqual(
                [r["sku"] for r in first + second],
                ["4071489791471", "4071489789959", "0000000000000"],
            )
            self.assertEqual(second[0]["item_id"], "110591477867")
            self.assertEqual(len(list(Path(directory).iterdir())), 1)

    def test_xml_rejects_failure_entities_truncation_and_missing_report(self):
        for xml in (
            XML.replace(b"<Ack>Success</Ack>", b"<Ack>Failure</Ack>"),
            XML[:-10],
            b"<Other/>",
            b'<!DOCTYPE report [<!ENTITY secret SYSTEM "file:///etc/passwd">]>'
            + XML.split(b"?>", 1)[1],
        ):
            with self.subTest(xml=xml[:40]), self.assertRaises(BulkEndError):
                list(iter_xml_inventory_rows(io.BytesIO(xml), "EBAY_DE"))

    def test_xml_reader_never_requests_an_unbounded_read(self):
        class BoundedStream(io.BytesIO):
            def read(self, size=-1):
                assert 0 < size <= 64 * 1024
                return super().read(size)

        self.assertEqual(
            len(list(iter_xml_inventory_rows(BoundedStream(XML), "EBAY_DE"))), 3
        )

    def test_csv_rejects_unclosed_quote_and_allows_real_sku_length(self):
        with self.assertRaises(BulkEndError):
            iter_inventory_chunk(
                io.BytesIO(b'SKU,ItemID,Channel\n"bad,123,EBAY_DE'), "", 10
            )
        rows, _, _ = iter_inventory_chunk(
            io.BytesIO(("SKU,ItemID,Channel\n" + "a" * 70 + ",123,EBAY_DE\n").encode()),
            "",
            10,
        )
        self.assertEqual(len(rows[0]["sku"]), 70)


class ReportToPreviewTests(TestCase):
    def test_lms_zip_to_postgres_to_preview_with_real_getitem_status_structure(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("activeinventory.xml", XML)
        response = MagicMock(status_code=200, ok=True)
        response.iter_content.return_value = [archive.getvalue()]
        with (
            TemporaryDirectory() as directory,
            override_settings(PRODUCT_IMAGE_STAGING_ROOT=directory),
        ):
            target = Path(directory, "ebay-bulk-end", "report.csv")
            with patch(
                "apps.ebay_bulk_end.feed.build_marketplace_session"
            ) as build_session:
                build_session.return_value.__enter__.return_value.get.return_value.__enter__.return_value = response
                staged = download_inventory_report_to_path("token", "task-1", target)
            snapshot = EbayInventorySnapshot.objects.create(
                account="jv",
                environment="sandbox",
                ebay_seller_id="qn-llr8lrkm",
                marketplace_id="EBAY_DE",
                status="importing",
                task_id="task-1",
                report_path="ebay-bulk-end/report.csv",
                report_size=staged["size"],
                report_sha256=staged["sha256"],
                report_mtime_ns=target.stat().st_mtime_ns,
            )
            with patch(
                "apps.ebay_bulk_end.inventory.access_context",
                return_value=(SimpleNamespace(ebay_user_id="qn-llr8lrkm"), "token"),
            ):
                self.assertEqual(
                    advance_inventory_snapshot_step(snapshot.pk, uuid4()), 1
                )
                self.assertIsNone(advance_inventory_snapshot_step(snapshot.pk, uuid4()))
                snapshot.refresh_from_db()
                self.assertTrue(snapshot.complete)
                self.assertEqual(snapshot.listings.count(), 3)
                sku_by_id = {
                    "110591477674": "4071489791471",
                    "110591477867": "4071489789959",
                }

                def get_item(_token, item_id):
                    return ET.fromstring(
                        f"<Item><ItemID>{item_id}</ItemID><SKU>{sku_by_id[item_id]}</SKU><Seller><UserID>seller-jv</UserID></Seller><SellingStatus><ListingStatus>Active</ListingStatus></SellingStatus><ListingType>FixedPriceItem</ListingType><Title>Test</Title></Item>"
                    )

                job = {
                    "id": str(uuid4()),
                    "kind": "scan",
                    "status": "queued",
                    "account": "jv",
                    "environment": "sandbox",
                    "eans": list(sku_by_id.values()),
                    "inventory_snapshot_id": str(snapshot.pk),
                    "discovery_details": {},
                }
                with (
                    patch(
                        "apps.ebay_bulk_end.inventory.get_item", side_effect=get_item
                    ) as get,
                    patch("apps.ebay_bulk_end.inventory.save_job"),
                    patch(
                        "apps.ebay_bulk_end.inventory.verified_seller_username",
                        return_value="seller-jv",
                    ) as identity,
                ):
                    self.assertIsNone(run_inventory_scan_chunk(job))
                self.assertEqual(get.call_count, 2)
                self.assertEqual(job["status"], "preview")
                self.assertEqual(job["seller_id"], "qn-llr8lrkm")
                self.assertEqual(job["seller_username"], "seller-jv")
                identity.assert_called_once_with("token", "qn-llr8lrkm")
                self.assertEqual(len(job["matches"]), 2)
                self.assertTrue(job["discovery_complete"])
