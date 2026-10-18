"""Shopify webhook handling (Phase 9) — SIMULATED until a dev store exists.

Verification follows the documented Shopify behavior (checked
2026-10-09, https://shopify.dev/docs/api/webhooks): Shopify signs every
webhook with HMAC-SHA256 of the RAW request body using the app's client
secret, base64-encodes the digest, and sends it in the
``X-Shopify-Hmac-Sha256`` header. We recompute and compare with
``hmac.compare_digest`` (constant-time).

Idempotency: Shopify may deliver the same webhook multiple times. Each
``X-Shopify-Webhook-Id`` is recorded in the ``shopify_webhook_events``
table (``INSERT OR IGNORE``); a duplicate id is acknowledged with 200
without any reprocessing.

Current status: no dev store exists, so ``SHOPIFY_WEBHOOK_SECRET`` is
unset and the endpoint honestly returns 503 ("shopify_not_configured").
The HMAC + dedupe logic below is fully unit-tested with locally
computed signatures — no live Shopify traffic involved.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import sqlite3


def verify_webhook_hmac(
    raw_body: bytes, hmac_header: str | None, secret: str | None
) -> bool:
    """Verify a Shopify webhook signature.

    Returns True only when *hmac_header* equals the base64-encoded
    HMAC-SHA256 of *raw_body* keyed with *secret* (constant-time
    comparison). Returns False for a missing header, a missing secret,
    or a mismatch — never raises.
    """
    if not hmac_header or not secret:
        return False
    digest = hmac.new(
        secret.encode("utf-8"), raw_body or b"", hashlib.sha256
    ).digest()
    expected = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(expected, hmac_header.strip())


def record_webhook_event(
    conn: sqlite3.Connection,
    webhook_id: str,
    topic: str | None,
    shop_domain: str | None,
) -> bool:
    """Record a webhook delivery id for idempotency.

    Returns True when this is the FIRST delivery of *webhook_id*
    (caller should process it), False when it is a duplicate
    (acknowledge without reprocessing). The caller owns the transaction.
    """
    cur = conn.execute(
        "INSERT OR IGNORE INTO shopify_webhook_events "
        "(webhook_id, topic, shop_domain) VALUES (?, ?, ?)",
        (webhook_id, topic, shop_domain),
    )
    return cur.rowcount == 1


# ---------------------------------------------------------------------------
# WooCommerce webhooks
#
# Verification follows the documented WooCommerce behavior: WooCommerce signs
# each webhook delivery with HMAC-SHA256 of the RAW request body using the
# webhook secret, base64-encodes the digest, and sends it in the
# ``X-WC-Webhook-Signature`` header. The construction is identical to the
# Shopify scheme above (constant-time comparison).
#
# Idempotency: WooCommerce may redeliver webhooks. Each
# ``X-WC-Webhook-Delivery-ID`` is recorded in the
# ``woocommerce_webhook_events`` table (``INSERT OR IGNORE``); a duplicate
# id is acknowledged with 200 without any reprocessing.
# ---------------------------------------------------------------------------


def verify_woocommerce_webhook_signature(
    raw_body: bytes, signature_header: str | None, secret: str | None
) -> bool:
    """Verify a WooCommerce webhook signature.

    Returns True only when *signature_header* equals the base64-encoded
    HMAC-SHA256 of *raw_body* keyed with *secret* (constant-time
    comparison). Returns False for a missing header, a missing secret,
    or a mismatch — never raises.
    """
    if not signature_header or not secret:
        return False
    digest = hmac.new(
        secret.encode("utf-8"), raw_body or b"", hashlib.sha256
    ).digest()
    expected = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(expected, signature_header.strip())


def record_woocommerce_webhook_event(
    conn: sqlite3.Connection,
    delivery_id: str,
    topic: str | None,
) -> bool:
    """Record a WooCommerce webhook delivery id for idempotency.

    Returns True when this is the FIRST delivery of *delivery_id*
    (caller should process it), False when it is a duplicate
    (acknowledge without reprocessing). The caller owns the transaction.
    """
    cur = conn.execute(
        "INSERT OR IGNORE INTO woocommerce_webhook_events "
        "(delivery_id, topic) VALUES (?, ?)",
        (delivery_id, topic),
    )
    return cur.rowcount == 1


def verify_tiktok_shop_webhook(
    raw_body: bytes,
    authorization_header: str | None,
    app_key: str | None,
    app_secret: str | None,
) -> bool:
    """Verify a TikTok Shop webhook signature.

    Per the official webhook overview: HMAC-SHA256 hex digest of
    ``app_key + raw_body`` keyed with ``app_secret``, compared
    (constant-time) against the ``authorization`` header. Returns False
    for a missing header, missing key/secret, or a mismatch — never
    raises. NOTE: verify against a dev app before first live use.
    """
    if not authorization_header or not app_key or not app_secret:
        return False
    digest = hmac.new(
        app_secret.encode("utf-8"),
        app_key.encode("utf-8") + (raw_body or b""),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(
        digest, authorization_header.strip().lower()
    )


def record_tiktok_shop_webhook_event(
    conn: sqlite3.Connection,
    notification_id: str,
    topic: str | None,
    shop_id: str | None,
) -> bool:
    """Record a TikTok Shop webhook delivery id for idempotency.

    Returns True when this is the FIRST delivery of *notification_id*
    (caller should process it), False when it is a duplicate
    (acknowledge without reprocessing). The caller owns the transaction.
    """
    cur = conn.execute(
        "INSERT OR IGNORE INTO tiktok_shop_webhook_events "
        "(notification_id, topic, shop_id) VALUES (?, ?, ?)",
        (notification_id, topic, shop_id),
    )
    return cur.rowcount == 1
