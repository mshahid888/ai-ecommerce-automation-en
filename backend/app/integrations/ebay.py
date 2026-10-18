"""Real eBay Sell APIs adapter — fully implemented, tested offline.

``EbayAdapter`` implements ``StoreIntegration`` against eBay's Sell APIs
(Fulfillment API for orders, Inventory API for the merchant's own
products). Every method is implemented against the documented
request/response mapping. All HTTP traffic is mocked in tests — no live
eBay call has ever been made; none is made without a configured store
(``EBAY_CLIENT_ID`` + ``EBAY_CLIENT_SECRET``, or ``EBAY_OAUTH_TOKEN``).

Docs reference (eBay Developers Program):
- OAuth 2.0 (client credentials):
  ``POST https://api.ebay.com/identity/v1/oauth2/token`` with
  ``Content-Type: application/x-www-form-urlencoded``,
  HTTP Basic Auth ``base64(client_id:client_secret)`` and body
  ``grant_type=client_credentials&scope=<space-separated scopes>``
  → ``{"access_token": ..., "expires_in": 7200, ...}``.
  Scopes used: ``https://api.ebay.com/oauth/api_scope/sell.fulfillment``
  and ``https://api.ebay.com/oauth/api_scope/sell.inventory``.
- Fulfillment API: ``GET https://api.ebay.com/sell/fulfillment/v1/order``
  (``{"orders": [...]}``) and
  ``GET .../sell/fulfillment/v1/order/{orderId}`` → ``orderId``
  (e.g. "12-34567-89012"), ``orderFulfillmentStatus``
  (``NOT_STARTED`` | ``IN_PROGRESS`` | ``FULFILLED``),
  ``orderPaymentStatus``, ``pricingSummary.total`` (``{value,
  currency}``), ``lineItems``. Header ``X-EBAY-C-MARKETPLACE-ID``
  (default ``EBAY_DE``).
- Inventory API (merchant's own catalog):
  ``GET https://api.ebay.com/sell/inventory/v1/inventory_item`` →
  ``{"inventoryItems": [{"sku": ..., "product": {"title": ...,
  "description": ...}, ...}]}``; single SKU:
  ``GET .../inventory_item/{sku}``.

Construction rules (hard):
- Only from server-side env vars ``EBAY_CLIENT_ID`` +
  ``EBAY_CLIENT_SECRET`` (client-credentials flow, token fetched lazily
  and cached in memory) or a ready ``EBAY_OAUTH_TOKEN`` — never from the
  frontend, never from a committed file. Missing vars →
  ``IntegrationConfigError`` with setup instructions; no network call is
  ever attempted unconfigured.
- Secrets/tokens live only in memory / server env (``chmod 600`` env
  file on the server). They are never logged, never returned in payloads.

Reliability: 10 s timeout per request, up to 2 retries with exponential
backoff (0.5 s, 1 s). HTTP 429 honors ``Retry-After`` (capped at 10 s).
HTTP 401 raises with explicit credential guidance; other failures raise
``IntegrationError`` without leaking secrets.

Status mapping (documented honestly — the Fulfillment API exposes no
delivered/delayed signal and no cancellation state)::

    orderFulfillmentStatus = FULFILLED   → shipped
    orderFulfillmentStatus = IN_PROGRESS → processing
    orderFulfillmentStatus = NOT_STARTED → processing
    missing / unknown                   → processing (safe default)

``delivered`` and ``delayed`` are NEVER invented. ``eta_date`` is always
None (no delivery estimate in the fulfillment payload); carrier and
tracking_ref come from the first line item's ``shipTo``/tracking info
when present, else None — the pipeline says so honestly.

Mapping caveats (to verify against a real seller account):
- Cancellations live in a separate eBay flow (sell fulfillment
  cancellation API); a cancelled order does not surface as a distinct
  ``orderFulfillmentStatus`` — this adapter never reports "cancelled".
- eBay order IDs look like "12-34567-89012"; ``get_order`` accepts the
  ID as-is (a leading "#" is stripped, nothing else is reformatted).

Messaging reality: eBay does NOT allow embedding a third-party chat
widget on ebay.de item pages, and buyer↔seller messages go through
eBay's messaging system under eBay's policies. This adapter therefore
covers backend data sync only (orders/products for the pipeline +
dashboard). Reading or sending eBay messages is deliberately OUT OF
SCOPE — automating member messages outside eBay's rules risks the
seller's account.

EXPLICIT RULE: never point this at a real seller account without
Shahid's written go-ahead. Sandbox first (see docs/EBAY-ADAPTER.md).
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

#: eBay OAuth token endpoint.
TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
#: eBay Sell APIs base URL.
API_BASE_URL = "https://api.ebay.com"
#: Scopes requested in the client-credentials flow.
OAUTH_SCOPES = (
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment "
    "https://api.ebay.com/oauth/api_scope/sell.inventory"
)
#: Default marketplace (Germany).
DEFAULT_MARKETPLACE_ID = "EBAY_DE"
#: Timeout per eBay HTTP request (seconds).
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


def _http_post_form(url: str, headers: dict[str, str], body: str, timeout: int):
    """POST urlencoded form → parsed JSON. Module-level for monkeypatching
    in tests (used only for the OAuth token endpoint)."""
    data = body.encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def _strip_html(text: str | None) -> str:
    return _TAG_RE.sub("", text or "").strip()


def _price_to_cents(value: Any) -> int | None:
    try:
        return int(Decimal(str(value)) * 100)
    except (InvalidOperation, ValueError, TypeError):
        return None


class EbayAdapter(StoreIntegration):
    """eBay Sell APIs backend. Never constructed without ``EBAY_CLIENT_ID``
    + ``EBAY_CLIENT_SECRET`` (or a ready ``EBAY_OAUTH_TOKEN``); missing
    vars → ``IntegrationConfigError``; the factory falls back to demo."""

    def __init__(self, settings: "Settings", store_id: str) -> None:
        super().__init__(store_id)
        client_id = (settings.ebay_client_id or "").strip()
        client_secret = (settings.ebay_client_secret or "").strip()
        direct_token = (settings.ebay_oauth_token or "").strip() or None
        if not direct_token and not (client_id and client_secret):
            raise IntegrationConfigError(
                "eBay adapter not configured: EBAY_CLIENT_ID and "
                "EBAY_CLIENT_SECRET are missing (or alternatively EBAY_OAUTH_TOKEN "
                "for a ready-made token). Server-side env vars only, "
                "never commit them. Create credentials in the eBay Developers Program "
                "(https://developer.ebay.com), select scopes "
                "sell.fulfillment + sell.inventory, put the values in the "
                "backend .env. See docs/EBAY-ADAPTER.md."
            )
        self._client_id = client_id
        self._client_secret = client_secret
        # Token cache (client-credentials flow); direct tokens are used
        # as-is. Secrets/tokens stay in memory only, never logged.
        self._token = direct_token
        self._token_expires_at = 0.0 if direct_token else 0.0
        self._marketplace_id = (
            settings.ebay_marketplace_id or DEFAULT_MARKETPLACE_ID
        ).strip() or DEFAULT_MARKETPLACE_ID

    @property
    def backend_name(self) -> str:
        return "ebay"

    # -- auth -------------------------------------------------------------
    def _get_token(self) -> str:
        """Return a valid OAuth token (cached, refreshed when expiring)."""
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token
        if self._token and self._token_expires_at == 0.0:
            # Direct EBAY_OAUTH_TOKEN: no expiry known, use as-is.
            return self._token
        credentials = f"{self._client_id}:{self._client_secret}".encode()
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": (
                "Basic " + base64.b64encode(credentials).decode("ascii")
            ),
        }
        body = "grant_type=client_credentials&scope=" + urllib.parse.quote(
            OAUTH_SCOPES, safe=""
        )
        try:
            status, payload = _http_post_form(
                TOKEN_URL, headers, body, REQUEST_TIMEOUT_SECONDS
            )
        except Exception as exc:
            raise IntegrationError(
                "Could not fetch the eBay OAuth token "
                f"({type(exc).__name__}). Check credentials and network."
            ) from exc
        token = (payload or {}).get("access_token")
        if not token:
            raise IntegrationError(
                "eBay OAuth response contained no access_token. "
                "Check client ID/secret and app permissions."
            )
        try:
            expires_in = int((payload or {}).get("expires_in") or 7200)
        except (TypeError, ValueError):
            expires_in = 7200
        self._token = token
        self._token_expires_at = time.time() + expires_in
        return token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "X-EBAY-C-MARKETPLACE-ID": self._marketplace_id,
            "Content-Type": "application/json",
        }

    # -- low-level HTTP ---------------------------------------------------
    def _get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict | None:
        """GET a Sell API resource → parsed JSON, or None on 404.

        Raises IntegrationError on network failure, timeouts, 401
        (with credential guidance), and other non-retryable statuses.
        Up to MAX_RETRIES retries with backoff on timeouts, 429 and 5xx.
        """
        url = API_BASE_URL + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                status, payload = _http_get_json(
                    url, self._headers(), REQUEST_TIMEOUT_SECONDS
                )
                if not isinstance(payload, dict):
                    raise IntegrationError(
                        f"eBay API returned a non-object payload for {path}"
                    )
                return payload
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return None
                if exc.code == 401:
                    raise IntegrationError(
                        "eBay API denied the request (401). EBAY_CLIENT_ID / "
                        "EBAY_CLIENT_SECRET (or refresh EBAY_OAUTH_TOKEN "
                        "); the app needs scopes sell.fulfillment "
                        "+ sell.inventory."
                    ) from exc
                if exc.code == 429 or 500 <= exc.code < 600:
                    last_error = exc
                    retry_after = (
                        exc.headers.get("Retry-After") if exc.headers else None
                    )
                    self._sleep_before_retry(attempt, retry_after)
                    continue
                raise IntegrationError(
                    f"eBay API request failed: HTTP {exc.code} for {path}"
                ) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                # Network failure / timeout — retry with backoff.
                last_error = exc
                self._sleep_before_retry(attempt, None)
                continue
        raise IntegrationError(
            "eBay API unreachable after "
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
    #: eBay ``orderFulfillmentStatus`` → canonical status. The Fulfillment
    #: API exposes no delivered/delayed signal and no cancellation state,
    #: so this adapter never emits them — it answers honestly instead of
    #: guessing (``eta_date`` is always None).
    #:
    #: | eBay signal              | canonical status |
    #: |--------------------------|------------------|
    #: | FULFILLED                | shipped          |
    #: | IN_PROGRESS              | processing       |
    #: | NOT_STARTED                | processing       |
    #: | missing / unknown        | processing       |
    EBAY_FULFILLMENT_STATUS_MAP = {
        "FULFILLED": "shipped",
        "IN_PROGRESS": "processing",
        "NOT_STARTED": "processing",
    }

    @staticmethod
    def _map_order(ebay_order: dict[str, Any]) -> dict[str, Any]:
        """Map an eBay fulfillment order to the canonical order dict."""
        fulfillment = str(ebay_order.get("orderFulfillmentStatus") or "")
        status = EbayAdapter.EBAY_FULFILLMENT_STATUS_MAP.get(
            fulfillment, "processing"
        )
        pricing = ebay_order.get("pricingSummary") or {}
        total = pricing.get("total") or {}
        # Tracking: first line item carrying tracking info wins; most
        # orders have none — then the pipeline says so honestly.
        carrier = tracking_ref = None
        for item in ebay_order.get("lineItems") or []:
            fulfillment_details = item.get("lineItemFulfillmentDetails") or {}
            shipments = fulfillment_details.get("shipments") or []
            for shipment in shipments:
                tracking_ref = (
                    shipment.get("trackingNumber") or tracking_ref
                )
                carrier = (
                    shipment.get("shippingCarrierCode") or carrier
                )
                if tracking_ref or carrier:
                    break
            if tracking_ref or carrier:
                break
        return {
            "order_ref": str(ebay_order.get("orderId") or ""),
            "status": status,
            "carrier": carrier,
            "tracking_ref": tracking_ref,
            "eta_date": None,  # no delivery estimate in fulfillment payload
            "total_cents": _price_to_cents(total.get("value")),
            "currency": total.get("currency") or "EUR",
            "ebay_order_id": ebay_order.get("orderId"),
        }

    @staticmethod
    def _map_inventory_item(item: dict[str, Any]) -> dict[str, Any]:
        product = item.get("product") or {}
        return {
            "id": item.get("sku"),
            "sku": item.get("sku"),
            "name": product.get("title"),
            "description": _strip_html(product.get("description")),
            # eBay inventory items carry no demo-style attribute flags:
            # {} keeps the pipeline honest instead of inventing data.
            "attributes_json": json.dumps({}),
            "ebay_sku": item.get("sku"),
        }

    # -- StoreIntegration -------------------------------------------------
    def get_product(self, sku_or_id: str) -> dict[str, Any] | None:
        key = (sku_or_id or "").strip()
        if not key:
            return None
        # Merchant's own catalog: direct SKU lookup first.
        payload = self._get(
            f"/sell/inventory/v1/inventory_item/{urllib.parse.quote(key, safe='')}"
        )
        if payload is not None and payload.get("sku"):
            return self._map_inventory_item(payload)
        # Fallback: scan the merchant's inventory list for a title match.
        for product in self.search_products(key):
            if (product.get("name") or "").strip().lower() == key.lower():
                return product
        return None

    def search_products(self, query: str) -> list[dict[str, Any]]:
        payload = self._get(
            "/sell/inventory/v1/inventory_item",
            {"limit": 10},
        )
        if payload is None:
            return []
        items = payload.get("inventoryItems") or []
        q = (query or "").strip().lower()
        scored = []
        for item in items:
            product = self._map_inventory_item(item)
            name = (product.get("name") or "").lower()
            sku = (product.get("sku") or "").lower()
            if q and (q in name or q in sku):
                scored.append(product)
        return scored[:10]

    def get_order(self, order_ref: str | None) -> dict[str, Any] | None:
        ref = (order_ref or "").strip().lstrip("#")
        if not ref:
            return None
        # eBay order IDs look like "12-34567-89012" — passed through as-is.
        payload = self._get(
            f"/sell/fulfillment/v1/order/{urllib.parse.quote(ref, safe='')}"
        )
        if payload is None:
            return None
        return self._map_order(payload)

    def list_orders(self, limit: int = 50) -> list[dict[str, Any]]:
        payload = self._get(
            "/sell/fulfillment/v1/order",
            {"limit": max(1, min(int(limit), 200))},
        )
        if payload is None:
            return []
        return [
            self._map_order(item) for item in payload.get("orders") or []
        ]

    def get_fulfillment(self, order_ref: str | None) -> dict[str, Any] | None:
        order = self.get_order(order_ref)
        if order is None:
            return None
        if not order.get("tracking_ref") and not order.get("carrier"):
            return None
        return {
            "order_ref": order.get("order_ref"),
            "carrier": order.get("carrier"),
            "tracking_ref": order.get("tracking_ref"),
            "eta_date": None,
            "ebay_order_id": order.get("ebay_order_id"),
        }
