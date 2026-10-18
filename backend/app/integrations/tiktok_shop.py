"""Real TikTok Shop Open Platform adapter — fully implemented, tested offline.

``TikTokShopAdapter`` implements ``StoreIntegration`` against the TikTok
Shop Open Platform API (REST for orders/products). All HTTP traffic is
mocked in tests — no live TikTok call has ever been made; none is made
without a configured store (TIKTOK_SHOP_APP_KEY + TIKTOK_SHOP_APP_SECRET
+ TIKTOK_SHOP_ACCESS_TOKEN + TIKTOK_SHOP_SHOP_ID).

Docs references (checked 2026-10-18):
- Open Platform / app registration & OAuth 2.0 (app_key/app_secret →
  auth code → access_token + refresh_token + shop cipher):
  https://www.keyapi.ai/blog/tiktok-shop-api-integration-guide-sellers/
- API base: ``https://open-api.tiktokglobalshop.com``; access token in
  the ``x-tts-access-token`` header; shop identified by the shop cipher.
- Order endpoints (documented pattern): ``POST
  /order/202309/orders/search`` (list/search by order id), ``GET
  /order/202309/orders/{order_id}`` (detail).
- Product endpoints (documented pattern): ``POST
  /product/202309/products/search``, ``GET
  /product/202309/products/{product_id}``.
- Request signing: every API call carries ``app_key``, ``timestamp``
  and ``sign`` query params; ``sign`` is the HMAC-SHA256 hex digest of
  the sorted parameters (+ body) keyed with ``app_secret``. The exact
  concatenation order below follows the documented pattern — VALIDATE
  against a dev app in the Partner Center before first live use; all
  tests mock the HTTP layer, so no live behavior depends on it.
- Webhook verification (per the official webhook overview, via
  https://dev.to/unifyport/tiktok-shop-customer-service-webhooks-a-production-ready-implementation-guide-2cid):
  HMAC-SHA256 hex digest of ``app_key + raw_body`` keyed with
  ``app_secret``, compared (constant-time) against the ``authorization``
  header. Notification id: ``tts_notification_id``. See ``webhooks.py``.

Construction rules (hard):
- Only from server-side env vars ``TIKTOK_SHOP_APP_KEY`` +
  ``TIKTOK_SHOP_APP_SECRET`` + ``TIKTOK_SHOP_ACCESS_TOKEN`` +
  ``TIKTOK_SHOP_SHOP_ID`` — never from the frontend, never from a
  committed file. Missing vars → ``IntegrationConfigError`` with setup
  instructions; no network call is ever attempted unconfigured.
- Tokens live only in memory / server env (``chmod 600`` env file on
  the server). They are never logged, never returned in payloads.

Reliability: 10 s timeout per request, up to 2 retries with
exponential backoff (0.5 s, 1 s). HTTP 429 honors ``Retry-After``
(capped at 10 s).

Marketplace reality (honest, documented — see
docs/TIKTOK-SHOP-ADAPTER.md): TikTok Shop sales happen in-app. Our
chat widget CANNOT be embedded in TikTok; customer messages go through
TikTok Shop's own customer-service/IM APIs under their policies.
This adapter covers backend data sync (orders/products for the
pipeline + dashboard). In-app messaging automation is out of scope.

EXPLICIT RULE: never point this at a real shop without Shahid's
written go-ahead. Dev app + sandbox first.
"""

from __future__ import annotations

import hashlib
import hmac
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

#: TikTok Shop Open Platform base URL (global).
DEFAULT_BASE_URL = "https://open-api.tiktokglobalshop.com"
#: Timeout per TikTok Shop HTTP request (seconds).
REQUEST_TIMEOUT_SECONDS = 10
#: Number of retries after the initial attempt (2 retries = 3 attempts max).
MAX_RETRIES = 2
_BACKOFF_SECONDS = (0.5, 1.0)

_TAG_RE = re.compile(r"<[^>]+>")


def _http_json(
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any] | None,
    timeout: int,
):
    """Module-level so tests can monkeypatch it (no real network in tests).

    Returns ``(status_code, parsed_json)``. Raises
    ``urllib.error.URLError`` / ``HTTPError`` on transport failure.
    """
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers=headers, method=method.upper()
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        return response.status, json.loads(raw) if raw.strip() else {}


def _strip_html(text: str | None) -> str:
    return _TAG_RE.sub("", text or "").strip()


def _amount_to_cents(amount: Any) -> int | None:
    """TikTok amounts are usually integer-cent strings ("3499"); fall
    back to decimal parsing ("34.99") — never raise."""
    if amount is None:
        return None
    text = str(amount).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return int(Decimal(text) * 100)
    except (InvalidOperation, ValueError, TypeError):
        return None


