# eBay bulk listing end

Manager tool at `/ebay` with operation history at `/ebay/history`; API routes are under `/api/v2/ebay-bulk-end/`. JV, XL and Depotum stay separated by the selected OAuth connection and Sandbox/Production environment.

## Discovery for large accounts

The scanner requests eBay's `LMS_ACTIVE_INVENTORY_REPORT`, normalizes LMS XML (including ZIP/GZIP) or CSV into a private CSV file, and imports it into isolated `EbayInventorySnapshot` and `EbayInventoryListing` tables. Parent and variation SKUs are mapped to the parent ItemID. XML is parsed one listing at a time; DTDs, entities, truncated XML and unsuccessful reports are rejected. Snapshots are scoped to the authenticated seller, environment and the request's `EBAY_DE` marketplace context. LMS XML does not supply a per-row marketplace; candidates are verified live by ItemID and seller before review. Snapshots are reused for 58 minutes. Report creation intents are limited to 24 per seller/environment per rolling 24 hours, and download attempts have a separate 24-per-marketplace budget. Failed and uncertain requests remain in that quota history.

The report request intent is committed before the eBay POST. Creation accepts both `/task/{id}` and `/inventory_task/{id}` Location paths and uses the extracted ID with getInventoryTask. If a worker loses the response, it searches eBay's inventory task list for one matching unfiltered task in the request window. Sandbox recovery also checks the observed fixed seven-hour mislabelling of creation timestamps; production remains strictly UTC. Multiple matches or a task already attached to another snapshot fail closed. No uncertain POST is resent. The first status check occurs after five seconds; pending report polling then backs off to five-minute intervals. A scan checks committed snapshot progress every ten seconds. This does not add repeated eBay requests while a snapshot step is already scheduled or owned.

The complete report is streamed and normalized to `PRODUCT_IMAGE_STAGING_ROOT/ebay-bulk-end/` before import. This directory must be on a private persistent volume shared by the web app and every marketplace worker; the production compose file currently mounts `/app/product-media` for these processes. The app directory and per-snapshot folders use mode `0700`, and completed CSV files use `0600`. The report's size and SHA-256 are recorded. Import processes at most 10,000 CSV rows per Celery task, inserts at most 1,000 rows per SQL batch and commits each batch together with its CSV checkpoint. A crash resumes at the last committed record. The snapshot becomes visible only after the entire CSV reaches EOF and final counts pass verification. There is no application-defined total-row or report-byte ceiling. If the report cannot be fully downloaded, staged or parsed, the scan fails closed. More than 2,000 matched ItemIDs fails closed so the existing preview and confirmation remain bounded.

Expired snapshot listings are removed asynchronously in 1,000-row batches. Report files are removed in bounded batches, and snapshot quota metadata stays until its rolling download window has ended. Completed snapshots schedule their own cleanup after the 58-minute freshness window.

eBay documents 25 Active Inventory Report feeds per seller token per 24 hours and a generic 15 MB regular/compressed XML file limit. A real Sandbox LMS request returned ZIP/XML; MIP CSV documentation describes a separate feed flow and must not be used to infer LMS output. This module imposes no total-row ceiling and fails closed if eBay rejects a report or it cannot be fully staged/parsed. Real eBay file limits and infrastructure capacity still apply; synthetic million-row tests do not establish production report limits.

The report contains SKU and ItemID. Exact SKU and cached identifier candidates are combined with seller-filtered Browse searches (`q` and `gtin`) for each requested EAN. Search pagination is checkpointed in bounded tasks. Only candidate ItemIDs are fetched with `GetItem` to check EAN and MPN/Herstellernummer, including variations. There is no automatic whole-account GetItem traversal. Verified identifiers are cached in PostgreSQL per seller/environment for 24 hours. Manufacturer numbers may have ASCII letter prefixes/suffixes around a complete valid EAN, for example `XLM4070863275101`. Partial digit matches and conflicting EANs are blocked. Candidates are checked live again before ending. Public search is best-effort: `ean_search_complete` and `mpn_search_complete` remain false, even after all search pages are processed. An unmatched identifier does not prove absence from eBay. Browse errors or truncated pages fail explicitly instead of silently returning no matches.

## Ending safety

OAuth `EbayConnection.ebay_user_id` is the immutable Identity API `userId`; Trading `Seller.UserID` is the username. They must not be compared directly. Before candidate verification and again for each ending chunk, the module requests Identity API with the current token, verifies its `userId` against the stored connection/job ID and compares its confirmed `username` with the listing seller. Identity mismatch, a missing username or a failed lookup blocks the operation. The immutable ID remains the key for snapshots, quotas and in-flight reservations.

