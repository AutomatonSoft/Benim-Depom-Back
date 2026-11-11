# eBay bulk listing end

Manager tool at `/ebay` with operation history at `/ebay/history`; API routes are under `/api/v2/ebay-bulk-end/`. JV, XL and Depotum stay separated by the selected OAuth connection and Sandbox/Production environment.

## Discovery for large accounts

The scanner requests eBay's `LMS_ACTIVE_INVENTORY_REPORT` and imports its CSV into isolated `EbayInventorySnapshot` and `EbayInventoryListing` tables. It keeps only the report's SKU, ItemID and marketplace mapping in PostgreSQL. Reports are scoped to the authenticated seller, environment and `EBAY_DE` marketplace. Snapshots are reused for 58 minutes. The module starts at most 24 report downloads per seller and marketplace in a rolling 24-hour window, below eBay's documented 25-download limit.

The report request intent is committed before the eBay POST. If a worker loses the response, it searches eBay's inventory task list for one matching unfiltered task in the request window. It does not resend an uncertain POST; a missing or ambiguous task makes the snapshot incomplete. A pending report can take up to 24 hours before timeout, and polling backs off to five-minute intervals.

The complete report is streamed and normalized to `PRODUCT_IMAGE_STAGING_ROOT/ebay-bulk-end/` before import. This directory must be on a private persistent volume shared by the web app and every marketplace worker; the production compose file currently mounts `/app/product-media` for these processes. The app directory and per-snapshot folders use mode `0700`, and completed CSV files use `0600`. The report's size and SHA-256 are recorded. Import processes at most 10,000 CSV rows per Celery task, inserts at most 1,000 rows per SQL batch and commits each batch together with its CSV checkpoint. A crash resumes at the last committed record. The snapshot becomes visible only after the entire CSV reaches EOF and final counts pass verification. There is no application-defined total-row or report-byte ceiling. If the report cannot be fully downloaded, staged or parsed, the scan fails closed. More than 2,000 matched ItemIDs fails closed so the existing preview and confirmation remain bounded.

Expired snapshot listings are removed asynchronously in 1,000-row batches. Report files are removed in bounded batches, and snapshot quota metadata stays until its rolling download window has ended. Completed snapshots schedule their own cleanup after the 58-minute freshness window.

eBay publishes a limit of 25 Active Inventory Report downloads per seller token per 24 hours. Its general Feed limits page says data files are limited to 15 MB as regular or compressed XML; the Active Inventory Report is returned as CSV. The REST API specification describes the download as a streamed binary response but does not declare a maximum size, and the docs do not clarify whether the generic XML limit applies to CSV downloads. This module does not guess or impose its own byte/row ceiling. If eBay rejects the task/download or the complete CSV cannot be staged or parsed, discovery fails closed. A live report from a very large seller account is still needed to verify eBay's effective CSV limit before claiming production validation at that scale.

The report contains SKU and ItemID, not EAN. A SKU equal to an EAN is a direct candidate match. Listings with another SKU may be missed; `not_found_in_sku_index` means only that no exact SKU was found, not that the EAN does not exist on eBay. Browse GTIN search can be considered as a separate best-effort source, but it is not part of this implementation and cannot guarantee whole-account coverage. `GetSellerList` is not used as a fallback because its required time window is limited to 120 days. Search completeness therefore describes the completed SKU index only; it does not claim exhaustive EAN search.

## Ending safety

- Every candidate is checked again before `EndItems` for seller, active status, current identifiers, title, listing type, variations and auction risk.
- SKU and EAN stay distinct. Exact SKU, exact variation SKU, exact EAN and exact variation EAN match types are recorded. Populated conflicting EAN data blocks selection and confirmation.
- Multi-variation listings and auctions with bids or an unknown bid count are excluded from default selection and require separate confirmation. eBay ends the parent ItemID and all its variations.
- Depotum listings found in BENIM's publication records are protected. Lookup failure blocks the operation.
- The manager confirms the account and `NotAvailable` ending reason. `EndItems` sends at most 10 ItemIDs per call.
- A batch attempt and unique seller/environment/ItemID reservations are committed in PostgreSQL before the eBay request. A timeout or worker crash is reconciled read-only when possible; uncertain results are never resent automatically or shown as success.
- Cancellation stops later batches; it cannot recall a request already sent.

## Permanent operation history

New tables owned by this app are `EbayBulkEndJob`, `EbayBulkEndJobItem`, `EbayBulkEndEvent`, `EbayBulkEndInFlight`, `EbayInventorySnapshot` and `EbayInventoryListing`. Migrations are under this app only. Apply them in a reviewed deployment before enabling the new code; no production migration or eBay deletion was performed as part of this change.

PostgreSQL is the source of truth for jobs, inputs, candidates, selections, results, timestamps, report metadata and append-only events. Redis keeps the seven-day progress cache and worker/account locks. Reading a job reconstructs it from PostgreSQL first, so Redis expiry/restart does not erase history or bypass a durable in-flight reservation. There is no automatic PostgreSQL history cleanup.

The history API and page are manager-only and owner-only: managers see their own jobs, not other managers' audit history. List filtering and page size are server-side; CSV output includes item results and EANs not found in the SKU index.

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

Run with `python manage.py test apps.ebay_bulk_end.tests`. All eBay calls are mocked; tests do not send live `EndItems` requests. The parser tests stream 100,000, 500,000 and 1,000,001 synthetic rows through bounded chunks; the lookup test stores 100,000 rows in PostgreSQL and asserts only matching SKU candidates call `GetItem`.
