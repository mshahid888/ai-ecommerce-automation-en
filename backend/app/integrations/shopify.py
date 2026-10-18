"""Real Shopify Admin API adapter (Phase 9b) — fully implemented, tested offline.

``ShopifyAdapter`` implements ``StoreIntegration`` against the Shopify
Admin API (REST for orders/products, GraphQL noted below). Every method is implemented against the documented request/response
mapping. All HTTP traffic is mocked in tests — no live Shopify call has
ever been made; none is made without a configured store
(SHOPIFY_SHOP_DOMAIN + SHOPIFY_ADMIN_TOKEN).

Docs reference (checked 2026-10-09):
- API versioning: https://shopify.dev/docs/api/usage/versioning
  → latest stable version is ``2026-10`` (used as the default below).
- REST Admin API: https://{shop}.myshopify.com/admin/api/{version}/
  with header ``X-Shopify-Access-Token: <admin API access token>``.
- Order lookup by merchant name:
  ``GET /admin/api/2026-10/orders.json?name=%2310482&status=any``
  (``name`` is the merchant-facing order name, e.g. "#10482").
- Products: ``GET /admin/api/2026-10/products.json``;
  single product: ``GET /admin/api/2026-10/products/{id}.json``.
- Fulfillments: ``GET /admin/api/2026-10/orders/{order_id}/fulfillments.json``
  → ``tracking_number``, ``tracking_company`` (tracking URL via
  ``tracking_urls``).
- Webhook verification: HMAC-SHA256 of the raw request body with the
  app's client secret, base64-encoded, compared (constant-time) with the
  ``X-Shopify-Hmac-Sha256`` header — see ``webhooks.py``.

Construction rules (hard):
- Only from server-side env vars ``SHOPIFY_SHOP_DOMAIN`` +
  ``SHOPIFY_ADMIN_API_TOKEN`` — never from the frontend, never from a
  committed file. Missing vars → ``IntegrationConfigError`` with setup
  instructions; no network call is ever attempted unconfigured.
- Tokens live only in memory / server env (``chmod 600`` env file on
  the server). They are never logged, never returned in payloads.

Reliability: 10 s timeout per request, up to 2 retries with exponential
backoff (0.5 s, 1 s). HTTP 429 honors ``Retry-After`` (capped at 10 s).
HTTP 403 raises with explicit scope guidance; other failures raise
``IntegrationError`` without leaking the token.

Minimal access scopes (custom app in the Shopify admin):
``read_orders``, ``read_products``, ``read_fulfillments`` (fulfillment
reads for tracking). Webhook topics: ``orders/create``,
``orders/updated``.

GraphQL note (Admin API ``/admin/api/2026-10/graphql.json``, same
access-token header) — preferred for SKU lookups, e.g.::

    { products(first: 1, query: "sku:MS-AP750") {
        edges { node { id title bodyHtml variants(first: 1) {
          edges { node { sku price } } } } } } }

Mapping caveats (to verify against a real dev store in Phase 9b):
- Shopify orders have no fixed "shipped vs delivered" status: we map
  ``fulfillment_status=fulfilled`` → canonical "shipped". The
  refinement (tracking-confirmed delivery) is documented but
  untested until a dev store exists.
- Shopify has no order ETA field: ``eta_date`` is always None from
  this adapter — the pipeline omits the delivery estimate honestly.
- Product attributes (dishwasher-safe, etc.) have no Shopify
  equivalent in the basic product payload: ``attributes_json`` is {}
  and the pipeline answers honestly ("information not available").

EXPLICIT RULE: never point this at a real store without Shahid's
written go-ahead. Dev store first (see docs/SHOPIFY.md).
"""

from __future__ import annotations

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

# Shopify docs (checked 2026-10-09): 2026-10 is the latest stable version.
DEFAULT_API_VERSION = "2026-10"
#: Minimal scopes for this adapter (documented; enforced by Shopify).
REQUIRED_SCOPES = ("read_orders", "read_products", "read_fulfillments")
#: Timeout per Shopify HTTP request (seconds).
REQUEST_TIMEOUT_SECONDS = 10
#: Number of retries after the initial attempt (2 retries = 3 attempts max).
MAX_RETRIES = 2
_BACKOFF_SECONDS = (0.5, 1.0)

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


