from unittest.mock import patch
from xml.etree import ElementTree as ET

from django.test import SimpleTestCase, override_settings

from apps.ebay_bulk_end.domain import is_ack_success, trading_element
from apps.ebay_bulk_end.ebay import _call, get_active_page, get_item
from apps.ebay_bulk_end.errors import BulkEndError


class AuthorizationErrorTests(SimpleTestCase):
    @override_settings(EBAY_ENVIRONMENT="sandbox")
    @patch("apps.ebay_bulk_end.ebay.build_marketplace_session")
    def test_http_authorization_rejection_tells_manager_to_reauthorize(
        self, build_session
    ):
        post = build_session.return_value.__enter__.return_value.post
        post.return_value.status_code = 403
        post.return_value.ok = False
        post.return_value.content = b""

        with self.assertRaises(BulkEndError) as error:
            _call(
                "private-token",
                "GetMyeBaySelling",
                trading_element("GetMyeBaySellingRequest"),
            )

        self.assertIn("Повторно авторизуйте", str(error.exception))
        self.assertIn("Trading API", str(error.exception))
        self.assertNotIn("private-token", str(error.exception))

    @override_settings(EBAY_ENVIRONMENT="sandbox")
    @patch("apps.ebay_bulk_end.ebay.build_marketplace_session")
    def test_get_item_requests_item_specifics_for_ean_matching(self, build_session):
        post = build_session.return_value.__enter__.return_value.post
        post.return_value.status_code = 200
        post.return_value.ok = True
        post.return_value.content = (
            b"<GetItemResponse><Ack>Success</Ack><Item><ItemID>123</ItemID>"
            b"</Item></GetItemResponse>"
        )

        get_item("private-token", "123")

        request = ET.fromstring(post.call_args.kwargs["data"])
        self.assertEqual(request.findtext("{*}IncludeItemSpecifics"), "true")

    @override_settings(EBAY_ENVIRONMENT="sandbox")
    @patch("apps.ebay_bulk_end.ebay.build_marketplace_session")
    def test_active_listing_pages_use_stable_item_id_sort(self, build_session):
        post = build_session.return_value.__enter__.return_value.post
        post.return_value.status_code = 200
        post.return_value.ok = True
        post.return_value.content = (
            b"<GetMyeBaySellingResponse><Ack>Success</Ack><ActiveList>"
            b"<ItemArray><Item><ItemID>123</ItemID></Item></ItemArray>"
            b"<PaginationResult><TotalNumberOfPages>1</TotalNumberOfPages>"
            b"<TotalNumberOfEntries>1</TotalNumberOfEntries></PaginationResult>"
            b"</ActiveList></GetMyeBaySellingResponse>"
        )

        get_active_page("private-token", 1)

        request = ET.fromstring(post.call_args.kwargs["data"])
        self.assertEqual(request.findtext(".//{*}Sort"), "ItemID")
        self.assertEqual(request.findtext(".//{*}EntriesPerPage"), "15")

    def test_invalid_iaf_token_keeps_only_the_safe_diagnostic_code(self):
        response = ET.fromstring(
            "<GetItemResponse><Ack>Failure</Ack><Errors>"
            "<ErrorCode>21916984</ErrorCode><ShortMessage>Invalid IAF token</ShortMessage>"
            "</Errors></GetItemResponse>"
        )

        with self.assertRaises(BulkEndError) as error:
            is_ack_success(response)

        self.assertIn("21916984", str(error.exception))
        self.assertIn("Повторно авторизуйте", str(error.exception))
