"""Real WooCommerce REST API adapter — fully implemented, tested offline.

``WooCommerceAdapter`` implements ``StoreIntegration`` against the
WooCommerce REST API (v3). Every method is implemented against the
documented request/response mapping. All HTTP traffic is mocked in
tests — no live shop call has ever been made; none is made without a
configured store (``WOOCOMMERCE_SHOP_URL`` + ``WOOCOMMERCE_CONSUMER_KEY``
+ ``WOOCOMMERCE_CONSUMER_SECRET``).

Docs reference (checked 2026-10-18):
- REST API overview: https://woocommerce.github.io/woocommerce-rest-api-docs/
- Authentication: ``https://{shop}/wp-json/wc/v3/`` with HTTP Basic Auth
  (consumer key as username, consumer secret as password) — Basic Auth
  only works over HTTPS; over plain HTTP WooCommerce forces query-string
  auth, which leaks secrets into server logs. This adapter therefore
  REFUSES non-HTTPS shop URLs.
- Orders: ``GET /wp-json/wc/v3/orders/{id}`` (WooCommerce order numbers
  are numeric post IDs by default, e.g. ``#12345`` → id ``12345``);
  list: ``GET /wp-json/wc/v3/orders``.
- Products: ``GET /wp-json/wc/v3/products/{id}``;
  ``GET /wp-json/wc/v3/products?sku={sku}``;
  ``GET /wp-json/wc/v3/products?search={query}``.
- Webhooks: WooCommerce signs each delivery with HMAC-SHA256 of the RAW
  request body using the webhook secret, base64-encoded, sent in the
  ``X-WC-Webhook-Signature`` header; delivery id in
  ``X-WC-Webhook-Delivery-ID`` — see ``webhooks.py``.

Construction rules (hard):
- Only from server-side env vars ``WOOCOMMERCE_SHOP_URL`` +
  ``WOOCOMMERCE_CONSUMER_KEY`` + ``WOOCOMMERCE_CONSUMER_SECRET`` — never
  from the frontend, never from a committed file. Missing vars →
  ``IntegrationConfigError`` with setup instructions; no network call is
  ever attempted unconfigured.
- Secrets live only in memory / server env (``chmod 600`` env file on
  the server). They are never logged, never returned in payloads.

Reliability: 10 s timeout per request, up to 2 retries with exponential
backoff (0.5 s, 1 s). HTTP 429 honors ``Retry-After`` (capped at 10 s).
HTTP 401 raises with explicit credential guidance; other failures raise
``IntegrationError`` without leaking secrets.

Status mapping (documented honestly — WooCommerce has no native
"shipped" state; ``completed`` conventionally means the order was
fulfilled/shipped)::

    pending     → processing   (waiting for payment)
    processing  → processing
    on-hold     → processing   (waiting for manual action)
    completed   → shipped      (fulfilled — see caveat below)
    cancelled   → cancelled
    refunded    → cancelled    (terminal, like cancelled)
    failed      → cancelled    (payment failed — never fulfilled)
    trash       → cancelled
    <unknown>   → processing   (safe default, mirrors Shopify adapter)

``delivered`` and ``delayed`` are NEVER invented: WooCommerce exposes no
reliable delivered/delayed signal in the core order payload.

Mapping caveats (to verify against a real test shop):
- ``completed`` → ``shipped`` is a convention, not a guarantee: some
  shops mark orders ``completed`` on payment without shipping. Tracking
  data (when present) is the stronger signal.
- Tracking/carrier: native WooCommerce has NO tracking fields. The
  adapter reads common shipment-plugin order meta keys
  (``_tracking_number`` / ``tracking_number``,
  ``_tracking_provider`` / ``tracking_provider`` / ``carrier``);
  without them, carrier/tracking_ref stay None and the pipeline says so
  honestly.
- WooCommerce has no order ETA field: ``eta_date`` is always None.
- Product attributes map from the product's ``attributes`` list
  (``{name: options}``); shops without attributes yield {}.

EXPLICIT RULE: never point this at a real store without Shahid's
written go-ahead. Test shop first (see docs/WOOCOMMERCE-ADAPTER.md).
"""

from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation
from typing import Any, TYPE_CHECKING

from .base import IntegrationConfigError, IntegrationError, StoreIntegration

if TYPE_CHECKING:  # pragma: no cover
    from ..config import Settings

#: WooCommerce → canonical status mapping (see module docstring).
WOOCOMMERCE_STATUS_MAP = {
    "pending": "processing",
    "processing": "processing",
    "on-hold": "processing",
    "completed": "shipped",
    "cancelled": "cancelled",
    "refunded": "cancelled",
    "failed": "cancelled",
    "trash": "cancelled",
}
#: Timeout per WooCommerce HTTP request (seconds).
REQUEST_TIMEOUT_SECONDS = 10
#: Number of retries after the initial attempt (2 retries = 3 attempts max).
MAX_RETRIES = 2
_BACKOFF_SECONDS = (0.5, 1.0)
#: Order meta keys (shipment plugins) consulted for tracking info —
#: native WooCommerce has no tracking fields.
_TRACKING_NUMBER_KEYS = ("_tracking_number", "tracking_number")
_CARRIER_KEYS = ("_tracking_provider", "tracking_provider", "carrier")

