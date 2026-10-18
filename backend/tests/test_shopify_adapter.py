"""Phase 9b tests: real ShopifyAdapter selection, mapping, webhooks.

All offline: ``_http_get_json`` is monkeypatched - no real network calls,
no real Shopify store, no real credentials. Covers:

- factory: STORE_BACKEND=shopify selects the real adapter when configured,
  falls back to demo (warning) when SHOPIFY_* is missing;
- env vars: SHOPIFY_ADMIN_TOKEN (primary) + legacy SHOPIFY_ADMIN_API_TOKEN;
  English IntegrationConfigError when unconfigured;
- order/product mapping incl. the documented Shopify->canonical status
  table; not-found handling (empty list, 404, blank ref);
- retry behavior on HTTP 429;
- webhooks: orders/create, orders/updated, refunds/create -> 200 received
  with topic recorded; unknown topics recorded as unhandled (still 200);
  malformed JSON bodies never crash; duplicate webhook ids processed once.

Isolated temp DB per test module run (dependency_overrides).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import urllib.error

import pytest

from app.api import deps
from app.config import Settings
from app.db.database import connect, init_db
from app.db.seed import DEMO_STORE_ID, create_demo_store
from app.integrations import (
    DemoStoreAdapter,
    IntegrationConfigError,
    IntegrationError,
    ShopifyAdapter,
    get_store_integration,
)
from app.security.audit import recent_audits


# --- fixtures ------------------------------------------------------------


@pytest.fixture()
def isolated_db(tmp_path):
    db_path = tmp_path / "phase9b.db"
    init_db(db_path)
    conn = connect(db_path)
    try:
        create_demo_store(conn)
    finally:
        conn.close()
    return db_path


@pytest.fixture()
def isolated_conn(isolated_db):
    conn = connect(isolated_db)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture()
def test_settings(isolated_db):
    s = Settings()
    s.database_path = isolated_db
    s.demo_mode = True
    s.llm_provider = "mock"
    return s


@pytest.fixture()
def isolated_client(isolated_db, test_settings):
    from fastapi.testclient import TestClient

    from app.main import app

    def _get_db():
        conn = connect(isolated_db, check_same_thread=False)
        try:
            yield conn
        finally:
            conn.close()

    app.dependency_overrides[deps.get_db] = _get_db
    app.dependency_overrides[deps.get_settings] = lambda: test_settings
    try:
        with TestClient(app) as client:
            yield client, test_settings
    finally:
        app.dependency_overrides.pop(deps.get_db, None)
        app.dependency_overrides.pop(deps.get_settings, None)


def _shopify_env(monkeypatch, **overrides):
    for var in (
        "SHOPIFY_SHOP_DOMAIN",
        "SHOPIFY_ADMIN_TOKEN",
        "SHOPIFY_ADMIN_API_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)
    return Settings()


# --- factory selection + fallback ----------------------------------------


def test_factory_shopify_falls_back_to_demo_when_unconfigured(
    monkeypatch, isolated_conn, test_settings
):
    _shopify_env(monkeypatch)
    test_settings.store_backend = "shopify"
    adapter = get_store_integration(test_settings, isolated_conn, DEMO_STORE_ID)
    assert isinstance(adapter, DemoStoreAdapter)
    assert adapter.backend_name == "demo"


def test_factory_shopify_selects_real_adapter_when_configured(
    monkeypatch, isolated_conn, test_settings
):
    _shopify_env(monkeypatch)  # ensure a clean env first
    test_settings.store_backend = "shopify"
    test_settings.shopify_shop_domain = "muster-shop.myshopify.com"
    test_settings.shopify_admin_api_token = "shpat_fake"
    adapter = get_store_integration(test_settings, isolated_conn, DEMO_STORE_ID)
    assert isinstance(adapter, ShopifyAdapter)
    assert adapter.backend_name == "shopify"


def test_missing_env_raises_german_error(monkeypatch):
    settings = _shopify_env(monkeypatch)
    with pytest.raises(IntegrationConfigError) as excinfo:
        ShopifyAdapter(settings, DEMO_STORE_ID)
    message = str(excinfo.value)
    assert "not configured" in message
    assert "SHOPIFY_SHOP_DOMAIN" in message
    assert "SHOPIFY_ADMIN_TOKEN" in message
    # Legacy var name + docs stay discoverable.
    assert "SHOPIFY_ADMIN_API_TOKEN" in message
    assert "docs/SHOPIFY-ADAPTER.md" in message


def test_new_token_var_accepted(monkeypatch):
    settings = _shopify_env(
        monkeypatch,
        SHOPIFY_SHOP_DOMAIN="muster-shop.myshopify.com",
        SHOPIFY_ADMIN_TOKEN="shpat_fake_new",
    )
    adapter = ShopifyAdapter(settings, DEMO_STORE_ID)
    assert adapter.backend_name == "shopify"


def test_legacy_token_var_still_accepted(monkeypatch):
    settings = _shopify_env(
        monkeypatch,
        SHOPIFY_SHOP_DOMAIN="muster-shop.myshopify.com",
        SHOPIFY_ADMIN_API_TOKEN="shpat_fake_legacy",
    )
    adapter = ShopifyAdapter(settings, DEMO_STORE_ID)
    assert adapter.backend_name == "shopify"


def test_bad_domain_raises_german_error(monkeypatch):
    settings = _shopify_env(
        monkeypatch,
        SHOPIFY_SHOP_DOMAIN="example.com",
        SHOPIFY_ADMIN_TOKEN="shpat_fake",
    )
    with pytest.raises(IntegrationConfigError) as excinfo:
        ShopifyAdapter(settings, DEMO_STORE_ID)
    assert "myshopify.com" in str(excinfo.value)


# --- mocked HTTP layer ----------------------------------------------------


def _configured_adapter(monkeypatch):
    settings = _shopify_env(
        monkeypatch,
        SHOPIFY_SHOP_DOMAIN="muster-shop.myshopify.com",
        SHOPIFY_ADMIN_TOKEN="shpat_fake",
    )
    return ShopifyAdapter(settings, DEMO_STORE_ID)


def _fake_order(name="#10482", fulfillment_status="fulfilled", cancelled=False):
    return {
        "id": 5500000001,
        "name": name,
        "cancelled": cancelled,
        "fulfillment_status": fulfillment_status,
        "total_price": "34.99",
        "currency": "EUR",
    }


def test_get_order_found(monkeypatch):
    import app.integrations.shopify as shopify_mod

    adapter = _configured_adapter(monkeypatch)
    calls: list[str] = []

    def fake(url, headers, timeout):
        calls.append(url)
        assert "shpat_fake" not in url
        assert headers["X-Shopify-Access-Token"] == "shpat_fake"
        return 200, {"orders": [_fake_order()]}

    monkeypatch.setattr(shopify_mod, "_http_get_json", fake)
    order = adapter.get_order("10482")
    assert order is not None
    assert order["order_ref"] == "10482"
    assert order["status"] == "shipped"
    assert order["total_cents"] == 3499
    assert order["currency"] == "EUR"
    assert order["eta_date"] is None  # Shopify has no ETA field (honest)
    assert order["shopify_id"] == "gid://shopify/Order/5500000001"
    assert calls, "must go through the HTTP layer (mocked)"


def test_get_order_not_found_empty_list(monkeypatch):
    import app.integrations.shopify as shopify_mod

    adapter = _configured_adapter(monkeypatch)

    def fake(url, headers, timeout):
        return 200, {"orders": []}

    monkeypatch.setattr(shopify_mod, "_http_get_json", fake)
    assert adapter.get_order("99999") is None


def test_get_order_404_is_none(monkeypatch):
    import app.integrations.shopify as shopify_mod

    adapter = _configured_adapter(monkeypatch)

    def fake_404(url, headers, timeout):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(shopify_mod, "_http_get_json", fake_404)
    assert adapter.get_order("10482") is None


def test_get_order_blank_ref_makes_no_http_call(monkeypatch):
    import app.integrations.shopify as shopify_mod

    adapter = _configured_adapter(monkeypatch)
    calls: list[str] = []

    def fake(url, headers, timeout):
        calls.append(url)
        return 200, {"orders": []}

    monkeypatch.setattr(shopify_mod, "_http_get_json", fake)
    assert adapter.get_order(None) is None
    assert adapter.get_order("   ") is None
    assert calls == []


@pytest.mark.parametrize(
    "shopify_fields,expected",
    [
        ({"cancelled": True, "fulfillment_status": "fulfilled"}, "cancelled"),
        ({"cancelled": False, "fulfillment_status": "fulfilled"}, "shipped"),
        ({"cancelled": False, "fulfillment_status": "partial"}, "processing"),
        ({"cancelled": False, "fulfillment_status": "unfulfilled"}, "processing"),
        ({"cancelled": False, "fulfillment_status": None}, "processing"),
        ({"cancelled": False, "fulfillment_status": "on_hold"}, "processing"),
    ],
)
def test_status_mapping_table(monkeypatch, shopify_fields, expected):
    import app.integrations.shopify as shopify_mod

    adapter = _configured_adapter(monkeypatch)

    def fake(url, headers, timeout):
        return 200, {"orders": [_fake_order(**shopify_fields)]}

    monkeypatch.setattr(shopify_mod, "_http_get_json", fake)
    order = adapter.get_order("10482")
    assert order is not None
    assert order["status"] == expected


def test_status_map_constant_documents_all_cases():
    mapping = ShopifyAdapter.SHOPIFY_FULFILLMENT_STATUS_MAP
    assert mapping["fulfilled"] == "shipped"
    assert mapping["partial"] == "processing"
    # "delivered"/"delayed" are deliberately absent: no reliable signal.
    assert "delivered" not in set(mapping.values())
    assert "delayed" not in set(mapping.values())


def test_get_product_by_id(monkeypatch):
    import app.integrations.shopify as shopify_mod

    adapter = _configured_adapter(monkeypatch)

    def fake(url, headers, timeout):
        assert "/products/123.json" in url
        return 200, {
            "product": {
                "id": 123,
                "title": "AquaPure 750",
                "body_html": "<p>Trinkflasche aus Edelstahl.</p>",
                "variants": [{"sku": "MS-AP750", "price": "19.99"}],
            }
        }

    monkeypatch.setattr(shopify_mod, "_http_get_json", fake)
    product = adapter.get_product("123")
    assert product is not None
    assert product["sku"] == "MS-AP750"
    assert product["name"] == "AquaPure 750"
    assert product["description"] == "Trinkflasche aus Edelstahl."
    assert product["shopify_id"] == "gid://shopify/Product/123"


def test_search_products(monkeypatch):
    import app.integrations.shopify as shopify_mod

    adapter = _configured_adapter(monkeypatch)

    def fake(url, headers, timeout):
        assert "/products.json" in url
        return 200, {
            "products": [
                {
                    "id": 123,
                    "title": "AquaPure 750",
                    "body_html": "",
                    "variants": [{"sku": "MS-AP750"}],
                }
            ]
        }

    monkeypatch.setattr(shopify_mod, "_http_get_json", fake)
    matches = adapter.search_products("Trinkflasche")
    assert len(matches) == 1
    assert matches[0]["name"] == "AquaPure 750"


def test_get_product_by_sku_via_search(monkeypatch):
    import app.integrations.shopify as shopify_mod

    adapter = _configured_adapter(monkeypatch)

    def fake(url, headers, timeout):
        assert "/products.json" in url
        return 200, {
            "products": [
                {
                    "id": 123,
                    "title": "AquaPure 750",
                    "body_html": "",
                    "variants": [{"sku": "MS-AP750"}],
                }
            ]
        }

    monkeypatch.setattr(shopify_mod, "_http_get_json", fake)
    product = adapter.get_product("MS-AP750")
    assert product is not None
    assert product["sku"] == "MS-AP750"
    assert adapter.get_product("NO-SUCH-SKU") is None


def test_retry_on_429_then_success(monkeypatch):
    import app.integrations.shopify as shopify_mod

    adapter = _configured_adapter(monkeypatch)
    attempts: list[int] = []

    def fake(url, headers, timeout):
        attempts.append(1)
        if len(attempts) == 1:
            raise urllib.error.HTTPError(url, 429, "Too Many", {}, None)
        return 200, {"orders": [_fake_order()]}

    monkeypatch.setattr(shopify_mod, "_http_get_json", fake)
    order = adapter.get_order("10482")
    assert order is not None and order["order_ref"] == "10482"
    assert len(attempts) == 2


def test_get_fulfillment_maps_tracking(monkeypatch):
    import app.integrations.shopify as shopify_mod

    adapter = _configured_adapter(monkeypatch)

    def fake(url, headers, timeout):
        if "/orders.json" in url:
            return 200, {"orders": [_fake_order()]}
        if "/fulfillments.json" in url:
            return 200, {
                "fulfillments": [
                    {
                        "id": 6600000001,
                        "tracking_number": "1ZFAKE999",
                        "tracking_company": "DHL",
                    }
                ]
            }
        raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr(shopify_mod, "_http_get_json", fake)
    fulfillment = adapter.get_fulfillment("10482")
    assert fulfillment is not None
    assert fulfillment["carrier"] == "DHL"
    assert fulfillment["tracking_ref"] == "1ZFAKE999"
    assert fulfillment["eta_date"] is None


def test_get_fulfillment_none_without_tracking(monkeypatch):
    import app.integrations.shopify as shopify_mod

    adapter = _configured_adapter(monkeypatch)

    def fake(url, headers, timeout):
        if "/orders.json" in url:
            return 200, {"orders": [_fake_order(fulfillment_status="unfulfilled")]}
        if "/fulfillments.json" in url:
            return 200, {"fulfillments": []}
        raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr(shopify_mod, "_http_get_json", fake)
    assert adapter.get_fulfillment("10482") is None


def test_list_orders_limit(monkeypatch):
    import app.integrations.shopify as shopify_mod

    adapter = _configured_adapter(monkeypatch)
    seen: list[str] = []

    def fake(url, headers, timeout):
        seen.append(url)
        return 200, {"orders": [_fake_order()]}

    monkeypatch.setattr(shopify_mod, "_http_get_json", fake)
    orders = adapter.list_orders(5)
    assert len(orders) == 1
    assert "limit=5" in seen[0]


# --- webhooks: topics, idempotency, malformed payloads ----------------------

_WEBHOOK_SECRET = "phase9b-test-webhook-secret"


def _sign(body: bytes, secret: str = _WEBHOOK_SECRET) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def _webhook_headers(body: bytes, topic: str, webhook_id: str) -> dict:
    return {
        "X-Shopify-Topic": topic,
        "X-Shopify-Shop-Domain": "muster-shop.myshopify.com",
        "X-Shopify-Hmac-Sha256": _sign(body),
        "X-Shopify-Webhook-Id": webhook_id,
        "Content-Type": "application/json",
    }


def _post_webhook(isolated_client, body: bytes, topic: str, webhook_id: str):
    client, settings = isolated_client
    settings.shopify_webhook_secret = _WEBHOOK_SECRET
    return client.post(
        "/api/integrations/shopify/webhooks",
        content=body,
        headers=_webhook_headers(body, topic, webhook_id),
    )


@pytest.mark.parametrize(
    "topic",
    ["orders/create", "orders/updated", "refunds/create"],
)
def test_webhook_known_topics_received(isolated_client, isolated_db, topic):
    body = b'{"id": 1, "name": "#10482"}'
    resp = _post_webhook(isolated_client, body, topic, f"9b-{topic}-1")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "received"
    conn = connect(isolated_db)
    try:
        event = conn.execute(
            "SELECT * FROM shopify_webhook_events WHERE webhook_id = ?",
            (f"9b-{topic}-1",),
        ).fetchone()
        assert event is not None
        assert event["topic"] == topic
        audits = recent_audits(conn, "shopify_webhook_received")
        assert len(audits) == 1
    finally:
        conn.close()


def test_webhook_unknown_topic_recorded_not_crash(isolated_client, isolated_db):
    body = b'{"id": 9}'
    resp = _post_webhook(isolated_client, body, "products/update", "9b-unknown-1")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "received"
    conn = connect(isolated_db)
    try:
        event = conn.execute(
            "SELECT * FROM shopify_webhook_events WHERE webhook_id = ?",
            ("9b-unknown-1",),
        ).fetchone()
        assert event is not None
        assert len(recent_audits(conn, "shopify_webhook_received")) == 1
    finally:
        conn.close()


def test_webhook_malformed_json_never_crashes(isolated_client, isolated_db):
    body = b"not-json{"
    resp = _post_webhook(isolated_client, body, "orders/create", "9b-malformed-1")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "received"
    conn = connect(isolated_db)
    try:
        assert len(recent_audits(conn, "shopify_webhook_received")) == 1
    finally:
        conn.close()


def test_webhook_duplicate_refund_processed_once(isolated_client, isolated_db):
    body = b'{"id": 7, "order_id": 5500000001}'
    first = _post_webhook(isolated_client, body, "refunds/create", "9b-dupe-refund")
    second = _post_webhook(isolated_client, body, "refunds/create", "9b-dupe-refund")
    assert first.json()["status"] == "received"
    assert second.json()["status"] == "duplicate"
    conn = connect(isolated_db)
    try:
        assert len(recent_audits(conn, "shopify_webhook_received")) == 1
    finally:
        conn.close()
