# Etsy Adapter (Etsy Open API v3)

Real Etsy adapter for the AI E-Commerce Automation platform.
Implements the `StoreIntegration` interface against the Etsy Open API v3.
All HTTP calls are mocked in tests — there has never been a real API call.

## What the adapter does

- **Orders**: fetch individual Etsy receipts
  (`GET /v3/application/shops/{shop_id}/receipts/{receipt_id}`) and list
  receipts (`.../receipts`). The merchant reference on Etsy is the
  numeric `receipt_id` (e.g. `#1234567890`).
- **Products**: search the shop's active listings
  (`GET /v3/application/listings/active?keywords=…&shop_id=…`),
  single fetch by `listing_id`.
- **Normalization**: Etsy data is translated into the platform's canonical
  format (order_ref, status, total_cents, currency …). What Etsy doesn't
  provide (tracking, delivery date, SKU on listings) stays honestly
  `None`/`{}` — the pipeline invents nothing.

## Status mapping (honestly documented)

Etsy receipts only know `was_paid` / `was_shipped`. There is **no**
delivered/delayed/cancelled signal — the adapter never invents these
statuses.

| Etsy signal                              | Canonical status |
|------------------------------------------|------------------|
| `was_shipped = true`                     | `shipped`        |
| `was_shipped = false`, `was_paid = true` | `processing`     |
| `was_paid = false`                       | `processing`     |
| flags missing / unknown                  | `processing`     |

## Setup (merchant)

1. Create an app in the [Etsy Developer Portal](https://www.etsy.com/developers)
   → copy the **API key**.
2. For order data (receipts), additionally set up **OAuth 2.0** and
   generate a token with shop read access → `ETSY_OAUTH_TOKEN`.
   Without an OAuth token the API responds with 401/403; the adapter then
   reports an English guide instead of silently empty results.
3. Take the **shop ID** from the Shop Manager (digits in the
   shop URL or shop settings).
4. In the backend `.env` (never commit, `chmod 600`):

```env
STORE_BACKEND=etsy
ETSY_API_KEY=<redacted>
ETSY_SHOP_ID=<shop-id-number>
ETSY_OAUTH_TOKEN=<redacted>
```

5. Restart the backend. Without configuration, the adapter reports
   `IntegrationConfigError` (English) and the factory automatically
   falls back to the demo backend — the pipeline always keeps running.

## Messaging reality (important)

Etsy **does not** allow embedding a third-party chat widget on
etsy.com pages. Buyer↔seller messages run through Etsy's own
"Convos" system under Etsy's rules. This adapter therefore covers
**backend data synchronization only** (orders and products for pipeline +
dashboard). Automated reading or sending of Convos messages is deliberately
**out of scope** — messaging automation outside Etsy's rules endangers the
merchant's shop.

## Security

- Secrets only via environment variables; `.env` is git-ignored.
- API key/OAuth token in memory only, never in logs or responses.
- 10 s timeout, 2 retries with backoff; 429 honors `Retry-After`.
- 401/403 → English guide (usually: check OAuth token/scopes).

## Limits

- No tracking, no delivery date, no SKU on listings (honestly
  `None`/`{}`).
- No webhooks: the Etsy API offers no public shop webhooks;
  synchronization runs via polling.
- `delivered`/`delayed`/`cancelled` are never reported.
- Verify against a real test shop before connecting a production
  shop — only with Shahid's written approval.
