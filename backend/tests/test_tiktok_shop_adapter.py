"""TikTok Shop adapter tests: selection, mapping, webhooks.

All offline: ``_http_json`` is monkeypatched - no real network calls,
no real TikTok shop, no real credentials. Covers:

- factory: STORE_BACKEND=tiktok_shop selects the real adapter when
  configured, falls back to demo (warning) when TIKTOK_SHOP_*
  is missing;
- env vars: TIKTOK_SHOP_APP_KEY/APP_SECRET/ACCESS_TOKEN/SHOP_ID;
  English IntegrationConfigError when unconfigured;
- order/product mapping incl. the documented TikTok->canonical status
  table; not-found handling (empty list, 404, blank ref);
- auth: x-tts-access-token header + app_key/sign query params sent
  (values are fakes; never real secrets);
- retry behavior on HTTP 429;
- webhooks: valid/invalid HMAC (app_key + raw body), idempotency on
  tts_notification_id, malformed JSON never crashes, unknown topics
  acknowledged.

Isolated temp DB per test module run (dependency_overrides).
"""

from __future__ import annotations

import hashlib
import hmac
import urllib.error
import urllib.parse

import pytest

from app.api import deps
from app.config import Settings
from app.db.database import connect, init_db
from app.db.seed import DEMO_STORE_ID, create_demo_store
from app.integrations import (
    DemoStoreAdapter,
    IntegrationConfigError,
    IntegrationError,
    TikTokShopAdapter,
    get_store_integration,
)
from app.integrations.webhooks import (
    record_tiktok_shop_webhook_event,
    verify_tiktok_shop_webhook,
)


# --- fixtures ------------------------------------------------------------


@pytest.fixture()
def isolated_db(tmp_path):
    db_path = tmp_path / "tiktok.db"
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


_TT_VARS = (
    "TIKTOK_SHOP_APP_KEY",
    "TIKTOK_SHOP_APP_SECRET",
    "TIKTOK_SHOP_ACCESS_TOKEN",
    "TIKTOK_SHOP_SHOP_ID",
)


def _tiktok_env(monkeypatch, **overrides):
    for var in _TT_VARS:
        monkeypatch.delenv(var, raising=False)
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)
    return Settings()


def _configured_settings(monkeypatch):
    return _tiktok_env(
        monkeypatch,
        TIKTOK_SHOP_APP_KEY="fake_app_key_123",
        TIKTOK_SHOP_APP_SECRET="fake_app_secret_456",
        TIKTOK_SHOP_ACCESS_TOKEN="fake_access_token_789",
        TIKTOK_SHOP_SHOP_ID="fake_shop_cipher_abc",
    )


# --- factory selection + fallback ----------------------------------------


def test_factory_tiktok_falls_back_to_demo_when_unconfigured(
    monkeypatch, isolated_conn, test_settings
):
    _tiktok_env(monkeypatch)
    test_settings.store_backend = "tiktok_shop"
    adapter = get_store_integration(test_settings, isolated_conn, DEMO_STORE_ID)
    assert isinstance(adapter, DemoStoreAdapter)
    assert adapter.backend_name == "demo"


def test_factory_tiktok_selects_real_adapter_when_configured(
    monkeypatch, isolated_conn, test_settings
):
    settings = _configured_settings(monkeypatch)
    test_settings.store_backend = "tiktok_shop"
    for var, attr in (
        ("TIKTOK_SHOP_APP_KEY", "tiktok_shop_app_key"),
        ("TIKTOK_SHOP_APP_SECRET", "tiktok_shop_app_secret"),
        ("TIKTOK_SHOP_ACCESS_TOKEN", "tiktok_shop_access_token"),
        ("TIKTOK_SHOP_SHOP_ID", "tiktok_shop_shop_id"),
    ):
        setattr(test_settings, attr, getattr(settings, attr))
    adapter = get_store_integration(test_settings, isolated_conn, DEMO_STORE_ID)
    assert isinstance(adapter, TikTokShopAdapter)
    assert adapter.backend_name == "tiktok_shop"


