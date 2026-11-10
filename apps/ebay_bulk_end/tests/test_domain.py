from datetime import timedelta
from unittest.mock import Mock, patch
from xml.etree import ElementTree as ET

from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from apps.ebay_bulk_end.auth import access_context, connection_for
from apps.ebay_bulk_end.domain import (
    exact_listing_matches,
    item_summary,
    normalize_eans,
    safe_csv_cell,
    valid_ean,
)
from apps.ebay_bulk_end.ebay import end_items
from apps.ebay_bulk_end.errors import BulkEndError, EBayRateLimitError

NS = "urn:ebay:apis:eBLBaseComponents"


def make_ean(body):
    weighted_sum = sum(
        int(digit) * (3 if offset % 2 == 0 else 1)
        for offset, digit in enumerate(reversed(body))
    )
    return body + str((-weighted_sum) % 10)


class EanTests(SimpleTestCase):
    def test_validates_ean8_and_ean13_as_strings(self):
        ean8 = make_ean("0123456")
        ean13 = make_ean("001234567890")
        self.assertTrue(valid_ean(ean8))
        self.assertTrue(valid_ean(ean13))
        self.assertEqual(normalize_eans([ean8, " " + ean8 + " ", ean13]), [ean8, ean13])

    def test_rejects_bad_checksum_and_non_digits(self):
        self.assertFalse(valid_ean("4006381333932"))
        self.assertFalse(valid_ean("40063813339A1"))
        with self.assertRaises(BulkEndError):
            normalize_eans(["4006381333932"])

    def test_matches_only_exact_listing_and_variation_eans(self):
        item = ET.fromstring(
            f'<Item xmlns="{NS}"><ItemID>123456789012</ItemID>'
            "<Title>EAN 4006381333931</Title>"
            "<ProductListingDetails><EAN>0012345678905</EAN></ProductListingDetails>"
            "<Variations><Variation><SKU>not-an-ean</SKU>"
            "<VariationProductListingDetails><EAN>4006381333931</EAN>"
            "</VariationProductListingDetails></Variation></Variations></Item>"
        )
        matches = exact_listing_matches(item, {"4006381333931", "0012345678905"})
        self.assertEqual(
            matches, {"0012345678905": "listing", "4006381333931": "variation"}
        )
        summary = item_summary(item, {"4006381333931"})
        self.assertTrue(summary["risky"])
        self.assertEqual(summary["variation_count"], 1)
        self.assertEqual(summary["item_id"], "123456789012")

    def test_marks_auction_bids_as_risky_but_fixed_price_stock_as_safe(self):
        fixed = ET.fromstring(
            f'<Item xmlns="{NS}"><ItemID>123456789012</ItemID><ListingType>FixedPriceItem</ListingType>'
            "<ProductListingDetails><EAN>4006381333931</EAN></ProductListingDetails>"
            "<SellingStatus><BidCount>0</BidCount></SellingStatus></Item>"
        )
        auction = ET.fromstring(
            f'<Item xmlns="{NS}"><ItemID>123456789013</ItemID><ListingType>Chinese</ListingType>'
            "<ProductListingDetails><EAN>4006381333931</EAN></ProductListingDetails>"
            "<SellingStatus><BidCount>2</BidCount></SellingStatus></Item>"
        )
        fixed_summary = item_summary(fixed, {"4006381333931"})
        auction_summary = item_summary(auction, {"4006381333931"})
        self.assertFalse(fixed_summary["auction_risky"])
        self.assertTrue(auction_summary["auction_risky"])
        self.assertEqual(auction_summary["bid_count"], 2)

    def test_formula_cells_are_escaped(self):
        self.assertEqual(safe_csv_cell("=1+1"), "'=1+1")
        self.assertEqual(safe_csv_cell("Normal title"), "Normal title")


class AccountSafetyTests(SimpleTestCase):
    @override_settings(EBAY_ENVIRONMENT="production")
    def test_depotum_is_not_accepted(self):
        with self.assertRaises(BulkEndError):
            connection_for("depotum")

    @override_settings(EBAY_ENVIRONMENT="sandbox")
    @patch(
        "apps.ebay_bulk_end.auth.token_request", return_value={"access_token": "fresh"}
    )
    @patch("apps.ebay_bulk_end.auth.decrypt_token", return_value="refresh")
    @patch("apps.ebay_bulk_end.auth.api_base_url")
    @patch("apps.ebay_bulk_end.auth.EbayConnection.objects.filter")
    def test_refresh_keeps_token_out_of_the_database(
        self, filter_connections, api_base, decrypt, refresh
    ):
        connection = Mock()
        connection.access_token_expires_at = timezone.now() - timedelta(minutes=1)
        connection.refresh_token_expires_at = timezone.now() + timedelta(days=1)
        connection.access_token_encrypted = "encrypted-access"
        connection.refresh_token_encrypted = "encrypted-refresh"
        filter_connections.return_value.first.return_value = connection
        actual_connection, token = access_context("jv", "sandbox")
        self.assertIs(actual_connection, connection)
        self.assertEqual(token, "fresh")
        connection.save.assert_not_called()
        refresh.assert_called_once()


