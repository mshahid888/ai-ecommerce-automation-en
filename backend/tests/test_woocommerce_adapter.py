"""WooCommerce adapter tests: selection, mapping, auth, webhooks.

All offline: ``_http_get_json`` is monkeypatched - no real network calls,
no real shop, no real credentials (fake ``ck_fake``/``cs_fake`` only).
Covers:

- factory: STORE_BACKEND=woocommerce selects the real adapter when
  configured, falls back to demo (warning) when WOOCOMMERCE_* is
  missing;
- env vars: English IntegrationConfigError when unconfigured; non-HTTPS
  shop URL is refused;
- auth: HTTP Basic header formation from consumer key/secret;
- order/product mapping incl. the documented WooCommerce->canonical status
  table; not-found handling (404, blank ref, non-numeric ref);
- retry behavior on HTTP 429;
- webhooks: order.created/order.updated -> 200 received with delivery
  recorded; bad signature -> 401; missing secret -> 503; duplicate
  delivery ids processed once; malformed JSON never crashes; unknown
  topics recorded as unhandled (still 200).

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
    WooCommerceAdapter,
    get_store_integration,
)
from app.integrations import woocommerce as woocommerce_mod
from app.security.audit import recent_audits


# --- fixtures ------------------------------------------------------------


@pytest.fixture()
def isolated_db(tmp_path):
    db_path = tmp_path / "phase_wc.db"
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


def _wc_env(monkeypatch, **overrides):
    for var in (
        "WOOCOMMERCE_SHOP_URL",
        "WOOCOMMERCE_CONSUMER_KEY",
        "WOOCOMMERCE_CONSUMER_SECRET",
        "WOOCOMMERCE_WEBHOOK_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)
    return Settings()


def _wc_settings(monkeypatch):
    return _wc_env(
        monkeypatch,
        WOOCOMMERCE_SHOP_URL="https://example-shop.de",
        WOOCOMMERCE_CONSUMER_KEY="ck_fake",
        WOOCOMMERCE_CONSUMER_SECRET="cs_fake",
    )


# --- factory selection + fallback ----------------------------------------


def test_factory_woocommerce_falls_back_to_demo_when_unconfigured(
    monkeypatch, isolated_conn, test_settings
):
    _wc_env(monkeypatch)
    test_settings.store_backend = "woocommerce"
    adapter = get_store_integration(test_settings, isolated_conn, DEMO_STORE_ID)
    assert isinstance(adapter, DemoStoreAdapter)
    assert adapter.backend_name == "demo"


def test_factory_woocommerce_selects_real_adapter_when_configured(
    monkeypatch, isolated_conn, test_settings
):
    _wc_env(monkeypatch)  # clean env first
    test_settings.store_backend = "woocommerce"
    test_settings.woocommerce_shop_url = "https://example-shop.de"
    test_settings.woocommerce_consumer_key = "ck_fake"
    test_settings.woocommerce_consumer_secret = "cs_fake"
    adapter = get_store_integration(test_settings, isolated_conn, DEMO_STORE_ID)
    assert isinstance(adapter, WooCommerceAdapter)
    assert adapter.backend_name == "woocommerce"


def test_missing_env_raises_german_error(monkeypatch):
    settings = _wc_env(monkeypatch)
    with pytest.raises(IntegrationConfigError) as excinfo:
        WooCommerceAdapter(settings, DEMO_STORE_ID)
    message = str(excinfo.value)
    assert "not configured" in message
    assert "WOOCOMMERCE_SHOP_URL" in message
    assert "WOOCOMMERCE_CONSUMER_KEY" in message
    assert "WOOCOMMERCE_CONSUMER_SECRET" in message
    assert "docs/WOOCOMMERCE-ADAPTER.md" in message


def test_http_shop_url_refused(monkeypatch):
    settings = _wc_env(
        monkeypatch,
        WOOCOMMERCE_SHOP_URL="http://example-shop.de",
        WOOCOMMERCE_CONSUMER_KEY="ck_fake",
        WOOCOMMERCE_CONSUMER_SECRET="cs_fake",
    )
    with pytest.raises(IntegrationConfigError) as excinfo:
        WooCommerceAdapter(settings, DEMO_STORE_ID)
    assert "https" in str(excinfo.value).lower()


# --- auth ----------------------------------------------------------------


def test_basic_auth_header_formation(monkeypatch):
    settings = _wc_settings(monkeypatch)
    adapter = WooCommerceAdapter(settings, DEMO_STORE_ID)
    expected = "Basic " + base64.b64encode(b"ck_fake:cs_fake").decode("ascii")
    assert adapter._auth_header == expected
    # Secrets never appear in URLs built by the adapter.
    assert "cs_fake" not in adapter._base_url
    assert "ck_fake" not in adapter._base_url


# --- order mapping -------------------------------------------------------

_FAKE_ORDER = {
    "id": 12345,
    "number": "12345",
    "status": "processing",
    "currency": "EUR",
    "total": "34.99",
    "meta_data": [],
}


def _fake_http(payload):
    def fake(url, headers, timeout):
        assert headers.get("Authorization", "").startswith("Basic ")
        return 200, payload

    return fake


def _fake_http_error(code):
    def fake(url, headers, timeout):
        raise urllib.error.HTTPError(url, code, "error", {}, None)

    return fake


@pytest.mark.parametrize(
    "wc_status,expected",
    [
        ("pending", "processing"),
        ("processing", "processing"),
        ("on-hold", "processing"),
        ("completed", "shipped"),
        ("cancelled", "cancelled"),
        ("refunded", "cancelled"),
        ("failed", "cancelled"),
        ("trash", "cancelled"),
        ("mystery-status", "processing"),  # unknown -> safe default
    ],
)
def test_status_mapping_table(monkeypatch, wc_status, expected):
    settings = _wc_settings(monkeypatch)
    adapter = WooCommerceAdapter(settings, DEMO_STORE_ID)
    order = dict(_FAKE_ORDER, status=wc_status)
    monkeypatch.setattr(woocommerce_mod, "_http_get_json", _fake_http(order))
    result = adapter.get_order("12345")
    assert result is not None
    assert result["status"] == expected
    assert result["order_ref"] == "12345"


def test_delivered_and_delayed_never_invented(monkeypatch):
    settings = _wc_settings(monkeypatch)
    adapter = WooCommerceAdapter(settings, DEMO_STORE_ID)
    for wc_status in ("pending", "processing", "on-hold", "completed"):
        order = dict(_FAKE_ORDER, status=wc_status)
        mapped = WooCommerceAdapter._map_order(order)
        assert mapped["status"] not in ("delivered", "delayed")


def test_get_order_accepts_hash_prefix(monkeypatch):
    settings = _wc_settings(monkeypatch)
    adapter = WooCommerceAdapter(settings, DEMO_STORE_ID)
    monkeypatch.setattr(
        woocommerce_mod, "_http_get_json", _fake_http(_FAKE_ORDER)
    )
    result = adapter.get_order("#12345")
    assert result is not None
    assert result["order_ref"] == "12345"
    assert result["total_cents"] == 3499
    assert result["currency"] == "EUR"
    assert result["woocommerce_id"] == 12345


def test_get_order_rejects_non_numeric_ref(monkeypatch):
    settings = _wc_settings(monkeypatch)
    adapter = WooCommerceAdapter(settings, DEMO_STORE_ID)

    def fail(url, headers, timeout):
        raise AssertionError("no network call expected")

    monkeypatch.setattr(woocommerce_mod, "_http_get_json", fail)
    assert adapter.get_order("ABC-123") is None
    assert adapter.get_order("") is None
    assert adapter.get_order(None) is None


def test_get_order_not_found_returns_none(monkeypatch):
    settings = _wc_settings(monkeypatch)
    adapter = WooCommerceAdapter(settings, DEMO_STORE_ID)
    monkeypatch.setattr(
        woocommerce_mod, "_http_get_json", _fake_http_error(404)
    )
    assert adapter.get_order("99999") is None


def test_order_tracking_from_meta(monkeypatch):
    settings = _wc_settings(monkeypatch)
    adapter = WooCommerceAdapter(settings, DEMO_STORE_ID)
    order = dict(
        _FAKE_ORDER,
        meta_data=[
            {"key": "_tracking_number", "value": "TRK-1"},
            {"key": "_tracking_provider", "value": "DHL"},
        ],
    )
    monkeypatch.setattr(woocommerce_mod, "_http_get_json", _fake_http(order))
    fulfillment = adapter.get_fulfillment("12345")
    assert fulfillment is not None
    assert fulfillment["tracking_ref"] == "TRK-1"
    assert fulfillment["carrier"] == "DHL"
    assert fulfillment["eta_date"] is None


def test_get_fulfillment_none_without_tracking(monkeypatch):
    settings = _wc_settings(monkeypatch)
    adapter = WooCommerceAdapter(settings, DEMO_STORE_ID)
    monkeypatch.setattr(
        woocommerce_mod, "_http_get_json", _fake_http(_FAKE_ORDER)
    )
    assert adapter.get_fulfillment("12345") is None


# --- products ------------------------------------------------------------

_FAKE_PRODUCT = {
    "id": 77,
    "sku": "MS-AP750",
    "name": "AquaPure 750",
    "description": "<p>Trinkflasche</p>",
    "attributes": [{"name": "Farbe", "options": ["Blau"]}],
}


def test_get_product_by_numeric_id(monkeypatch):
    settings = _wc_settings(monkeypatch)
    adapter = WooCommerceAdapter(settings, DEMO_STORE_ID)
    monkeypatch.setattr(
        woocommerce_mod, "_http_get_json", _fake_http(_FAKE_PRODUCT)
    )
    product = adapter.get_product("77")
    assert product is not None
    assert product["sku"] == "MS-AP750"
    assert product["description"] == "Trinkflasche"  # HTML stripped
    assert product["woocommerce_id"] == 77


def test_get_product_by_sku(monkeypatch):
    settings = _wc_settings(monkeypatch)
    adapter = WooCommerceAdapter(settings, DEMO_STORE_ID)
    monkeypatch.setattr(
        woocommerce_mod, "_http_get_json", _fake_http([_FAKE_PRODUCT])
    )
    product = adapter.get_product("MS-AP750")
    assert product is not None
    assert product["name"] == "AquaPure 750"


def test_search_products(monkeypatch):
    settings = _wc_settings(monkeypatch)
    adapter = WooCommerceAdapter(settings, DEMO_STORE_ID)
    monkeypatch.setattr(
        woocommerce_mod, "_http_get_json", _fake_http([_FAKE_PRODUCT])
    )
    results = adapter.search_products("Trinkflasche")
    assert len(results) == 1
    assert results[0]["sku"] == "MS-AP750"


# --- retry behavior ------------------------------------------------------

def test_retry_on_429_then_success(monkeypatch):
    settings = _wc_settings(monkeypatch)
    adapter = WooCommerceAdapter(settings, DEMO_STORE_ID)
    calls = []

    def flaky(url, headers, timeout):
        calls.append(url)
        if len(calls) < 3:
            raise urllib.error.HTTPError(url, 429, "rate limited", {}, None)
        return 200, _FAKE_ORDER

    monkeypatch.setattr(woocommerce_mod, "_http_get_json", flaky)
    monkeypatch.setattr(
        WooCommerceAdapter, "_sleep_before_retry", staticmethod(lambda *a: None)
    )
    result = adapter.get_order("12345")
    assert result is not None
    assert len(calls) == 3


def test_401_raises_with_guidance(monkeypatch):
    settings = _wc_settings(monkeypatch)
    adapter = WooCommerceAdapter(settings, DEMO_STORE_ID)
    monkeypatch.setattr(
        woocommerce_mod, "_http_get_json", _fake_http_error(401)
    )
    monkeypatch.setattr(
        WooCommerceAdapter, "_sleep_before_retry", staticmethod(lambda *a: None)
    )
    with pytest.raises(IntegrationError) as excinfo:
        adapter.get_order("12345")
    assert "401" in str(excinfo.value)


# --- webhooks ------------------------------------------------------------

def _wc_signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def test_webhook_unconfigured_returns_503(isolated_client):
    client, settings = isolated_client
    settings.woocommerce_webhook_secret = None
    resp = client.post(
        "/api/integrations/woocommerce/webhooks",
        content=b"{}",
        headers={"X-WC-Webhook-Topic": "order.created"},
    )
    assert resp.status_code == 503
    assert resp.json()["error"] == "woocommerce_not_configured"


def test_webhook_bad_signature_returns_401(isolated_client):
    client, settings = isolated_client
    settings.woocommerce_webhook_secret = "whsec_fake"
    resp = client.post(
        "/api/integrations/woocommerce/webhooks",
        content=b'{"id": 1}',
        headers={
            "X-WC-Webhook-Signature": "invalid",
            "X-WC-Webhook-Delivery-ID": "d-1",
            "X-WC-Webhook-Topic": "order.created",
        },
    )
    assert resp.status_code == 401


def test_webhook_valid_signature_records_delivery(isolated_client, isolated_conn):
    client, settings = isolated_client
    settings.woocommerce_webhook_secret = "whsec_fake"
    body = b'{"id": 12345, "number": "12345"}'
    resp = client.post(
        "/api/integrations/woocommerce/webhooks",
        content=body,
        headers={
            "X-WC-Webhook-Signature": _wc_signature(body, "whsec_fake"),
            "X-WC-Webhook-Delivery-ID": "d-2",
            "X-WC-Webhook-Topic": "order.created",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "received"
    row = isolated_conn.execute(
        "SELECT topic FROM woocommerce_webhook_events WHERE delivery_id = 'd-2'"
    ).fetchone()
    assert row is not None and row[0] == "order.created"
    audits = recent_audits(isolated_conn, limit=5)
    assert any(a["action"] == "woocommerce_webhook_received" for a in audits)


def test_webhook_duplicate_delivery_processed_once(isolated_client, isolated_conn):
    client, settings = isolated_client
    settings.woocommerce_webhook_secret = "whsec_fake"
    body = b'{"id": 12345}'
    headers = {
        "X-WC-Webhook-Signature": _wc_signature(body, "whsec_fake"),
        "X-WC-Webhook-Delivery-ID": "d-3",
        "X-WC-Webhook-Topic": "order.updated",
    }
    first = client.post("/api/integrations/woocommerce/webhooks", content=body, headers=headers)
    second = client.post("/api/integrations/woocommerce/webhooks", content=body, headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"
    count = isolated_conn.execute(
        "SELECT COUNT(*) FROM woocommerce_webhook_events WHERE delivery_id = 'd-3'"
    ).fetchone()[0]
    assert count == 1


def test_webhook_malformed_json_never_crashes(isolated_client):
    client, settings = isolated_client
    settings.woocommerce_webhook_secret = "whsec_fake"
    body = b"not-json{{{"
    resp = client.post(
        "/api/integrations/woocommerce/webhooks",
        content=body,
        headers={
            "X-WC-Webhook-Signature": _wc_signature(body, "whsec_fake"),
            "X-WC-Webhook-Delivery-ID": "d-4",
            "X-WC-Webhook-Topic": "order.created",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "received"


def test_webhook_unknown_topic_recorded_unhandled(isolated_client, isolated_conn):
    client, settings = isolated_client
    settings.woocommerce_webhook_secret = "whsec_fake"
    body = b'{"id": 9}'
    resp = client.post(
        "/api/integrations/woocommerce/webhooks",
        content=body,
        headers={
            "X-WC-Webhook-Signature": _wc_signature(body, "whsec_fake"),
            "X-WC-Webhook-Delivery-ID": "d-5",
            "X-WC-Webhook-Topic": "product.deleted",
        },
    )
    assert resp.status_code == 200
    audits = recent_audits(isolated_conn, limit=5)
    entry = next(
        a for a in audits if a["action"] == "woocommerce_webhook_received"
    )
    import json as _json

    details = _json.loads(entry["details_json"]) if isinstance(entry["details_json"], str) else entry["details_json"]
    assert details["handled"] is False
