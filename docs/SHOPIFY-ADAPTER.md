# Shopify Adapter (real) — Setup & Operation

**Status: Phase 9b (2026-10-18).** The `ShopifyAdapter` (`backend/app/integrations/shopify.py`)
implements the `StoreIntegration` interface against the real Shopify Admin API
(REST, API version `2026-10`). All HTTP calls are mocked in tests —
**no real Shopify shop has ever been connected: no dev store, no credentials.**
The pipeline only uses the adapter when it is configured; otherwise it
silently falls back to the demo adapter.

## 1. What the adapter does

- **Read orders:** `GET /orders.json?name=%2310482&status=any` — search by the
  commercial order number (`name`, e.g. `#10482`). Not found → an honest
  "not found" (nothing invented).
- **Read/search products:** `GET /products/{id}.json` or
  `GET /products.json?title=…`; SKU search via result matching.
- **Shipment info:** `GET /orders/{id}/fulfillments.json` → `tracking_number`,
  `tracking_company`. If there is no shipment data, the adapter honestly
  returns `None` (the pipeline then says "no shipment information yet").
- **Webhooks:** `POST /api/integrations/shopify/webhooks` verifies
  HMAC-SHA256, deduplicates via `X-Shopify-Webhook-Id`, and logs
  `orders/create`, `orders/updated`, `refunds/create` in the audit log.
  Deliveries are only *logged* — no automatic order/refund actions.

## 2. Status mapping (Shopify → canonical)

| Shopify signal                     | Canonical status |
|------------------------------------|------------------|
| Order `cancelled = true`           | `cancelled`      |
| `fulfillment_status = fulfilled`   | `shipped`        |
| `fulfillment_status = partial`     | `processing`     |
| `fulfillment_status = unfulfilled` | `processing`     |
| `fulfillment_status = restocked`   | `processing`     |
| `fulfillment_status = null`/other  | `processing`     |

Honest limits: the REST API provides **no reliable "delivered" signal**
and knows no "delayed" — the adapter never emits these statuses (no guessing).
`eta_date` is always `None`; the pipeline then omits the delivery estimate.
Product attributes (e.g. dishwasher safety) have no Shopify equivalent —
`attributes_json` is `{}`, and the pipeline honestly answers "no information
available".

## 3. Required environment variables (backend `.env`, never commit)

| Variable                  | Required     | Purpose                                      |
|---------------------------|--------------|----------------------------------------------|
| `SHOPIFY_SHOP_DOMAIN`     | yes          | `muster-shop.myshopify.com` (domain only)    |
| `SHOPIFY_ADMIN_TOKEN`     | yes          | Custom app Admin API access token            |
| `SHOPIFY_ADMIN_API_TOKEN` | no           | legacy variable, still accepted              |
| `SHOPIFY_API_VERSION`     | no           | default `2026-10`                            |
| `SHOPIFY_WEBHOOK_SECRET`  | for webhooks | Custom app client secret (HMAC verification) |
| `STORE_BACKEND`           | no           | choose `shopify`; without configuration → demo |

If configuration is missing, the adapter reports "not configured" and the
pipeline automatically keeps using the demo adapter — nothing crashes.

## 4. Create a custom app in the Shopify admin (free)

1. Create a free partner account at partners.shopify.com (if you don't have
   one yet).
2. **Stores → Add store → Development store** — create a dev store
   (not a real shop, no cost).
3. In the dev store: **Settings → Apps and sales channels → Develop apps**
   (you may first need to enable "Allow custom app development").
4. Name the app, e.g. `Demo-Support-Agent`.
5. **Configuration → Admin API access scopes:** enable `read_orders`,
   `read_products`, `read_fulfillments` → save.
6. **API credentials → Admin API access token → Install token.**
   The token is shown only **once** — immediately save it to the backend
   `.env` as `SHOPIFY_ADMIN_TOKEN=<redacted>`;
   never commit it, never send it via chat/email.
7. In the same view you'll find the **Client secret** → save it as
   `SHOPIFY_WEBHOOK_SECRET=<redacted>`.
8. Set `SHOPIFY_SHOP_DOMAIN=<dev-store>.myshopify.com` and optionally
   `STORE_BACKEND=shopify` → restart the backend.

## 5. Register webhooks

In the dev store's Shopify admin: **Settings → Notifications →
Webhooks → Create webhook:**

| Event            | Format | URL                                                     |
|------------------|--------|---------------------------------------------------------|
| `orders/create`  | JSON   | `https://<your-host>/api/integrations/shopify/webhooks` |
| `orders/updated` | JSON   | `https://<your-host>/api/integrations/shopify/webhooks` |
| `refunds/create` | JSON   | `https://<your-host>/api/integrations/shopify/webhooks` |

API version: the one set under `SHOPIFY_API_VERSION` (default `2026-10`).
Every delivery is verified via HMAC (401 on a wrong signature),
deduplicated by webhook ID, and logged in the audit log.

## 6. Limits (honest)

- **No real shop connected:** Without a dev store + token nothing happens —
  the adapter reports "not configured", the demo keeps running.
- **Read only:** The adapter reads orders/products/fulfillments. It creates
  no orders, triggers no refunds, changes nothing in the shop.
- **No delivery estimate:** `eta_date` is always `None`.
- **No product attributes:** `attributes_json` is `{}`.
- **Answers in English:** The pipeline answers in the repo's language
  (this repo: English).
- **Rate limits:** 10 s timeout per request, 2 retries with backoff;
  HTTP 429 honors `Retry-After` (max. 10 s).

## 7. Security

- Token and webhook secret only in server environment variables / `.env`
  (`chmod 600`); **never** commit, log, or return them to the pipeline.
- Webhook signatures are verified with `hmac.compare_digest`
  (constant-time); invalid signatures → 401 + audit entry.
- The Admin API token only needs the three read scopes — grant no write
  permissions.
