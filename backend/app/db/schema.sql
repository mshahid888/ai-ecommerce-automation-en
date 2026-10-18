-- AI E-Commerce Automation backend schema (Phase 1).
--
-- Multi-store from day one: EVERY business table carries `store_id`
-- TEXT NOT NULL with a foreign key to stores(id). Repository code must
-- always filter by store_id so tenants never see each other's rows.

CREATE TABLE IF NOT EXISTS stores (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    slug        TEXT UNIQUE,
    is_demo     INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS customers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id    TEXT NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    email       TEXT,
    is_demo     INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS products (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id        TEXT NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    sku             TEXT NOT NULL,
    name            TEXT NOT NULL,
    description     TEXT,
    attributes_json TEXT,
    is_demo         INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(store_id, sku)
);

CREATE TABLE IF NOT EXISTS orders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id    TEXT NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    order_ref   TEXT NOT NULL,
    customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
    status      TEXT NOT NULL DEFAULT 'processing',
    carrier     TEXT,
    tracking_ref TEXT,
    eta_date    TEXT,
    total_cents INTEGER NOT NULL DEFAULT 0,
    currency    TEXT NOT NULL DEFAULT 'EUR',
    is_demo     INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(store_id, order_ref)
);

CREATE TABLE IF NOT EXISTS conversations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id    TEXT NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    session_key TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS tickets (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id            TEXT NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    conversation_id     INTEGER REFERENCES conversations(id) ON DELETE SET NULL,
    intent              TEXT,
    order_ref           TEXT,
    summary             TEXT,
    status              TEXT NOT NULL DEFAULT 'open',
    priority            TEXT NOT NULL DEFAULT 'normal',
    escalation_reason   TEXT,
    recommended_action  TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS return_requests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id    TEXT NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    order_ref   TEXT,
    status      TEXT NOT NULL DEFAULT 'pending',
    reason      TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS workflow_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id        TEXT NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    conversation_id INTEGER REFERENCES conversations(id) ON DELETE SET NULL,
    event_key       TEXT NOT NULL,
    label_de        TEXT NOT NULL,
    payload_json    TEXT,
    status          TEXT NOT NULL DEFAULT 'done',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS integration_credentials (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id    TEXT NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    provider    TEXT NOT NULL,
    label       TEXT,
    secret_ref  TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS store_policies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id    TEXT NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    policy_key  TEXT NOT NULL,
    policy_json TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(store_id, policy_key)
);

CREATE INDEX IF NOT EXISTS idx_customers_store        ON customers(store_id);
CREATE INDEX IF NOT EXISTS idx_products_store         ON products(store_id);
CREATE INDEX IF NOT EXISTS idx_orders_store           ON orders(store_id);
CREATE INDEX IF NOT EXISTS idx_orders_store_ref       ON orders(store_id, order_ref);
CREATE INDEX IF NOT EXISTS idx_conversations_store    ON conversations(store_id);
CREATE INDEX IF NOT EXISTS idx_messages_conversation  ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_tickets_store          ON tickets(store_id);
CREATE INDEX IF NOT EXISTS idx_return_requests_store  ON return_requests(store_id);
CREATE INDEX IF NOT EXISTS idx_events_store           ON workflow_events(store_id);
CREATE INDEX IF NOT EXISTS idx_events_conversation    ON workflow_events(conversation_id);
CREATE INDEX IF NOT EXISTS idx_credentials_store      ON integration_credentials(store_id);
CREATE INDEX IF NOT EXISTS idx_policies_store         ON store_policies(store_id);

-- Phase 7: append-only audit log (security events). Rows are never
-- updated or deleted by the application; the demo reset does NOT touch
-- this table, so abuse evidence survives resets.
CREATE TABLE IF NOT EXISTS audit_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    actor_ip_hash TEXT,
    action        TEXT NOT NULL,
    details_json  TEXT,
    store_id      TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);

-- Phase 9: Shopify webhook delivery log for idempotency. Shopify may
-- deliver the same webhook multiple times; X-Shopify-Webhook-Id is the
-- primary key so duplicates are acknowledged without reprocessing.
-- NOT wiped by the demo reset (integration evidence, like the audit log).
CREATE TABLE IF NOT EXISTS shopify_webhook_events (
    webhook_id  TEXT PRIMARY KEY,
    topic       TEXT,
    shop_domain TEXT,
    received_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_shopify_webhook_topic ON shopify_webhook_events(topic);

-- WooCommerce webhook delivery log for idempotency. WooCommerce may
-- redeliver the same webhook; X-WC-Webhook-Delivery-ID is the primary
-- key so duplicates are acknowledged without reprocessing.
-- NOT wiped by the demo reset (integration evidence, like the audit log).
CREATE TABLE IF NOT EXISTS woocommerce_webhook_events (
    delivery_id TEXT PRIMARY KEY,
    topic       TEXT,
    received_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_woocommerce_webhook_topic ON woocommerce_webhook_events(topic);

-- TikTok Shop webhook delivery log for idempotency. TikTok may
-- deliver the same notification multiple times; tts_notification_id
-- is the primary key so duplicates are acknowledged without
-- reprocessing. NOT wiped by the demo reset (integration evidence,
-- like the audit log).
CREATE TABLE IF NOT EXISTS tiktok_shop_webhook_events (
    notification_id TEXT PRIMARY KEY,
    topic           TEXT,
    shop_id         TEXT,
    received_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_tiktok_shop_webhook_topic ON tiktok_shop_webhook_events(topic);


-- Phase 7: daily real-LLM usage counters for the AI spending cap
-- (LLM_DAILY_REQUEST_CAP). Mock/stub calls are never counted.
CREATE TABLE IF NOT EXISTS ai_usage (
    usage_date TEXT NOT NULL,
    provider   TEXT NOT NULL,
    calls      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (usage_date, provider)
);
