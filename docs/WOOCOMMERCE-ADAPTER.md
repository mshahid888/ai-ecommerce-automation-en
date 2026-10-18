# WooCommerce Adapter (real) — Setup & Operation

Real WooCommerce REST API adapter (`WooCommerceAdapter`) for the
AI E-Commerce Automation platform. Tested offline (31 tests, all
HTTP calls mocked) — **never run against a real shop**.
Verify against a test shop before connecting a merchant shop.

## 1. What the adapter does

- `get_order("#12345")` — load an order by WooCommerce order number
  (`GET /wp-json/wc/v3/orders/{id}`). WooCommerce order numbers are
  numeric IDs by default (`#12345` and `12345` both work;
  custom order-number plugins are not supported).
- `get_product()` / `search_products()` — products by ID, SKU, or
  full-text search (`/wp-json/wc/v3/products`).
- `get_fulfillment()` — tracking/shipping info from order meta fields
  (shipping plugins), if present.
- `list_orders()` — latest orders (max. 100 per fetch).
- Webhook endpoint `POST /api/integrations/woocommerce/webhooks` for
  `order.created` / `order.updated` — logging only (idempotent),
  no automatic business actions.

## 2. Status mapping (WooCommerce → canonical)

| WooCommerce status | Canonical     | Reason                             |
|--------------------|---------------|------------------------------------|
| `pending`          | `processing`  | waiting for payment                |
| `processing`       | `processing`  |                                    |
| `on-hold`          | `processing`  | waiting for manual action          |
| `completed`        | `shipped`     | convention: fulfilled/shipped (see below) |
| `cancelled`        | `cancelled`   |                                    |
| `refunded`         | `cancelled`   | end state, treated as cancelled    |
| `failed`           | `cancelled`   | payment failed — never fulfilled   |
| `trash`            | `cancelled`   |                                    |
| unknown            | `processing`  | safe default                       |

**Honestly documented:** WooCommerce has no native "shipped" status —
`completed` → `shipped` is a convention, not a guarantee (some shops set
`completed` as soon as payment arrives). `delivered` and `delayed` are
**never invented**: WooCommerce provides no reliable signal for them.
Likewise, there is no native tracking field — tracking only comes from
shipping-plugin meta fields (`_tracking_number` etc.); without them,
`tracking_ref` stays empty and the pipeline says so honestly.

## 3. Required environment variables (backend `.env`, never commit)

```bash
STORE_BACKEND=woocommerce
WOOCOMMERCE_SHOP_URL=https://example-shop.de   # https ONLY (required)
WOOCOMMERCE_CONSUMER_KEY=ck_...
WOOCOMMERCE_CONSUMER_SECRET=<redacted>
WOOCOMMERCE_WEBHOOK_SECRET=<redacted>
```

If configuration is missing, the factory reports an English warning and
silently falls back to the demo backend — the pipeline keeps running.

## 4. Generate REST API keys (free)

1. In the WordPress backend: **WooCommerce → Settings → Advanced →
   REST API → Add key**.
2. Choose a user, **permission "Read"** (sufficient for this adapter),
   **Generate API key**.
3. Enter the consumer key + consumer secret in the backend `.env`
   (`chmod 600`), restart the backend.

## 5. Register webhooks

1. **WooCommerce → Settings → Advanced → Webhooks → Add webhook**: topic
   `Order created` / `Order updated`, delivery URL
   `https://<backend-domain>/api/integrations/woocommerce/webhooks`,
   set a strong **secret** → add it to `.env` as
   `WOOCOMMERCE_WEBHOOK_SECRET`.
2. The adapter verifies `X-WC-Webhook-Signature` (HMAC-SHA256, constant-time
   comparison); duplicate `X-WC-Webhook-Delivery-ID`s are processed exactly
   once.
3. Without a configured secret, the endpoint honestly responds with 503
   (`woocommerce_not_configured`).

## 6. Limits (honest)

- No test against a real shop has been done — mapping table and
  meta fields must be verified on a test shop.
- Custom order-number plugins are not supported.
- Product attributes come from the WooCommerce attribute list; shops without
  attributes return `{}` (the pipeline answers honestly).
- `eta_date` is always empty (no WooCommerce field for it).
- Webhooks are only logged, not processed.

## 7. Security

- HTTPS only (HTTP URLs are rejected at startup — otherwise keys would
  end up in server logs).
- Keys only in server environment variables, never in code/frontend/git.
- 10 s timeout, 2 retries with backoff, `Retry-After` on 429, no
  token leakage in error messages or payloads.
- Webhook signature verification + idempotency + audit log, as with the
  Shopify adapter.