def _sign_request(
    app_key: str, app_secret: str, params: dict[str, str], body: str
) -> str:
    """Compute the TikTok Shop API ``sign`` parameter.

    Documented pattern: sort all params (except ``sign``) by key,
    concatenate as ``key+value``, then
    ``sign = hex(HMAC_SHA256(app_secret, app_key + concat + body))``.
    VALIDATE against a dev app in the Partner Center before first live
    use — the offline tests mock the HTTP layer and never depend on
    this exact formula.
    """
    concat = "".join(
        f"{key}{params[key]}" for key in sorted(params) if key != "sign"
    )
    message = (app_key + concat + (body or "")).encode("utf-8")
    return hmac.new(
        app_secret.encode("utf-8"), message, hashlib.sha256
    ).hexdigest()


class TikTokShopAdapter(StoreIntegration):
    """TikTok Shop Open Platform backend. Never constructed without the
    ``TIKTOK_SHOP_APP_KEY`` + ``TIKTOK_SHOP_APP_SECRET`` +
    ``TIKTOK_SHOP_ACCESS_TOKEN`` + ``TIKTOK_SHOP_SHOP_ID`` env vars
    (missing vars → ``IntegrationConfigError``; the factory falls back
    to demo)."""

    def __init__(self, settings: "Settings", store_id: str) -> None:
        super().__init__(store_id)
        app_key = (settings.tiktok_shop_app_key or "").strip()
        app_secret = settings.tiktok_shop_app_secret or ""
        access_token = settings.tiktok_shop_access_token or ""
        shop_id = (settings.tiktok_shop_shop_id or "").strip()
        if not app_key or not app_secret or not access_token or not shop_id:
            raise IntegrationConfigError(
                "TikTok Shop adapter not configured: "
                "TIKTOK_SHOP_APP_KEY, TIKTOK_SHOP_APP_SECRET, "
                "TIKTOK_SHOP_ACCESS_TOKEN and TIKTOK_SHOP_SHOP_ID are missing "
                "(server-side env vars only, never commit them). "
                "Register an app in the TikTok Shop Partner Center "
                "(custom app for your own shop), complete OAuth, "
                "put token + shop cipher in the backend .env. "
                "See docs/TIKTOK-SHOP-ADAPTER.md."
            )
        self._app_key = app_key
        # Secrets stay in memory only; never logged or in payloads.
        self._app_secret = app_secret
        self._access_token = access_token
        self._shop_id = shop_id
        self._base_url = (
            (settings.tiktok_shop_base_url or DEFAULT_BASE_URL)
            .strip()
            .rstrip("/")
            or DEFAULT_BASE_URL
        )

    @property
    def backend_name(self) -> str:
        return "tiktok_shop"

    # -- low-level HTTP ---------------------------------------------------
    def _call(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> dict | list | None:
        """Signed API call → parsed JSON, or None on 404.

        Raises IntegrationError on network failure, timeouts and other
        non-retryable statuses. Up to MAX_RETRIES retries with backoff
        on timeouts, 429 and 5xx. Never leaks the app secret / token.
        """
        timestamp = str(int(time.time()))
        params: dict[str, str] = {
            "app_key": self._app_key,
            "shop_cipher": self._shop_id,
            "timestamp": timestamp,
        }
        body_str = json.dumps(body) if body is not None else ""
        params["sign"] = _sign_request(
            self._app_key, self._app_secret, params, body_str
        )
        url = self._base_url + path + "?" + urllib.parse.urlencode(params)
        headers = {
            "x-tts-access-token": self._access_token,
            "Content-Type": "application/json",
        }
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                status, payload = _http_json(
                    method, url, headers, body, REQUEST_TIMEOUT_SECONDS
                )
                if not isinstance(payload, (dict, list)):
                    raise IntegrationError(
                        "TikTok Shop API returned a non-JSON payload "
                        f"for {path}"
                    )
                return payload
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return None
                if exc.code in (401, 403):
                    raise IntegrationError(
                        "TikTok Shop API denied the request "
                        f"(HTTP {exc.code}). Check/refresh the access token "
                        "and check app permissions (scopes) in the Partner Center."
                    ) from exc
                if exc.code == 429 or 500 <= exc.code < 600:
                    last_error = exc
                    retry_after = (
                        exc.headers.get("Retry-After") if exc.headers else None
                    )
                    self._sleep_before_retry(attempt, retry_after)
                    continue
                raise IntegrationError(
                    "TikTok Shop API request failed: "
                    f"HTTP {exc.code} for {path}"
                ) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                self._sleep_before_retry(attempt, None)
                continue
        raise IntegrationError(
            "TikTok Shop API unreachable after "
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
    #: TikTok Shop ``order_status`` → canonical status. Unlike
    #: Shopify/WooCommerce, TikTok Shop reports REAL delivery, so
    #: ``DELIVERED`` → "delivered" is honest here, not invented.
    #: ``COMPLETED`` means delivered + no return within 14 days.
    #:
    #: | TikTok Shop status       | canonical status |
    #: |--------------------------|------------------|
    #: | UNPAID                   | processing       |
    #: | AWAITING_SHIPMENT        | processing       |
    #: | AWAITING_COLLECTION      | processing       |
    #: | PARTIALLY_SHIPPED        | shipped          |
    #: | IN_TRANSIT               | shipped          |
    #: | DELIVERED                | delivered        |
    #: | COMPLETED                | delivered        |
    #: | CANCELLED                | cancelled        |
    #: | null / other             | processing       |
    TIKTOK_ORDER_STATUS_MAP = {
        "UNPAID": "processing",
        "AWAITING_SHIPMENT": "processing",
        "AWAITING_COLLECTION": "processing",
        "PARTIALLY_SHIPPED": "shipped",
        "IN_TRANSIT": "shipped",
        "DELIVERED": "delivered",
        "COMPLETED": "delivered",
        "CANCELLED": "cancelled",
    }

    @staticmethod
    def _map_order(tiktok_order: dict[str, Any]) -> dict[str, Any]:
        """Map a TikTok Shop order to the canonical order dict."""
        order_id = str(
            tiktok_order.get("order_id") or tiktok_order.get("id") or ""
        )
        raw_status = str(tiktok_order.get("order_status") or "").upper()
        status = TikTokShopAdapter.TIKTOK_ORDER_STATUS_MAP.get(
            raw_status, "processing"
        )
        payment = tiktok_order.get("payment") or {}
        # Tracking: TikTok exposes it via the package/fulfillment info
        # when present; otherwise None (honest, never invented).
        carrier = None
        tracking_ref = None
        packages = tiktok_order.get("packages") or []
        for package in packages:
            tracking_ref = (
                package.get("tracking_number") or tracking_ref
            )
            carrier = (
                package.get("shipping_provider") or carrier
            )
            if tracking_ref or carrier:
                break
        return {
            "order_ref": order_id,
            "status": status,
            "carrier": carrier,
            "tracking_ref": tracking_ref,
            "eta_date": None,  # TikTok Shop exposes no delivery ETA field
            "total_cents": _amount_to_cents(payment.get("total_amount")),
            "currency": payment.get("currency") or "EUR",
            "tiktok_order_id": order_id,
            "tiktok_order_status": tiktok_order.get("order_status"),
        }

    @staticmethod
    def _map_product(tiktok_product: dict[str, Any]) -> dict[str, Any]:
        skus = tiktok_product.get("skus") or []
        seller_sku = skus[0].get("seller_sku") if skus else None
        return {
            "id": tiktok_product.get("id"),
            "sku": seller_sku,
            "name": tiktok_product.get("title") or tiktok_product.get("name"),
            "description": _strip_html(tiktok_product.get("description")),
            # No direct TikTok equivalent of the demo attributes
            # (spuelmaschinengeeignet, ...): {} keeps the pipeline honest.
            "attributes_json": json.dumps({}),
            "tiktok_product_id": tiktok_product.get("id"),
        }

    # -- StoreIntegration -------------------------------------------------
    def get_product(self, sku_or_id: str) -> dict[str, Any] | None:
        key = (sku_or_id or "").strip()
        if not key:
            return None
        payload = self._call(
            "GET", f"/product/202309/products/{urllib.parse.quote(key)}"
        )
        if payload is None:
            return None
        product = (payload or {}).get("data") or {}
        if not product:
            return None
        return self._map_product(product)

    def search_products(self, query: str) -> list[dict[str, Any]]:
        payload = self._call(
            "POST",
            "/product/202309/products/search",
            {"keyword": query or "", "page_size": 10},
        )
        if payload is None:
            return []
        products = (payload or {}).get("data", {}).get("products") or []
        return [self._map_product(item) for item in products]

    def get_order(self, order_ref: str | None) -> dict[str, Any] | None:
        ref = (order_ref or "").strip().lstrip("#")
        if not ref:
            return None
        payload = self._call(
            "POST",
            "/order/202309/orders/search",
            {"order_id": ref, "page_size": 1},
        )
        orders = (payload or {}).get("data", {}).get("orders") or []
        if not orders:
            return None
        return self._map_order(orders[0])

    def list_orders(self, limit: int = 50) -> list[dict[str, Any]]:
        payload = self._call(
            "POST",
            "/order/202309/orders/search",
            {"page_size": max(1, min(int(limit), 100))},
        )
        orders = (payload or {}).get("data", {}).get("orders") or []
        return [self._map_order(item) for item in orders]

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
            "eta_date": None,  # TikTok Shop exposes no ETA field
            "tiktok_order_id": order.get("tiktok_order_id"),
        }
