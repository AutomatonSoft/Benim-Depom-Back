from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
from xml.etree import ElementTree as ET

from django.test import TestCase
from django.utils import timezone

from apps.ebay_bulk_end.domain import item_summary
from apps.ebay_bulk_end.errors import BulkEndError, EBayTransportError
from apps.ebay_bulk_end.identifiers import index_snapshot_identifiers
from apps.ebay_bulk_end.inventory import run_inventory_scan_chunk
from apps.ebay_bulk_end.models import (
    EbayInventoryListing,
    EbayInventorySnapshot,
    EbayListingIdentifiers,
)
from apps.ebay_bulk_end.workflows import _verify_item

EAN = "4070863275101"


def item(item_id, *, mpn=False, seller="xl-seller"):
    identifier = (
        f"<ItemSpecifics><NameValueList><Name>Herstellernummer</Name><Value>XLM{EAN}</Value></NameValueList></ItemSpecifics>"
        if mpn
        else f"<ProductListingDetails><EAN>{EAN}</EAN></ProductListingDetails>"
    )
    return ET.fromstring(
        f"<Item><ItemID>{item_id}</ItemID><SKU>173930174</SKU><Title>Kommode</Title>"
        f"<Seller><UserID>{seller}</UserID></Seller><SellingStatus><ListingStatus>Active</ListingStatus>"
        f"</SellingStatus><ListingType>FixedPriceItem</ListingType>{identifier}</Item>"
    )


