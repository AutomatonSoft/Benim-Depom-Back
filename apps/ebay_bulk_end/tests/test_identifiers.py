from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4
from xml.etree import ElementTree as ET

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.ebay_bulk_end.errors import BulkEndError, EBayTransportError
from apps.ebay_bulk_end.identifiers import (
    discover_identifier_candidates,
    search_identifier_page,
)
from apps.ebay_bulk_end.inventory import run_inventory_scan_chunk
from apps.ebay_bulk_end.models import EbayInventoryListing, EbayInventorySnapshot

EAN = "4070863275101"


def item(number, value=EAN):
    return ET.fromstring(
        f"<Item><ItemID>{number}</ItemID><SKU>173930174</SKU><Title>Kommode</Title>"
        "<Seller><UserID>xl-seller</UserID></Seller><SellingStatus><ListingStatus>Active</ListingStatus>"
        "</SellingStatus><ListingType>FixedPriceItem</ListingType><ItemSpecifics>"
        f"<NameValueList><Name>Herstellernummer</Name><Value>XLM{value}</Value></NameValueList></ItemSpecifics></Item>"
    )


class IdentifierSearchTests(SimpleTestCase):
    def test_search_queries_are_paginated_deduplicated_and_resumable(self):
        job = {"eans": [EAN]}
        with (
            patch(
                "apps.ebay_bulk_end.identifiers.search_identifier_page",
                side_effect=[({"1"}, 200), EBayTransportError("timeout")],
            ),
            patch("apps.ebay_bulk_end.identifiers.save_job"),
            self.assertRaises(EBayTransportError),
        ):
            discover_identifier_candidates(job, "xl-seller")
        self.assertEqual(job["identifier_search_offset"], 200)
        with (
            patch(
                "apps.ebay_bulk_end.identifiers.search_identifier_page",
                side_effect=[({"2"}, None), ({"1"}, None)],
            ) as search,
            patch("apps.ebay_bulk_end.identifiers.save_job"),
        ):
            self.assertFalse(discover_identifier_candidates(job, "xl-seller"))
            self.assertTrue(discover_identifier_candidates(job, "xl-seller"))
        self.assertEqual(
            [call.args[2:] for call in search.call_args_list], [("q", 200), ("gtin", 0)]
        )
        self.assertEqual(job["identifier_search_ids"], ["1", "2"])

    def test_http_search_is_seller_scoped_and_rejects_invalid_pages(self):
        response = Mock(ok=True, status_code=200)
        response.json.return_value = {
            "total": 1,
            "itemSummaries": [{"itemId": "v1|306818711951|0"}],
        }
        with (
            patch(
                "apps.ebay_bulk_end.identifiers._application_token",
                return_value="app-token",
            ),
            patch("apps.ebay_bulk_end.identifiers.build_marketplace_session") as build,
        ):
            session = build.return_value.__enter__.return_value
            session.get.return_value = response
            self.assertEqual(
                search_identifier_page(EAN, "xl-seller", "q", 0),
                ({"306818711951"}, None),
            )
            self.assertEqual(
                session.get.call_args.kwargs["params"]["filter"],
                "sellers:{xl-seller},buyingOptions:{FIXED_PRICE|AUCTION}",
            )
            for payload in (
                {"total": 1},
                {"total": 1, "itemSummaries": [{"itemId": "bad"}]},
                {"total": 2001},
            ):
                response.json.return_value = payload
                with self.assertRaises(BulkEndError):
                    search_identifier_page(EAN, "xl-seller", "q", 0)


class FastIdentifierDiscoveryTests(TestCase):
    def test_only_search_hits_are_read_instead_of_the_whole_report(self):
        snapshot = EbayInventorySnapshot.objects.create(
            account="xl",
            environment="production",
            ebay_seller_id="immutable-xl",
            marketplace_id="EBAY_DE",
            status="complete",
            complete=True,
            expires_at=timezone.now() + timedelta(minutes=58),
        )
        EbayInventoryListing.objects.bulk_create(
            [
                EbayInventoryListing(
                    snapshot=snapshot,
                    item_id=str(306818711951 + index),
                    sku=str(index),
                    marketplace_id="EBAY_DE",
                )
                for index in range(100)
            ]
        )
        job = {
            "id": str(uuid4()),
            "kind": "scan",
            "status": "queued",
            "account": "xl",
            "environment": "production",
            "seller_id": "immutable-xl",
            "eans": [EAN],
            "inventory_snapshot_id": str(snapshot.pk),
            "discovery_details": {},
        }
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
                "apps.ebay_bulk_end.identifiers.search_identifier_page",
                return_value=({"306818711951", "999999999999"}, None),
            ) as search,
            patch(
                "apps.ebay_bulk_end.inventory.get_item",
                side_effect=lambda _token, number: item(
                    number, EAN if number == "306818711951" else "4006381333931"
                ),
            ) as read,
        ):
            while run_inventory_scan_chunk(job) is not None:
                pass
        self.assertEqual(search.call_count, 2)
        self.assertEqual(read.call_count, 2)
        self.assertEqual(job["status"], "preview")
        self.assertEqual([row["item_id"] for row in job["matches"]], ["306818711951"])
        self.assertFalse(job["discovery_details"]["ean_search_complete"])
