import os
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from django.core.exceptions import SuspiciousFileOperation
from django.test import SimpleTestCase, override_settings

from apps.ebay_bulk_end.staging import (
    delete_report_path,
    delete_snapshot_staging_files,
    relative_report_path,
    report_path,
    resolve_report_path,
    resolve_report_target,
)


class ReportStagingTests(SimpleTestCase):
    def test_report_paths_are_private_and_confined_to_the_module_directory(self):
        with TemporaryDirectory() as directory, override_settings(PRODUCT_IMAGE_STAGING_ROOT=directory):
            snapshot_id, owner_token = uuid4(), uuid4()
            target = report_path(snapshot_id, 1, owner_token)
            target.write_text("private report", encoding="utf-8")

            relative = relative_report_path(target)
            self.assertEqual(resolve_report_target(relative), target)
            self.assertEqual(resolve_report_path(relative), target)
            self.assertTrue(delete_report_path(relative))
            self.assertFalse(target.exists())

            if os.name != "nt":
                self.assertEqual(target.parent.stat().st_mode & 0o777, 0o700)

            with self.assertRaises(SuspiciousFileOperation):
                resolve_report_target("../outside.csv")

    def test_snapshot_cleanup_deletes_files_in_bounded_batches(self):
        with TemporaryDirectory() as directory, override_settings(PRODUCT_IMAGE_STAGING_ROOT=directory):
            snapshot_id = uuid4()
            first = report_path(snapshot_id, 1, uuid4())
            second = report_path(snapshot_id, 2, uuid4())
            third = report_path(snapshot_id, 3, uuid4())
            for path in (first, second, third):
                path.write_bytes(b"csv")

            self.assertTrue(delete_snapshot_staging_files(snapshot_id, limit=2))
            self.assertFalse(first.exists())
            self.assertFalse(second.exists())
            self.assertTrue(third.exists())
            self.assertFalse(delete_snapshot_staging_files(snapshot_id, limit=2))
            self.assertFalse(third.exists())
            self.assertFalse(Path(directory, "ebay-bulk-end", str(snapshot_id)).exists())