class IdentifierIndexTests(TestCase):
    def setUp(self):
        self.snapshot = EbayInventorySnapshot.objects.create(
            account="xl",
            environment="production",
            ebay_seller_id="immutable-xl",
            marketplace_id="EBAY_DE",
            status="complete",
            complete=True,
            expires_at=timezone.now() + timedelta(minutes=58),
        )
        for number, sku in (("306818711951", "173930174"), ("306818711952", "")):
            EbayInventoryListing.objects.create(
                snapshot=self.snapshot,
                item_id=number,
                sku=sku,
                marketplace_id="EBAY_DE",
            )
        self.job = {
            "id": str(uuid4()),
            "kind": "scan",
            "status": "queued",
            "account": "xl",
            "environment": "production",
            "seller_id": "immutable-xl",
            "eans": [EAN],
            "inventory_snapshot_id": str(self.snapshot.pk),
            "discovery_details": {},
        }

    def test_ean_and_manufacturer_number_discovery_with_unrelated_or_missing_sku(self):
        def get_item(_token, number):
            return item(number, mpn=number.endswith("2"))

        with (
            patch(
                "apps.ebay_bulk_end.inventory.access_context",
                return_value=(SimpleNamespace(ebay_user_id="immutable-xl"), "token"),
            ),
            patch(
                "apps.ebay_bulk_end.inventory.verified_seller_username",
                return_value="xl-seller",
            ),
            patch("apps.ebay_bulk_end.inventory.save_job"),
            patch("apps.ebay_bulk_end.identifiers.save_job"),
            patch(
                "apps.ebay_bulk_end.identifiers.get_item", side_effect=get_item
            ) as index_read,
            patch("apps.ebay_bulk_end.inventory.get_item", side_effect=get_item),
        ):
            while run_inventory_scan_chunk(self.job) is not None:
                pass
        self.assertEqual(index_read.call_count, 2)
        self.assertEqual(self.job["status"], "preview")
        self.assertEqual(
            {row["match_type"] for row in self.job["matches"]},
            {"exact_ean", "exact_mpn"},
        )
        self.assertEqual(self.job["unmatched_eans"], [])
        with patch("apps.ebay_bulk_end.identifiers.get_item") as read:
            self.assertTrue(
                index_snapshot_identifiers(
                    self.snapshot, self.job, "token", "xl-seller"
                )
            )
            read.assert_not_called()

    def test_committed_index_entries_survive_a_failed_chunk(self):
        with (
            patch(
                "apps.ebay_bulk_end.identifiers.get_item",
                side_effect=[item("306818711951"), EBayTransportError("timeout")],
            ),
            self.assertRaises(EBayTransportError),
        ):
            index_snapshot_identifiers(self.snapshot, self.job, "token", "xl-seller")
        self.assertEqual(EbayListingIdentifiers.objects.count(), 1)
        with (
            patch(
                "apps.ebay_bulk_end.identifiers.get_item",
                return_value=item("306818711952"),
            ) as read,
            patch("apps.ebay_bulk_end.identifiers.save_job"),
        ):
            self.assertFalse(
                index_snapshot_identifiers(
                    self.snapshot, self.job, "token", "xl-seller"
                )
            )
        read.assert_called_once_with("token", "306818711952")

    def test_other_seller_or_item_id_is_not_indexed(self):
        for details in (item("306818711951", seller="other"), item("999999999999")):
            with (
                patch("apps.ebay_bulk_end.identifiers.get_item", return_value=details),
                self.assertRaises(BulkEndError),
            ):
                index_snapshot_identifiers(
                    self.snapshot, self.job, "token", "xl-seller"
                )
        self.assertFalse(EbayListingIdentifiers.objects.exists())

    def test_expired_or_different_seller_index_does_not_skip_live_reads(self):
        for number, seller in (
            ("306818711951", "immutable-xl"),
            ("306818711952", "other"),
        ):
            EbayListingIdentifiers.objects.create(
                environment="production",
                seller_id=seller,
                item_id=number,
                identifiers=[EAN],
                checked_at=timezone.now() - timedelta(days=2),
            )
        with (
            patch(
                "apps.ebay_bulk_end.identifiers.get_item",
                side_effect=lambda _token, number: item(number),
            ) as read,
            patch("apps.ebay_bulk_end.identifiers.save_job"),
        ):
            self.assertFalse(
                index_snapshot_identifiers(
                    self.snapshot, self.job, "token", "xl-seller"
                )
            )
        self.assertEqual(read.call_count, 2)

    def test_manufacturer_match_is_rechecked_before_ending(self):
        details = item("306818711951", mpn=True)
        selected = item_summary(details, {EAN})
        with patch("apps.ebay_bulk_end.workflows.get_item", return_value=details):
            self.assertEqual(
                _verify_item("token", selected, {"seller_username": "xl-seller"}),
                ("ready", ""),
            )
            details.find(".//Value").text = "XLM4006381333931"
            self.assertEqual(
                _verify_item("token", selected, {"seller_username": "xl-seller"})[0],
                "changed",
            )

    def test_candidate_set_stays_fixed_when_shared_index_changes_between_chunks(self):
        with (
            patch(
                "apps.ebay_bulk_end.inventory.access_context",
                return_value=(SimpleNamespace(ebay_user_id="immutable-xl"), "token"),
            ),
            patch(
                "apps.ebay_bulk_end.inventory.verified_seller_username",
                return_value="xl-seller",
            ),
            patch("apps.ebay_bulk_end.inventory.save_job"),
            patch("apps.ebay_bulk_end.identifiers.save_job"),
            patch(
                "apps.ebay_bulk_end.identifiers.get_item",
                side_effect=lambda _token, number: item(number),
            ),
            patch(
                "apps.ebay_bulk_end.inventory.get_item",
                side_effect=lambda _token, number: item(number),
            ),
            patch("apps.ebay_bulk_end.inventory.VERIFY_ITEMS_PER_TASK", 1),
        ):
            self.assertEqual(run_inventory_scan_chunk(self.job), 1)
            self.assertEqual(run_inventory_scan_chunk(self.job), 1)
            EbayListingIdentifiers.objects.filter(item_id="306818711952").update(
                identifiers=[]
            )
            self.snapshot.expires_at = timezone.now() - timedelta(minutes=1)
            self.snapshot.save(update_fields=("expires_at",))
            self.assertIsNone(run_inventory_scan_chunk(self.job))
        self.assertEqual(len(self.job["matches"]), 2)