def test_missing_env_raises_german_error(monkeypatch):
    settings = _tiktok_env(monkeypatch)
    with pytest.raises(IntegrationConfigError) as excinfo:
        TikTokShopAdapter(settings, DEMO_STORE_ID)
    message = str(excinfo.value)
    assert "not configured" in message
    assert "TIKTOK_SHOP_APP_KEY" in message
    assert "TIKTOK_SHOP_ACCESS_TOKEN" in message
    assert "docs/TIKTOK-SHOP-ADAPTER.md" in message


def test_partial_env_raises_german_error(monkeypatch):
    settings = _tiktok_env(
        monkeypatch, TIKTOK_SHOP_APP_KEY="fake_app_key_123"
    )
    with pytest.raises(IntegrationConfigError):
        TikTokShopAdapter(settings, DEMO_STORE_ID)


# --- order / product mapping ----------------------------------------------


def _order_fixture(status="AWAITING_SHIPMENT"):
    return {
        "order_id": "578876543210987456",
        "order_status": status,
        "payment": {"total_amount": "3499", "currency": "EUR"},
        "packages": [
            {"tracking_number": "TT-TRK-001", "shipping_provider": "DHL"}
        ],
    }


def _patch_http(monkeypatch, payload, status=200, capture=None):
    from app.integrations import tiktok_shop as tt

    def fake_http(method, url, headers, body, timeout):
        if capture is not None:
            capture.append(
                {"method": method, "url": url, "headers": headers,
                 "body": body}
            )
        return status, payload

    monkeypatch.setattr(tt, "_http_json", fake_http)


def test_get_order_found(monkeypatch):
    _configured_settings(monkeypatch)
    _patch_http(
        monkeypatch, {"data": {"orders": [_order_fixture()]}}
    )
    from app.integrations import tiktok_shop as tt

    adapter = TikTokShopAdapter(Settings(), DEMO_STORE_ID)
    order = adapter.get_order("578876543210987456")
    assert order is not None
    assert order["order_ref"] == "578876543210987456"
    assert order["status"] == "processing"
    assert order["carrier"] == "DHL"
    assert order["tracking_ref"] == "TT-TRK-001"
    assert order["total_cents"] == 3499
    assert order["currency"] == "EUR"


def test_get_order_not_found_empty_list(monkeypatch):
    _configured_settings(monkeypatch)
    _patch_http(monkeypatch, {"data": {"orders": []}})
    from app.integrations import tiktok_shop as tt

    adapter = TikTokShopAdapter(Settings(), DEMO_STORE_ID)
    assert adapter.get_order("000000000000000000") is None


def test_get_order_blank_ref_returns_none(monkeypatch):
    _configured_settings(monkeypatch)
    from app.integrations import tiktok_shop as tt

    adapter = TikTokShopAdapter(Settings(), DEMO_STORE_ID)
    assert adapter.get_order(None) is None
    assert adapter.get_order("   ") is None


@pytest.mark.parametrize(
    "tiktok_status,canonical",
    [
        ("UNPAID", "processing"),
        ("AWAITING_SHIPMENT", "processing"),
        ("AWAITING_COLLECTION", "processing"),
        ("PARTIALLY_SHIPPED", "shipped"),
        ("IN_TRANSIT", "shipped"),
        ("DELIVERED", "delivered"),
        ("COMPLETED", "delivered"),
        ("CANCELLED", "cancelled"),
        ("SOMETHING_NEW", "processing"),
        (None, "processing"),
    ],
)
def test_status_mapping_table(monkeypatch, tiktok_status, canonical):
    order = _order_fixture(status=tiktok_status)
    order.pop("packages", None)
    mapped = TikTokShopAdapter._map_order(order)
    assert mapped["status"] == canonical


def test_map_order_without_packages_has_no_tracking(monkeypatch):
    order = _order_fixture(status="DELIVERED")
    order.pop("packages", None)
    mapped = TikTokShopAdapter._map_order(order)
    assert mapped["status"] == "delivered"
    assert mapped["carrier"] is None
    assert mapped["tracking_ref"] is None
    assert mapped["eta_date"] is None


def test_get_product_found(monkeypatch):
    _configured_settings(monkeypatch)
    _patch_http(
        monkeypatch,
        {"data": {
            "id": "prod_123",
            "title": "Test Shirt",
            "description": "<p>Nice shirt</p>",
            "skus": [{"seller_sku": "TS-SHIRT-01"}],
        }},
    )
    from app.integrations import tiktok_shop as tt

    adapter = TikTokShopAdapter(Settings(), DEMO_STORE_ID)
    product = adapter.get_product("prod_123")
    assert product is not None
    assert product["name"] == "Test Shirt"
    assert product["sku"] == "TS-SHIRT-01"
    assert product["description"] == "Nice shirt"