class EndItemsTests(SimpleTestCase):
    @override_settings(EBAY_ENVIRONMENT="sandbox")
    @patch("apps.ebay_bulk_end.ebay.build_marketplace_session")
    def test_enditems_uses_not_available_and_returns_item_outcomes(self, build_session):
        post = build_session.return_value.__enter__.return_value.post
        post.return_value.status_code = 200
        post.return_value.ok = True
        post.return_value.content = (
            f'<EndItemsResponse xmlns="{NS}"><Ack>Success</Ack>'
            "<EndItemResponseContainer><CorrelationID>123456789012</CorrelationID>"
            "<EndTime>2026-11-10T12:00:00Z</EndTime></EndItemResponseContainer></EndItemsResponse>"
        ).encode()
        response = end_items("access", ["123456789012"])
        self.assertEqual(response["123456789012"]["end_time"], "2026-11-10T12:00:00Z")
        sent = ET.fromstring(post.call_args.kwargs["data"])
        container = sent.find(f"{{{NS}}}EndItemRequestContainer")
        self.assertIsNotNone(container)
        self.assertEqual(container.findtext(f"{{{NS}}}MessageID"), "123456789012")
        self.assertEqual(container.findtext(f"{{{NS}}}ItemID"), "123456789012")
        self.assertEqual(container.findtext(f"{{{NS}}}EndingReason"), "NotAvailable")
        self.assertIsNone(container.find(f"{{{NS}}}CorrelationID"))
        self.assertNotIn(b"access", post.call_args.kwargs["data"])

    @override_settings(EBAY_ENVIRONMENT="sandbox")
    def test_enditems_rejects_batches_larger_than_ten(self):
        with self.assertRaises(BulkEndError):
            end_items("access", [str(value) for value in range(11)])

    @override_settings(EBAY_ENVIRONMENT="sandbox")
    @patch("apps.ebay_bulk_end.ebay.build_marketplace_session")
    def test_enditems_returns_error_code_for_per_item_failure(self, build_session):
        post = build_session.return_value.__enter__.return_value.post
        post.return_value.status_code = 200
        post.return_value.ok = True
        post.return_value.content = (
            f'<EndItemsResponse xmlns="{NS}"><Ack>Failure</Ack>'
            "<EndItemResponseContainer><CorrelationID>123456789012</CorrelationID>"
            "<Ack>Failure</Ack><Errors><ErrorClassification>RequestError</ErrorClassification>"
            "<ErrorCode>21916700</ErrorCode><ShortMessage>Listing cannot be ended</ShortMessage>"
            "</Errors></EndItemResponseContainer></EndItemsResponse>"
        ).encode()
        response = end_items("access", ["123456789012"])
        self.assertEqual(response["123456789012"]["error_code"], "21916700")
        self.assertEqual(response["123456789012"]["classification"], "RequestError")

    @override_settings(EBAY_ENVIRONMENT="sandbox")
    @patch("apps.ebay_bulk_end.ebay.build_marketplace_session")
    def test_rejects_xml_entity_declarations(self, build_session):
        post = build_session.return_value.__enter__.return_value.post
        post.return_value.status_code = 200
        post.return_value.ok = True
        post.return_value.content = b'<!DOCTYPE a [<!ENTITY x "x">]><a>&x;</a>'
        with self.assertRaises(BulkEndError):
            end_items("access", ["123456789012"])

    @override_settings(EBAY_ENVIRONMENT="sandbox")
    @patch("apps.ebay_bulk_end.ebay.build_marketplace_session")
    def test_rate_limit_returns_bounded_retry_after(self, build_session):
        post = build_session.return_value.__enter__.return_value.post
        post.return_value.status_code = 429
        post.return_value.ok = False
        post.return_value.headers = {"Retry-After": "900"}
        with self.assertRaises(EBayRateLimitError) as error:
            end_items("access", ["123456789012"])
        self.assertEqual(error.exception.retry_after, 60)
