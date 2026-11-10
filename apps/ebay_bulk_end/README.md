# eBay bulk listing end

Manager tool at `/ebay` with API under `/api/v2/ebay-bulk-end/`. It searches active listings in the connected JV or XL seller account by exact EAN and ends only the ItemIDs explicitly selected from a complete, short-lived server preview. It does not edit BENIM product records or create SQL tables.

## Safety and limits

- The API is manager-only, and supports JV and XL. eBay returns listings for the seller authenticated by the request, so this cannot end listings belonging to other eBay sellers.
- EAN input is checked as EAN-8 or EAN-13 with its check digit and compared as a string to preserve leading zeroes. The UI reports duplicate input values.
- Each scan accepts 500 unique EANs by default; deployments can override this with the Django setting `EBAY_BULK_END_MAX_EANS`, and the UI reads the active limit from the API.
- Active listings are enumerated using `GetMyeBaySelling` (15 per page, sorted by ItemID). That response does not include listing EANs, so the scan retrieves each listing through `GetItem` with `IncludeItemSpecifics=true` before matching. A second pass verifies that the active ItemID set did not change during the lookup. eBay caps the active-list result at 25,000; scans at that cap are incomplete and cannot be confirmed. To leave room for the selected-item preflight and ending calls under eBay's documented default Trading API budget of 5,000 calls/day, scans above 2,000 active listings are marked incomplete. The keyset is shared with the rest of the app, so existing API usage may reduce this headroom; quota errors fail the job closed.
- `GetSellerList` is not used as a fallback: its required time window cannot prove completeness for old active listings.
- Listings with variations and auctions with bids or an unknown bid count are unselected by default and need separate opt-ins. The final check blocks auctions with bids ending within 12 hours, which eBay does not allow the seller to end early.
- The manager must type the selected account and confirm that `NotAvailable` (“item is no longer available for sale”) is the correct ending reason.
- Confirmation is bound to the manager, account, environment, exact server preview and its 15-minute expiry. Each selected item is fetched again and checked for seller, active status, EAN, listing type, variations and auction risk.
- `EndItems` uses batches of at most 10. Destructive calls are never retried automatically. An unclear outcome is reconciled with `GetItem` when bounded; otherwise it is reported as uncertain. Cancellation prevents the next batch but cannot retract a batch already sent to eBay.
- Read-only scans honor a bounded `Retry-After` after HTTP 429. XML entity declarations are rejected.
- Job input, preview, cancellation marker and report use namespaced Redis keys with a seven-day TTL. OAuth tokens are not stored in Redis. A refreshed access token is kept in memory and is not saved to `EbayConnection`.
- CSV output escapes spreadsheet formulas.

## API

- `GET /api/v2/ebay-bulk-end/connections/`
- `POST /api/v2/ebay-bulk-end/scans/` with `{ "account": "jv", "eans": ["..."] }`
- `GET /api/v2/ebay-bulk-end/jobs/<uuid>/`
- `POST /api/v2/ebay-bulk-end/jobs/<uuid>/confirm/` with `item_ids`, `typed_account`, `include_variations`, `include_auction_risk` and `confirm_unavailable_reason`
- `POST /api/v2/ebay-bulk-end/jobs/<uuid>/cancel/` to stop between batches
- `GET /api/v2/ebay-bulk-end/jobs/<uuid>/csv/`

Only the manager who created a job can read, confirm, cancel or download its report.

## eBay API notes

References checked on 2026-11-10:

- [GetMyeBaySelling](https://developer.ebay.com/devzone/xml/docs/Reference/ebay/GetMyeBaySelling.html)
- [GetItem](https://developer.ebay.com/devzone/xml/docs/Reference/ebay/GetItem.html)
- [EndItems](https://developer.ebay.com/devzone/xml/docs/Reference/ebay/EndItems.html)
- [OAuth authorization](https://developer.ebay.com/develop/guides/sell/authorization)
- [API deprecation status](https://developer.ebay.com/develop/api-deprecation-status)
- [API call limits](https://developer.ebay.com/develop/api/buy/api_call_limits)

The client pins Trading API compatibility level 1489, which matches the current Trading API reference. The deprecation list does not list the calls used here; it does list the `GetItem` field `eBayPlusEligible`, which this tool does not request or use. Recheck the status before production release.

## Tests

Run with `python manage.py test apps.ebay_bulk_end.tests`. Tests mock eBay HTTP calls and never send live `EndItems` requests.
