# eBay Adapter (eBay Sell APIs)

Real eBay adapter for the AI E-Commerce Automation platform.
Implements the `StoreIntegration` interface against eBay's Sell APIs
(Fulfillment API for orders, Inventory API for the merchant
catalog). All HTTP calls are mocked in tests — there has never been a
real API call.

## What the adapter does

- **Orders**: fetch via the Fulfillment API
  (`GET /sell/fulfillment/v1/order/{orderId}`; eBay IDs look like
  `12-34567-89012`) and list orders (`.../order?limit=…`).
- **Products**: the merchant's **own catalog** via the Inventory API
  (`GET /sell/inventory/v1/inventory_item`, single SKU via
  `.../inventory_item/{sku}`).
- **Authentication**: OAuth 2.0 client-credentials flow
  (`POST /identity/v1/oauth2/token`, scopes `sell.fulfillment` +
  `sell.inventory`). The token is cached in memory and renewed before
  expiry. Alternatively, a ready-made token can be passed via
  `EBAY_OAUTH_TOKEN`.
- **Normalization**: eBay data is translated into the platform's canonical
  format. Tracking comes from the line items when present — otherwise
  honestly `None`.

## Status mapping (honestly documented)

The Fulfillment API only knows `orderFulfillmentStatus`. There is **no**
delivered/delayed signal and no cancellation status (cancellations go
through a separate eBay flow) — the adapter never invents these
statuses.

| eBay signal            | Canonical status |
|------------------------|------------------|
| `FULFILLED`            | `shipped`        |
| `IN_PROGRESS`          | `processing`     |
| `NOT_STARTED`          | `processing`     |
| missing / unknown      | `processing`     |

## Setup (merchant)

1. Create an app in the [eBay Developers Program](https://developer.ebay.com)
   (sandbox for testing, production later).
2. Copy the **client ID** and **client secret**; grant the app the
   `sell.fulfillment` and `sell.inventory` scopes.
3. Choose a marketplace (default: `EBAY_DE` for Germany).
4. In the backend `.env` (never commit, `chmod 600`):

```env
STORE_BACKEND=ebay
EBAY_CLIENT_ID=<client-id>
EBAY_CLIENT_SECRET=<redacted>
EBAY_MARKETPLACE_ID=EBAY_DE        # optional, default is EBAY_DE
# alternative: EBAY_OAUTH_TOKEN=<redacted>
```

5. Restart the backend. Without configuration, the adapter reports
   `IntegrationConfigError` (English) and the factory automatically
   falls back to the demo backend — the pipeline always keeps running.

## Messaging reality (important)

eBay **does not** allow embedding a third-party chat widget on
ebay.de item pages. Buyer↔seller messages run through eBay's
own messaging system under eBay's rules. This adapter therefore covers
**backend data synchronization only** (orders and products for pipeline +
dashboard). Automated reading or sending of eBay messages is deliberately
**out of scope** — messaging automation outside eBay's rules endangers the
seller account.

## Security

- Secrets only via environment variables; `.env` is git-ignored.
- Client secret/OAuth token in memory only, never in logs or
  responses (basic auth only against the token endpoint).
- 10 s timeout, 2 retries with backoff; 429 honors `Retry-After`.
- 401 → English guide (check credentials/scopes).

## Limits

- No delivery date (`eta_date` always `None`); tracking only if the
  line items include it.
- `delivered`/`delayed`/`cancelled` are never reported.
- No eBay webhooks implemented (the Notification API needs endpoint
  validation — documented as a follow-up, not cobbled together);
  synchronization runs via polling.
- Verify against the eBay sandbox before connecting a production
  account — only with Shahid's written approval.