class ShopifyAdapter(StoreIntegration):
    """Shopify Admin API backend. Never constructed without the
    ``SHOPIFY_SHOP_DOMAIN`` + ``SHOPIFY_ADMIN_TOKEN`` env vars (missing
    vars → ``IntegrationConfigError``; the factory falls back to demo)."""

    def __init__(self, settings: "Settings", store_id: str) -> None:
        super().__init__(store_id)
        shop_domain = (settings.shopify_shop_domain or "").strip()
        token = settings.shopify_admin_api_token or ""
        if not shop_domain or not token:
            raise IntegrationConfigError(
                "Shopify adapter not configured: SHOPIFY_SHOP_DOMAIN "
                "and SHOPIFY_ADMIN_TOKEN are missing (server-side env vars, "
                "never commit them). The older variable "
                "SHOPIFY_ADMIN_API_TOKEN is also accepted. "
                "Create a free Shopify Partner dev store + custom app "
                "(Scopes: read_orders, read_products, read_fulfillments), "
                "put the Admin API token in the backend .env. "
                "See docs/SHOPIFY.md and docs/SHOPIFY-ADAPTER.md."
            )
        shop_domain = re.sub(r"^https?://", "", shop_domain).rstrip("/").lower()
        if "myshopify.com" not in shop_domain:
            raise IntegrationConfigError(
                f"SHOPIFY_SHOP_DOMAIN looks wrong: {shop_domain!r}. "
                "Use the myshopify.com domain of the store, e.g. "
                "'muster-shop.myshopify.com'."
            )
        self.shop_domain = shop_domain
        # Token stays in memory only; never logged or included in payloads.
        self._token = token
        self._api_version = (
            settings.shopify_api_version or DEFAULT_API_VERSION
        ).strip() or DEFAULT_API_VERSION
        self._base_url = (
            f"https://{self.shop_domain}/admin/api/{self._api_version}"
        )

    @property
    def backend_name(self) -> str:
        return "shopify"

    # -- low-level HTTP ---------------------------------------------------
    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict | None:
        """GET a REST resource → parsed JSON, or None on 404.

        Raises IntegrationError on network failure, timeouts, 403
        (with scope guidance), and other non-retryable statuses. Up to
        MAX_RETRIES retries with backoff on timeouts, 429 and 5xx.
        """
        url = self._base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {"X-Shopify-Access-Token": self._token}
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                status, payload = _http_get_json(
                    url, headers, REQUEST_TIMEOUT_SECONDS
                )
                if not isinstance(payload, dict):
                    raise IntegrationError(
                        f"Shopify API returned a non-object payload for {path}"
                    )
                return payload
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return None
                if exc.code == 403:
                    raise IntegrationError(
                        "Shopify API denied the request (403). Check the "
                        "custom app's access scopes "
                        f"({', '.join(REQUIRED_SCOPES)}) and that the app "
                        "is installed on the store."
                    ) from exc
                if exc.code == 429 or 500 <= exc.code < 600:
                    last_error = exc
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    self._sleep_before_retry(attempt, retry_after)
                    continue
                raise IntegrationError(
                    f"Shopify API request failed: HTTP {exc.code} for {path}"
                ) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                # Network failure / timeout — retry with backoff.
                last_error = exc
                self._sleep_before_retry(attempt, None)
                continue
        raise IntegrationError(
            "Shopify API unreachable after "
            f"{MAX_RETRIES + 1} attempts: {type(last_error).__name__}"
        ) from last_error

    @staticmethod
    def _sleep_before_retry(attempt: int, retry_after: str | None) -> None:
        if attempt >= MAX_RETRIES:
            return
        delay = _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)]
        if retry_after:
            try:
                delay = min(float(retry_after), 10.0)
            except ValueError:
                pass
        time.sleep(delay)

    # -- mapping helpers --------------------------------------------------
    #: Shopify ``fulfillment_status`` → canonical status. Shopify REST
    #: exposes no reliable "delivered" signal and no "delayed" concept, so
    #: this adapter never emits "delivered"/"delayed" — it answers honestly
    #: instead of guessing (``eta_date`` is always None).
    #:
    #: | Shopify signal                  | canonical status |
    #: |---------------------------------|------------------|
    #: | order cancelled = true          | cancelled        |
    #: | fulfillment_status = fulfilled  | shipped          |
    #: | fulfillment_status = partial    | processing       |
    #: | fulfillment_status = unfulfilled| processing       |
    #: | fulfillment_status = restocked  | processing       |
    #: | fulfillment_status = null/other | processing       |
    SHOPIFY_FULFILLMENT_STATUS_MAP = {
        "fulfilled": "shipped",
        "partial": "processing",
        "unfulfilled": "processing",
        "restocked": "processing",
    }

    @staticmethod
    def _map_order(shopify_order: dict[str, Any]) -> dict[str, Any]:
        """Map a Shopify REST order to the canonical order dict
        (status per SHOPIFY_FULFILLMENT_STATUS_MAP)."""
        name = str(shopify_order.get("name") or "").lstrip("#")
        if shopify_order.get("cancelled"):
            status = "cancelled"
        else:
            status = ShopifyAdapter.SHOPIFY_FULFILLMENT_STATUS_MAP.get(
                shopify_order.get("fulfillment_status"), "processing"
            )
        return {
            "order_ref": name,
            "status": status,
            "carrier": None,  # via get_fulfillment (tracking_company)
            "tracking_ref": None,  # via get_fulfillment (tracking_number)
            "eta_date": None,  # Shopify has no order ETA field
            "total_cents": _price_to_cents(shopify_order.get("total_price")),
            "currency": shopify_order.get("currency") or "EUR",
            "shopify_id": (
                f"gid://shopify/Order/{shopify_order.get('id')}"
            ),
            "shopify_name": shopify_order.get("name"),
        }

    @staticmethod
    def _map_product(shopify_product: dict[str, Any]) -> dict[str, Any]:
        variants = shopify_product.get("variants") or []
        sku = variants[0].get("sku") if variants else None
        return {
            "id": shopify_product.get("id"),
            "sku": sku,
            "name": shopify_product.get("title"),
            "description": _strip_html(shopify_product.get("body_html")),
            # No direct Shopify equivalent of the demo attributes
            # (spuelmaschinengeeignet, ...): {} keeps the pipeline honest.
            "attributes_json": json.dumps({}),
            "shopify_id": (
                f"gid://shopify/Product/{shopify_product.get('id')}"
            ),
        }

    # -- StoreIntegration -------------------------------------------------
    def get_product(self, sku_or_id: str) -> dict[str, Any] | None:
        key = (sku_or_id or "").strip()
        if not key:
            return None
        if key.isdigit():
            payload = self._get(f"/products/{key}.json")
            if payload is None:
                return None
            return self._map_product(payload.get("product") or {})
        # REST has no direct SKU lookup; GraphQL is the documented route
        # (see module docstring). REST fallback: title search.
        matches = self.search_products(key)
        for product in matches:
            if (product.get("sku") or "").lower() == key.lower():
                return product
        return None

    def search_products(self, query: str) -> list[dict[str, Any]]:
        payload = self._get(
            "/products.json",
            {"title": query or "", "limit": 10, "status": "active"},
        )
        if payload is None:
            return []
        return [
            self._map_product(item)
            for item in payload.get("products") or []
        ]

    def get_order(self, order_ref: str | None) -> dict[str, Any] | None:
        ref = (order_ref or "").strip().lstrip("#")
        if not ref:
            return None
        payload = self._get(
            "/orders.json",
            {"name": f"#{ref}", "status": "any", "limit": 1},
        )
        orders = (payload or {}).get("orders") or []
        if not orders:
            return None
        return self._map_order(orders[0])

    def list_orders(self, limit: int = 50) -> list[dict[str, Any]]:
        payload = self._get(
            "/orders.json",
            {"status": "any", "limit": max(1, min(int(limit), 250))},
        )
        return [
            self._map_order(item) for item in (payload or {}).get("orders") or []
        ]

    def get_fulfillment(self, order_ref: str | None) -> dict[str, Any] | None:
        order = self.get_order(order_ref)
        if order is None:
            return None
        shopify_id = str(order.get("shopify_id") or "")
        numeric_id = shopify_id.rsplit("/", 1)[-1]
        if not numeric_id.isdigit():
            return None
        payload = self._get(f"/orders/{numeric_id}/fulfillments.json")
        fulfillments = (payload or {}).get("fulfillments") or []
        for fulfillment in fulfillments:
            tracking_number = fulfillment.get("tracking_number")
            tracking_company = fulfillment.get("tracking_company")
            if tracking_number or tracking_company:
                return {
                    "order_ref": order.get("order_ref"),
                    "carrier": tracking_company,
                    "tracking_ref": tracking_number,
                    "eta_date": None,  # Shopify exposes no ETA field
                    "shopify_id": (
                        f"gid://shopify/Fulfillment/{fulfillment.get('id')}"
                    ),
                }
        return None