_TAG_RE = re.compile(r"<[^>]+>")


def _http_get_json(url: str, headers: dict[str, str], timeout: int):
    """Module-level so tests can monkeypatch it (no real network in tests).

    Returns ``(status_code, parsed_json)``. Raises
    ``urllib.error.URLError`` / ``HTTPError`` on transport failure.
    """
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def _strip_html(text: str | None) -> str:
    return _TAG_RE.sub("", text or "").strip()


def _price_to_cents(price: Any) -> int | None:
    try:
        return int(Decimal(str(price)) * 100)
    except (InvalidOperation, ValueError, TypeError):
        return None


def _meta_lookup(meta_data: Any, keys: tuple[str, ...]) -> str | None:
    """First non-empty ``value`` for *keys* in a WooCommerce ``meta_data`` list."""
    if not isinstance(meta_data, list):
        return None
    wanted = set(keys)
    for entry in meta_data:
        if not isinstance(entry, dict):
            continue
        if entry.get("key") in wanted:
            value = entry.get("value")
            if isinstance(value, str) and value.strip():
                return value.strip()
            if value is not None and not isinstance(value, (dict, list)):
                text = str(value).strip()
                if text:
                    return text
    return None


class WooCommerceAdapter(StoreIntegration):
    """WooCommerce REST API backend. Never constructed without the
    ``WOOCOMMERCE_SHOP_URL`` + ``WOOCOMMERCE_CONSUMER_KEY`` +
    ``WOOCOMMERCE_CONSUMER_SECRET`` env vars (missing vars →
    ``IntegrationConfigError``; the factory falls back to demo)."""

    def __init__(self, settings: "Settings", store_id: str) -> None:
        super().__init__(store_id)
        shop_url = (settings.woocommerce_shop_url or "").strip()
        consumer_key = (settings.woocommerce_consumer_key or "").strip()
        consumer_secret = (settings.woocommerce_consumer_secret or "").strip()
        if not shop_url or not consumer_key or not consumer_secret:
            raise IntegrationConfigError(
                "WooCommerce adapter not configured: WOOCOMMERCE_SHOP_URL, "
                "WOOCOMMERCE_CONSUMER_KEY and WOOCOMMERCE_CONSUMER_SECRET are missing "
                "(server-side env vars only, never commit them). "
                "In WooCommerce, create REST API keys "
                "(WooCommerce → Settings → Advanced → REST API, "
                "Read permission), put URL + keys in the backend .env. "
                "See docs/WOOCOMMERCE-ADAPTER.md."
            )
        parsed = urllib.parse.urlparse(shop_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise IntegrationConfigError(
                f"WOOCOMMERCE_SHOP_URL must be an https URL, got: "
                f"{shop_url!r}. WooCommerce allows HTTP Basic Auth over "
                "HTTPS only (over HTTP the keys would end up in server logs)."
            )
        self.shop_url = f"https://{parsed.netloc}".rstrip("/")
        credentials = f"{consumer_key}:{consumer_secret}".encode("utf-8")
        # Secret stays in memory only; never logged or included in payloads.
        self._auth_header = (
            "Basic " + base64.b64encode(credentials).decode("ascii")
        )
        self._base_url = f"{self.shop_url}/wp-json/wc/v3"

    @property
    def backend_name(self) -> str:
        return "woocommerce"

    # -- low-level HTTP ---------------------------------------------------
    def _get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict | list | None:
        """GET a REST resource → parsed JSON (object or array), or None on 404.

        WooCommerce list endpoints (``/products``, ``/orders``) return bare
        JSON arrays; single-resource endpoints return objects.

        Raises IntegrationError on network failure, timeouts, 401
        (with credential guidance), and other non-retryable statuses. Up to
        MAX_RETRIES retries with backoff on timeouts, 429 and 5xx.
        """
        url = self._base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {"Authorization": self._auth_header}
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                status, payload = _http_get_json(
                    url, headers, REQUEST_TIMEOUT_SECONDS
                )
                if not isinstance(payload, (dict, list)):
                    raise IntegrationError(
                        "WooCommerce API returned a non-JSON payload for "
                        f"{path}"
                    )
                return payload
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return None
                if exc.code == 401:
                    raise IntegrationError(
                        "WooCommerce API denied the request (401). "
                        "Check Consumer Key / Consumer Secret "
                        "(WooCommerce → Settings → Advanced → REST API) "
                        "and make sure the key has "
                        "Read permission."
                    ) from exc
                if exc.code == 429 or 500 <= exc.code < 600:
                    last_error = exc
                    retry_after = (
                        exc.headers.get("Retry-After") if exc.headers else None
                    )
                    self._sleep_before_retry(attempt, retry_after)
                    continue
                raise IntegrationError(
                    f"WooCommerce API request failed: HTTP {exc.code} for {path}"
                ) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                # Network failure / timeout — retry with backoff.
                last_error = exc
                self._sleep_before_retry(attempt, None)
                continue
        raise IntegrationError(
            "WooCommerce API unreachable after "
            f"{MAX_RETRIES + 1} attempts: {type(last_error).__name__}"
        ) from last_error

    @staticmethod
    def _sleep_before_retry(attempt: int, retry_after: str | None) -> None:
        if attempt >= MAX_RETRIES:
            return
        delay = _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)]
        if retry_after:
            try:
                delay = min(max(float(retry_after), 0.0), 10.0)
            except (TypeError, ValueError):
                pass
        time.sleep(delay)

    # -- mapping ----------------------------------------------------------
    @staticmethod
    def _map_order(wc_order: dict[str, Any]) -> dict[str, Any]:
        """Map a WooCommerce order to the canonical order dict
        (status per WOOCOMMERCE_STATUS_MAP)."""
        raw_status = str(wc_order.get("status") or "").strip().lower()
        if raw_status in ("cancelled", "refunded", "failed", "trash"):
            status = "cancelled"
        else:
            status = WOOCOMMERCE_STATUS_MAP.get(raw_status, "processing")
        meta = wc_order.get("meta_data")
        return {
            # WooCommerce order numbers are numeric post IDs by default.
            "order_ref": str(wc_order.get("number") or wc_order.get("id") or ""),
            "status": status,
            "carrier": _meta_lookup(meta, _CARRIER_KEYS),
            "tracking_ref": _meta_lookup(meta, _TRACKING_NUMBER_KEYS),
            "eta_date": None,  # WooCommerce has no order ETA field
            "total_cents": _price_to_cents(wc_order.get("total")),
            "currency": wc_order.get("currency") or "EUR",
            "woocommerce_id": wc_order.get("id"),
        }

    @staticmethod
    def _map_product(wc_product: dict[str, Any]) -> dict[str, Any]:
        """Map a WooCommerce product to the canonical product dict."""
        attributes: dict[str, str] = {}
        for attr in wc_product.get("attributes") or []:
            if not isinstance(attr, dict):
                continue
            name = attr.get("name")
            options = attr.get("options") or []
            if name:
                attributes[str(name)] = ", ".join(str(o) for o in options)
        description = _strip_html(
            wc_product.get("description") or wc_product.get("short_description")
        )
        return {
            "id": wc_product.get("id"),
            "sku": wc_product.get("sku") or None,
            "name": wc_product.get("name"),
            "description": description,
            "attributes_json": json.dumps(attributes),
            "woocommerce_id": wc_product.get("id"),
        }

    # -- StoreIntegration -------------------------------------------------
    def get_product(self, sku_or_id: str) -> dict[str, Any] | None:
        key = (sku_or_id or "").strip()
        if not key:
            return None
        if key.isdigit():
            payload = self._get(f"/products/{key}")
            if payload is None:
                return None
            return self._map_product(payload)
        payload = self._get("/products", {"sku": key, "per_page": 1})
        products = (payload or []) if isinstance(payload, list) else []
        if not products:
            return None
        return self._map_product(products[0])

    def search_products(self, query: str) -> list[dict[str, Any]]:
        payload = self._get(
            "/products",
            {"search": query or "", "per_page": 10, "status": "publish"},
        )
        if not isinstance(payload, list):
            return []
        return [self._map_product(item) for item in payload]

    def get_order(self, order_ref: str | None) -> dict[str, Any] | None:
        # WooCommerce order numbers are numeric IDs by default
        # ("#12345" and "12345" both work); custom order-number plugins
        # are not supported — documented in docs/WOOCOMMERCE-ADAPTER.md.
        ref = (order_ref or "").strip().lstrip("#")
        if not ref or not ref.isdigit():
            return None
        payload = self._get(f"/orders/{ref}")
        if payload is None:
            return None
        return self._map_order(payload)

    def list_orders(self, limit: int = 50) -> list[dict[str, Any]]:
        payload = self._get(
            "/orders",
            {
                "per_page": max(1, min(int(limit), 100)),
                "orderby": "date",
                "order": "desc",
            },
        )
        if not isinstance(payload, list):
            return []
        return [self._map_order(item) for item in payload]

    def get_fulfillment(self, order_ref: str | None) -> dict[str, Any] | None:
        order = self.get_order(order_ref)
        if order is None:
            return None
        tracking_ref = order.get("tracking_ref")
        carrier = order.get("carrier")
        if not tracking_ref and not carrier:
            return None
        return {
            "order_ref": order.get("order_ref"),
            "carrier": carrier,
            "tracking_ref": tracking_ref,
            "eta_date": None,  # WooCommerce exposes no ETA field
            "woocommerce_id": order.get("woocommerce_id"),
        }