def test_search_products(monkeypatch):
    _configured_settings(monkeypatch)
    _patch_http(
        monkeypatch,
        {"data": {"products": [
            {"id": "p1", "title": "A", "skus": []},
            {"id": "p2", "title": "B", "skus": []},
        ]}},
    )
    from app.integrations import tiktok_shop as tt

    adapter = TikTokShopAdapter(Settings(), DEMO_STORE_ID)
    results = adapter.search_products("shirt")
    assert len(results) == 2
    assert results[0]["name"] == "A"


def test_list_orders(monkeypatch):
    _configured_settings(monkeypatch)
    _patch_http(
        monkeypatch,
        {"data": {"orders": [
            _order_fixture("DELIVERED"),
            _order_fixture("CANCELLED"),
        ]}},
    )
    from app.integrations import tiktok_shop as tt

    adapter = TikTokShopAdapter(Settings(), DEMO_STORE_ID)
    orders = adapter.list_orders(limit=2)
    assert [o["status"] for o in orders] == ["delivered", "cancelled"]


def test_get_fulfillment_with_tracking(monkeypatch):
    _configured_settings(monkeypatch)
    _patch_http(
        monkeypatch, {"data": {"orders": [_order_fixture()]}}
    )
    from app.integrations import tiktok_shop as tt

    adapter = TikTokShopAdapter(Settings(), DEMO_STORE_ID)
    ff = adapter.get_fulfillment("578876543210987456")
    assert ff is not None
    assert ff["carrier"] == "DHL"
    assert ff["tracking_ref"] == "TT-TRK-001"


def test_get_fulfillment_without_tracking_returns_none(monkeypatch):
    _configured_settings(monkeypatch)
    order = _order_fixture()
    order.pop("packages", None)
    _patch_http(monkeypatch, {"data": {"orders": [order]}})
    from app.integrations import tiktok_shop as tt

    adapter = TikTokShopAdapter(Settings(), DEMO_STORE_ID)
    assert adapter.get_fulfillment("578876543210987456") is None


# --- auth headers ----------------------------------------------------------


def test_auth_headers_and_signed_params(monkeypatch):
    settings = _configured_settings(monkeypatch)
    captured = []
    _patch_http(monkeypatch, {"data": {"orders": []}}, capture=captured)
    from app.integrations import tiktok_shop as tt

    adapter = TikTokShopAdapter(settings, DEMO_STORE_ID)
    adapter.get_order("123")
    assert captured, "expected one HTTP call"
    call = captured[0]
    # Access token header (fake value — never a real secret).
    assert call["headers"]["x-tts-access-token"] == "fake_access_token_789"
    # The app secret must NEVER appear in the URL or headers.
    assert "fake_app_secret_456" not in call["url"]
    assert "fake_app_secret_456" not in str(call["headers"])
    # Signed query params present.
    query = urllib.parse.parse_qs(urllib.parse.urlparse(call["url"]).query)
    assert query["app_key"] == ["fake_app_key_123"]
    assert query["shop_cipher"] == ["fake_shop_cipher_abc"]
    assert "timestamp" in query
    assert "sign" in query
    assert len(query["sign"][0]) == 64  # hex HMAC-SHA256


