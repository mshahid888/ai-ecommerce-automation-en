# TikTok Shop Adapter

Real adapter for the **TikTok Shop Open Platform** (REST API for
orders and products). It implements the
`StoreIntegration` interface: the chat pipeline, the dashboard, and the
returns triage don't care where an order comes from — TikTok Shop
is mapped onto the canonical data model like any other shop.

**Status:** implemented, tested offline. **No real shop**
connected yet — only after explicit approval.

## What the adapter does

- **Read orders:** search by order ID (`/order/202309/orders/search`),
  details including payment amount, currency, and — if present —
  parcel/tracking information.
- **Read/search products:** `/product/202309/products/...`
  (title, description, seller SKU).
- **Status mapping (honestly documented):**

  | TikTok Shop status | Canonical status |
  |--------------------|------------------|
  | UNPAID             | processing       |
  | AWAITING_SHIPMENT  | processing       |
  | AWAITING_COLLECTION| processing       |
  | PARTIALLY_SHIPPED  | shipped          |
  | IN_TRANSIT         | shipped          |
  | DELIVERED          | delivered        |
  | COMPLETED          | delivered        |
  | CANCELLED          | cancelled        |
  | unknown            | processing       |

  Unlike Shopify/WooCommerce, TikTok Shop reports real deliveries —
  `DELIVERED → delivered` is therefore honest, not guessed. `COMPLETED`
  means "delivered + 14 days without a return".
  `eta_date` is always `None` (no delivery-date field in the API).
- **Webhooks:** `POST /api/integrations/tiktok-shop/webhooks`.
  Signature verification via HMAC-SHA256 (hex) over `app_key + raw body`
  with `app_secret`, compared against the `authorization` header
  (constant time). Idempotency via `tts_notification_id`
  (`INSERT OR IGNORE`). Unknown topics are logged as "unhandled"
  but acknowledged with 200 (no retry storms).
  Malformed bodies never crash the endpoint.
  Webhooks are only **logged** — no automatic business actions.

## Marketplace reality (important)

TikTok Shop sells **in the app**. Our chat widget **cannot**
be embedded there. Customer messages run through TikTok
Shop's own customer-service/IM APIs and their policies. This
adapter covers **backend data synchronization** (orders and
products for pipeline + dashboard). In-app messaging automation is
deliberately **not** included — no workarounds around
platform rules.

## Setup (merchant side)

1. **TikTok Shop Partner Center:** register as a developer,
   choose region/market.
2. **Create an app** ("App & Service" → custom app for your own
   shop), request the required scopes (orders, products,
   webhooks) and wait for app review (approx. 2–3 business days; test in
   the sandbox first).
3. **Run through OAuth:** app key → authorization URL → seller
   approves → exchange the code for `access_token` + `refresh_token` +
   shop cipher. Plan for token expiry (refresh before expiry).
4. Store the **webhook URL** in the app
   (`https://<backend>/api/integrations/tiktok-shop/webhooks`),
   subscribe to events (e.g. order status changes).
5. **Backend `.env`** (server, `chmod 600`, never commit):

   ```env
   STORE_BACKEND=tiktok_shop
   TIKTOK_SHOP_APP_KEY=<app key from the Partner Center>
   TIKTOK_SHOP_APP_SECRET=<redacted>
   TIKTOK_SHOP_ACCESS_TOKEN=<redacted>
   TIKTOK_SHOP_SHOP_ID=<shop cipher>
   # optional:
   TIKTOK_SHOP_BASE_URL=https://open-api.tiktokglobalshop.com
   TIKTOK_SHOP_WEBHOOK_SECRET=<redacted>
   ```

   If a variable is missing, the adapter honestly reports
   "not configured" and the factory falls back to the demo backend
   — the chat keeps running.

## Security

- Secrets only via environment variables; never logged, never in
  API responses.
- Webhook signatures are compared with `hmac.compare_digest`;
  missing/invalid signatures → 401 + audit entry.
- 10 s timeout, max. 2 retries with backoff; HTTP 429 honors
  `Retry-After` (max. 10 s).

## Limits (honest)

- Request signature (`sign` parameter) follows the documented
  pattern — **validate against a dev app before the first live run**.
- Tracking/carrier only if TikTok provides parcel data; otherwise honestly
  "no information".
- Product attributes (e.g. dishwasher safety) don't exist on TikTok in this
  form — the pipeline honestly answers "no information".
- No in-app messaging (see above).