- Every candidate is checked again before `EndItems` for seller, active status, current identifiers, title, listing type, variations and auction risk.
- SKU, EAN and manufacturer number match types stay distinct, including variation matches. Populated conflicting EAN data blocks selection and confirmation.
- Multi-variation listings and auctions with bids or an unknown bid count are excluded from default selection and require separate confirmation. eBay ends the parent ItemID and all its variations.
- Depotum listings found in BENIM's publication records are protected. Lookup failure blocks the operation.
- The manager confirms the account and `NotAvailable` ending reason. `EndItems` sends at most 10 ItemIDs per call.
- A batch attempt and unique seller/environment/ItemID reservations are committed in PostgreSQL before the eBay request. A timeout or worker crash is reconciled read-only when possible; uncertain results are never resent automatically or shown as success.
- Cancellation stops later batches; it cannot recall a request already sent.

## Permanent operation history

New tables owned by this app are `EbayBulkEndJob`, `EbayBulkEndJobItem`, `EbayBulkEndEvent`, `EbayBulkEndInFlight`, `EbayInventorySnapshot` and `EbayInventoryListing`. Migrations are under this app only. Apply them in a reviewed deployment before enabling the new code; no production migration or eBay deletion was performed as part of this change.

PostgreSQL is the source of truth for jobs, inputs, candidates, selections, results, timestamps, report metadata and append-only events. Redis keeps the seven-day progress cache and worker/account locks. Reading a job reconstructs it from PostgreSQL first, so Redis expiry/restart does not erase history or bypass a durable in-flight reservation. There is no automatic PostgreSQL history cleanup.

The history API and page are manager-only and owner-only: managers see their own jobs, not other managers' audit history. List filtering and page size are server-side; CSV output includes item results and EANs not found in the current identifier index. Older jobs retain their original SKU-only coverage.

## API

- `GET /api/v2/ebay-bulk-end/connections/`
- `POST /api/v2/ebay-bulk-end/scans/`
- `GET /api/v2/ebay-bulk-end/jobs/<uuid>/`
- `POST /api/v2/ebay-bulk-end/jobs/<uuid>/confirm/`
- `POST /api/v2/ebay-bulk-end/jobs/<uuid>/cancel/`
- `GET /api/v2/ebay-bulk-end/jobs/<uuid>/csv/`
- `GET /api/v2/ebay-bulk-end/history/` (account, environment, date range, status/result, EAN, ItemID/SKU and owner filters; paginated)
- `GET /api/v2/ebay-bulk-end/history/<uuid>/`
- `GET /api/v2/ebay-bulk-end/history/<uuid>/items/` (paginated)
- `GET /api/v2/ebay-bulk-end/history/<uuid>/events/` (paginated)
- `GET /api/v2/ebay-bulk-end/history/<uuid>/csv/`

The Active Inventory Report requires the existing OAuth scope `https://api.ebay.com/oauth/api_scope/sell.inventory`. This module does not change global OAuth scopes or credentials. If the selected connection lacks permission, the report request fails closed with a reconnect instruction.

## References

- [Downloadable report flow](https://developer.ebay.com/api-docs/sell/static/feed/merchant-data-downloadable-reports-flow.html)
- [Active Inventory Report fields](https://developer.ebay.com/api-docs/user-guides/static/mip-user-guide/mip-definitions-active-inventory-report.html)
- [Feed limits](https://developer.ebay.com/api-docs/sell/static/feed/data-file-limits.html)
- [Sell Feed API specification](https://developer.ebay.com/develop/api/spec/feed_api.json)
- [Sell Feed scope and file retention](https://developer.ebay.com/api-docs/sell/static/feed/sell-feed.html)
- [GetSellerList time window](https://developer.ebay.com/devzone/xml/docs/Reference/ebay/GetSellerList.html)
- [Browse API search](https://www.developer.ebay.com/develop/api/buy)

## Report diagnostics

Search progress distinguishes report creation (`requesting_report`), recovery of an unconfirmed request (`recovering_report_request`), accepted reports (`waiting_for_report`), downloading, importing and verification. An unknown total is displayed as indeterminate progress. The page shows the job ID for support/log correlation. Recovery checks the original request for up to ten minutes and does not send another creation POST.

Inventory errors are written to the marketplace worker log as `eBay inventory error` and persisted in the job's audit events (`snapshot_request_uncertain` / `snapshot_report_error`). Each entry includes job ID, snapshot ID, account, environment, stage and known eBay task ID. Feed diagnostics contain the operation, failure reason (HTTP/network/invalid response/missing task ID), HTTP status, allowlisted request identifiers and numeric eBay error IDs when available. Authorization headers, tokens, raw response bodies and network exception text are excluded. `snapshot_recovery_checked` records each recovery outcome. For staging, use `docker logs --since 30m benim-depom-stage-worker_marketplace-1 2>&1 | grep '<job-id>'`; permanent events are available through the existing history events API.

## Tests

Run with `python manage.py test apps.ebay_bulk_end.tests`. All eBay calls are mocked; tests do not send live `EndItems` requests. The parser tests stream 100,000, 500,000 and 1,000,001 synthetic rows through bounded chunks. The warm-index lookup test stores 100,000 rows in PostgreSQL. Identifier tests cover seller-filtered searches, pagination, resumable queries, malformed responses and reading only candidate listings.