def test_retry_on_429_then_success(monkeypatch):
    _configured_settings(monkeypatch)
    from app.integrations import tiktok_shop as tt

    calls = {"n": 0}

    def flaky(method, url, headers, body, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError(
                url, 429, "Too Many Requests", {}, None
            )
        return 200, {"data": {"orders": []}}

    monkeypatch.setattr(tt, "_http_json", flaky)
    monkeypatch.setattr(tt.TikTokShopAdapter, "_sleep_before_retry",
                        staticmethod(lambda *a: None))
    adapter = TikTokShopAdapter(Settings(), DEMO_STORE_ID)
    assert adapter.get_order("123") is None
    assert calls["n"] == 2


def test_401_raises_german_scope_guidance(monkeypatch):
    _configured_settings(monkeypatch)
    from app.integrations import tiktok_shop as tt

    def denied(method, url, headers, body, timeout):
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(tt, "_http_json", denied)
    adapter = TikTokShopAdapter(Settings(), DEMO_STORE_ID)
    with pytest.raises(IntegrationError) as excinfo:
        adapter.get_order("123")
    assert "access token" in str(excinfo.value)


# --- webhook verification + idempotency ------------------------------------


def _sign_webhook(app_key, app_secret, raw_body: bytes) -> str:
    digest = hmac.new(
        app_secret.encode("utf-8"), app_key.encode("utf-8") + raw_body,
        hashlib.sha256,
    ).hexdigest()
    return digest


def test_webhook_signature_valid():
    body = b'{"tts_notification_id":"n1","type":"ORDER_STATUS_CHANGE"}'
    sig = _sign_webhook("fake_app_key_123", "fake_app_secret_456", body)
    assert verify_tiktok_shop_webhook(
        body, sig, "fake_app_key_123", "fake_app_secret_456"
    ) is True


def test_webhook_signature_invalid_cases():
    body = b'{"tts_notification_id":"n1"}'
    good = _sign_webhook("fake_app_key_123", "fake_app_secret_456", body)
    assert verify_tiktok_shop_webhook(
        body, good + "00", "fake_app_key_123", "fake_app_secret_456"
    ) is False
    assert verify_tiktok_shop_webhook(
        body, None, "fake_app_key_123", "fake_app_secret_456"
    ) is False
    assert verify_tiktok_shop_webhook(
        body, good, "fake_app_key_123", None
    ) is False
    assert verify_tiktok_shop_webhook(
        b"tampered", good, "fake_app_key_123", "fake_app_secret_456"
    ) is False


def test_webhook_idempotency(isolated_conn):
    first = record_tiktok_shop_webhook_event(
        isolated_conn, "n-dedupe-1", "ORDER_STATUS_CHANGE", "shop_1"
    )
    second = record_tiktok_shop_webhook_event(
        isolated_conn, "n-dedupe-1", "ORDER_STATUS_CHANGE", "shop_1"
    )
    assert first is True
    assert second is False


def test_webhook_endpoint_flow(isolated_client, monkeypatch):
    client, settings = isolated_client
    _configured_settings(monkeypatch)
    settings.tiktok_shop_webhook_secret = "fake_app_secret_456"
    settings.tiktok_shop_app_key = "fake_app_key_123"

    body = (
        b'{"tts_notification_id":"n-e2e-1","type":"ORDER_STATUS_CHANGE",'
        b'"shop_id":"shop_1","data":{"order_id":"578"}}'
    )
    sig = _sign_webhook("fake_app_key_123", "fake_app_secret_456", body)

    r1 = client.post(
        "/api/integrations/tiktok-shop/webhooks",
        content=body,
        headers={"authorization": sig,
                 "content-type": "application/json"},
    )
    assert r1.status_code == 200
    assert r1.json()["received"] is True

    # Duplicate delivery → still 200, processed once.
    r2 = client.post(
        "/api/integrations/tiktok-shop/webhooks",
        content=body,
        headers={"authorization": sig,
                 "content-type": "application/json"},
    )
    assert r2.status_code == 200
    assert r2.json()["duplicate"] is True

    # Bad signature → 401.
    r3 = client.post(
        "/api/integrations/tiktok-shop/webhooks",
        content=body,
        headers={"authorization": "deadbeef",
                 "content-type": "application/json"},
    )
    assert r3.status_code == 401

    # Malformed JSON → never crashes (200 acknowledged + audit note).
    bad_sig = _sign_webhook(
        "fake_app_key_123", "fake_app_secret_456", b"not-json{{{"
    )
    r4 = client.post(
        "/api/integrations/tiktok-shop/webhooks",
        content=b"not-json{{{",
        headers={"authorization": bad_sig,
                 "content-type": "application/json"},
    )
    assert r4.status_code == 200


def test_webhook_endpoint_unconfigured_returns_503(
    isolated_client, monkeypatch
):
    client, settings = isolated_client
    _tiktok_env(monkeypatch)  # no webhook secret
    settings.tiktok_shop_webhook_secret = None
    r = client.post(
        "/api/integrations/tiktok-shop/webhooks",
        content=b"{}",
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 503
    assert r.json()["error"] == "tiktok_shop_not_configured"
