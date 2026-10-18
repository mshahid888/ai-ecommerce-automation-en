"""Real Etsy Open API v3 adapter — fully implemented, tested offline.

``EtsyAdapter`` implements ``StoreIntegration`` against the Etsy Open API
(v3). Every method is implemented against the documented request/response
mapping. All HTTP traffic is mocked in tests — no live Etsy call has ever
been made; none is made without a configured store
(``ETSY_API_KEY`` + ``ETSY_SHOP_ID``).

Docs reference (Etsy Open API v3):
- Base URL: ``https://openapi.etsy.com/v3/application``
- Auth: ``x-api-key: <api key>`` header on every request; shop-scoped
  reads (receipts) additionally require OAuth 2.0:
  ``Authorization: Bearer <oauth token>``.
- Shop receipts: ``GET /v3/application/shops/{shop_id}/receipts``
  → ``{"count": N, "results": [...]}`` (supports ``was_paid``,
  ``was_shipped``, ``limit`` filters); single receipt:
  ``GET /v3/application/shops/{shop_id}/receipts/{receipt_id}``
  → receipt object with ``receipt_id``, ``name``, ``total_price``
  (``{amount, divisor, currency_code}``), ``was_paid``, ``was_shipped``,
  ``create_timestamp``.
- Active listings: ``GET /v3/application/listings/active`` with
  ``keywords`` + ``shop_id`` filters → ``{"count", "results"}``; single:
  ``GET /v3/application/listings/{listing_id}`` → ``listing_id``,
  ``title``, ``description``, ``price`` (Money object).

Construction rules (hard):
- Only from server-side env vars ``ETSY_API_KEY`` + ``ETSY_SHOP_ID``
  (+ optional ``ETSY_OAUTH_TOKEN`` for shop-scoped receipt reads) —
  never from the frontend, never from a committed file. Missing vars →
  ``IntegrationConfigError`` with setup instructions; no network call is
  ever attempted unconfigured.
- Keys/tokens live only in memory / server env (``chmod 600`` env file
  on the server). They are never logged, never returned in payloads.

Reliability: 10 s timeout per request, up to 2 retries with exponential
backoff (0.5 s, 1 s). HTTP 429 honors ``Retry-After`` (capped at 10 s).
HTTP 401/403 raises with explicit guidance (usually: OAuth token
missing or insufficient scopes); other failures raise
``IntegrationError`` without leaking credentials.

Status mapping (documented honestly — Etsy receipts expose only
``was_paid`` / ``was_shipped``; there is no delivered/delayed/
cancelled signal on the receipt object)::

    was_shipped = true                  → shipped
    was_shipped = false, was_paid = true → processing
    was_paid = false                    → processing (awaiting payment)
    flags missing / unknown             → processing (safe default)

``delivered`` and ``delayed`` are NEVER invented. ``eta_date`` is always
None (Etsy exposes no delivery estimate on receipts); carrier and
tracking_ref are None (no tracking fields on the receipt object) — the
pipeline says so honestly.

Mapping caveats (to verify against a real shop):
- The merchant-facing order reference on Etsy IS the numeric
  ``receipt_id`` (customers see e.g. "Order #1234567890"). ``get_order``
  accepts "1234567890" or "#1234567890" and looks the receipt up
  directly; non-numeric refs return None (honest, no guessing).
- SKU is not a first-class field on Etsy listings (it lives in the
  inventory record); ``sku`` stays None and ``attributes_json`` is {} —
  the pipeline answers honestly instead of inventing data.
- Receipt reads require OAuth: without ``ETSY_OAUTH_TOKEN`` the API
  answers 401/403 and the adapter raises ``IntegrationError`` with
  guidance instead of silently returning empty results.

Messaging reality: Etsy does NOT allow embedding a third-party chat
widget on etsy.com pages, and seller↔buyer messages live in Etsy's
"Convos" system under Etsy's policies. This adapter therefore covers
backend data sync only (orders/products for the pipeline + dashboard).
Reading or sending Convos messages is deliberately OUT OF SCOPE —
automating buyer messages outside Etsy's rules risks the seller's shop.

EXPLICIT RULE: never point this at a real shop without Shahid's
written go-ahead. Test shop first (see docs/ETSY-ADAPTER.md).
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, TYPE_CHECKING

from .base import IntegrationConfigError, IntegrationError, StoreIntegration

if TYPE_CHECKING:  # pragma: no cover
    from ..config import Settings

#: Etsy Open API v3 base URL.
BASE_URL = "https://openapi.etsy.com/v3/application"
#: Timeout per Etsy HTTP request (seconds).
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


def _money_to_cents(money: Any) -> int | None:
    """Etsy Money object {amount, divisor, currency_code} → cents."""
    if not isinstance(money, dict):
        return None
    try:
        amount = int(money.get("amount"))
        divisor = int(money.get("divisor") or 1)
        if divisor <= 0:
            return None
        return round(amount * 100 / divisor)
    except (TypeError, ValueError):
        return None


class EtsyAdapter(StoreIntegration):
    """Etsy Open API v3 backend. Never constructed without the
    ``ETSY_API_KEY`` + ``ETSY_SHOP_ID`` env vars (missing vars →
    ``IntegrationConfigError``; the factory falls back to demo)."""

    def __init__(self, settings: "Settings", store_id: str) -> None:
        super().__init__(store_id)
        api_key = (settings.etsy_api_key or "").strip()
        shop_id = (settings.etsy_shop_id or "").strip()
        if not api_key or not shop_id:
            raise IntegrationConfigError(
                "Etsy adapter not configured: ETSY_API_KEY and "
                "ETSY_SHOP_ID are missing (server-side env vars, "
                "never commit them). Order data (receipts) additionally requires "
                "ETSY_OAUTH_TOKEN (OAuth 2.0). "
                "Create an API key in the Etsy developer portal "
                "(https://www.etsy.com/developers), take the shop ID from the "
                "Shop Manager, put tokens in the backend .env. "
                "See docs/ETSY-ADAPTER.md."
            )
        self._api_key = api_key
        self._shop_id = shop_id
        # OAuth token is optional at construction time (needed for
        # shop-scoped receipt reads); stored in memory only, never logged.
        self._oauth_token = (settings.etsy_oauth_token or "").strip() or None

    @property
    def backend_name(self) -> str:
        return "etsy"

    # -- low-level HTTP ---------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {"x-api-key": self._api_key}
        if self._oauth_token:
            headers["Authorization"] = f"Bearer {self._oauth_token}"
        return headers

    def _get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict | None:
        """GET a v3 resource → parsed JSON, or None on 404.

        Raises IntegrationError on network failure, timeouts, 401/403
        (with guidance), and other non-retryable statuses. Up to
        MAX_RETRIES retries with backoff on timeouts, 429 and 5xx.
        """
        url = BASE_URL + path
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
                        f"Etsy API returned a non-object payload for {path}"
                    )
                return payload
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return None
                if exc.code in (401, 403):
                    raise IntegrationError(
                        "Etsy API denied the request "
                        f"(HTTP {exc.code}). Shop data (receipts) requires "
                        "an OAuth token: set ETSY_OAUTH_TOKEN and "
                        "make sure the app has the required scopes. "
                        "See docs/ETSY-ADAPTER.md."
                    ) from exc
                if exc.code == 429 or 500 <= exc.code < 600:
                    last_error = exc
                    retry_after = (
                        exc.headers.get("Retry-After") if exc.headers else None
                    )
                    self._sleep_before_retry(attempt, retry_after)
                    continue
                raise IntegrationError(
                    f"Etsy API request failed: HTTP {exc.code} for {path}"
                ) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                # Network failure / timeout — retry with backoff.
                last_error = exc
                self._sleep_before_retry(attempt, None)
                continue
        raise IntegrationError(
            "Etsy API unreachable after "
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
    #: Etsy receipt flags → canonical status. Etsy exposes no
    #: delivered/delayed/cancelled signal on receipts, so this adapter
    #: never emits them — it answers honestly instead of guessing.
    #:
    #: | Etsy signal                              | canonical status |
    #: |------------------------------------------|------------------|
    #: | was_shipped = true                       | shipped          |
    #: | was_shipped = false, was_paid = true     | processing       |
    #: | was_paid = false                         | processing       |
    #: | flags missing / unknown                  | processing       |
    @staticmethod
    def _map_status(receipt: dict[str, Any]) -> str:
        if receipt.get("was_shipped") is True:
            return "shipped"
        # was_paid True/False/None → still processing (or awaiting payment).
        return "processing"

    @staticmethod
    def _map_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
        """Map an Etsy receipt to the canonical order dict."""
        money = receipt.get("total_price") or {}
        return {
            "order_ref": str(receipt.get("receipt_id") or ""),
            "status": EtsyAdapter._map_status(receipt),
            "carrier": None,  # no tracking fields on Etsy receipts
            "tracking_ref": None,
            "eta_date": None,  # Etsy exposes no delivery estimate
            "total_cents": _money_to_cents(money),
            "currency": money.get("currency_code") or "EUR",
            "etsy_receipt_id": receipt.get("receipt_id"),
        }

    @staticmethod
    def _map_listing(listing: dict[str, Any]) -> dict[str, Any]:
        money = listing.get("price") or {}
        return {
            "id": listing.get("listing_id"),
            # SKU lives in the inventory record, not on the listing:
            # None keeps the pipeline honest instead of guessing.
            "sku": None,
            "name": listing.get("title"),
            "description": _strip_html(listing.get("description")),
            "attributes_json": json.dumps({}),
            "etsy_listing_id": listing.get("listing_id"),
        }

    # -- StoreIntegration -------------------------------------------------
    def get_product(self, sku_or_id: str) -> dict[str, Any] | None:
        key = (sku_or_id or "").strip()
        if not key:
            return None
        if key.isdigit():
            payload = self._get(f"/listings/{key}")
            if payload is None:
                return None
            return self._map_listing(payload)
        # No numeric id: keyword search, exact title match wins.
        for product in self.search_products(key):
            if (product.get("name") or "").strip().lower() == key.lower():
                return product
        return None

    def search_products(self, query: str) -> list[dict[str, Any]]:
        payload = self._get(
            "/listings/active",
            {
                "keywords": query or "",
                "shop_id": self._shop_id,
                "limit": 10,
            },
        )
        if payload is None:
            return []
        return [
            self._map_listing(item) for item in payload.get("results") or []
        ]

    def get_order(self, order_ref: str | None) -> dict[str, Any] | None:
        ref = (order_ref or "").strip().lstrip("#")
        # Etsy merchant references are numeric receipt IDs — never guess
        # on non-numeric input.
        if not ref or not ref.isdigit():
            return None
        payload = self._get(f"/shops/{self._shop_id}/receipts/{ref}")
        if payload is None:
            return None
        return self._map_receipt(payload)

    def list_orders(self, limit: int = 50) -> list[dict[str, Any]]:
        payload = self._get(
            f"/shops/{self._shop_id}/receipts",
            {"limit": max(1, min(int(limit), 100))},
        )
        if payload is None:
            return []
        return [
            self._map_receipt(item) for item in payload.get("results") or []
        ]

    def get_fulfillment(self, order_ref: str | None) -> dict[str, Any] | None:
        # Etsy receipts carry no tracking fields — always None, honestly.
        return None
