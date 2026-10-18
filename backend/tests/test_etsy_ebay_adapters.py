"""Adapter tests: real EtsyAdapter + EbayAdapter selection and mapping.

All offline: ``_http_get_json`` / ``_http_post_form`` are monkeypatched —
no real network calls, no real Etsy/eBay shops, no real credentials.
Covers (per adapter):

- factory: STORE_BACKEND=etsy/ebay selects the real adapter when
  configured, falls back to demo (warning) when env vars miss;
- env vars: English IntegrationConfigError when unconfigured;
- order/product mapping incl. the documented status tables; not-found
  handling (empty results, 404, blank/non-numeric ref);
- auth header formation (fake credentials only);
- eBay: client-credentials token flow (mocked) + direct token;
- retry behavior on HTTP 429.

Isolated temp DB per test module run (dependency_overrides).
"""

from __future__ import annotations

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
    get_store_integration,
)
from app.integrations.etsy import EtsyAdapter
from app.integrations.ebay import EbayAdapter


# --- fixtures ------------------------------------------------------------


@pytest.fixture()
def isolated_db(tmp_path):
    db_path = tmp_path / "etsy_ebay.db"
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


def _clean_env(monkeypatch, *vars):
    for var in vars:
        monkeypatch.delenv(var, raising=False)


ETSY_VARS = ("ETSY_API_KEY", "ETSY_SHOP_ID", "ETSY_OAUTH_TOKEN")
EBAY_VARS = (
    "EBAY_CLIENT_ID",
    "EBAY_CLIENT_SECRET",
    "EBAY_MARKETPLACE_ID",
    "EBAY_OAUTH_TOKEN",
)


def _etsy_settings(monkeypatch, **overrides):
    _clean_env(monkeypatch, *ETSY_VARS)
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)
    return Settings()


def _ebay_settings(monkeypatch, **overrides):
    _clean_env(monkeypatch, *EBAY_VARS)
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)
    return Settings()


def _configured_etsy(monkeypatch):
    settings = _etsy_settings(
        monkeypatch,
        ETSY_API_KEY="etsy_fake_key",
        ETSY_SHOP_ID="1234567",
        ETSY_OAUTH_TOKEN="etsy_fake_oauth",
    )
    return EtsyAdapter(settings, DEMO_STORE_ID)


def _configured_ebay(monkeypatch):
    settings = _ebay_settings(
        monkeypatch,
        EBAY_CLIENT_ID="ebay_fake_id",
        EBAY_CLIENT_SECRET="ebay_fake_secret",
        EBAY_OAUTH_TOKEN="ebay_fake_token",
    )
    return EbayAdapter(settings, DEMO_STORE_ID)


# --- Etsy: factory selection + config ------------------------------------


def test_factory_etsy_falls_back_to_demo_when_unconfigured(
    monkeypatch, isolated_conn, test_settings
):
    _etsy_settings(monkeypatch)
    test_settings.store_backend = "etsy"
    adapter = get_store_integration(test_settings, isolated_conn, DEMO_STORE_ID)
    assert isinstance(adapter, DemoStoreAdapter)
    assert adapter.backend_name == "demo"


def test_factory_etsy_selects_real_adapter_when_configured(
    monkeypatch, isolated_conn, test_settings
):
    _etsy_settings(monkeypatch)
    test_settings.store_backend = "etsy"
    test_settings.etsy_api_key = "etsy_fake_key"
    test_settings.etsy_shop_id = "1234567"
    adapter = get_store_integration(test_settings, isolated_conn, DEMO_STORE_ID)
    assert isinstance(adapter, EtsyAdapter)
    assert adapter.backend_name == "etsy"


def test_etsy_missing_env_raises_german_error(monkeypatch):
    settings = _etsy_settings(monkeypatch)
    with pytest.raises(IntegrationConfigError) as excinfo:
        EtsyAdapter(settings, DEMO_STORE_ID)
    message = str(excinfo.value)
    assert "not configured" in message
    assert "ETSY_API_KEY" in message
    assert "ETSY_SHOP_ID" in message
    assert "docs/ETSY-ADAPTER.md" in message


# --- Etsy: order mapping -------------------------------------------------


def _fake_receipt(**overrides):
    receipt = {
        "receipt_id": 1234567890,
        "name": "Order #1234567890",
        "total_price": {"amount": 3499, "divisor": 100, "currency_code": "EUR"},
        "was_paid": True,
        "was_shipped": True,
        "create_timestamp": 1760000000,
    }
    receipt.update(overrides)
    return receipt


def test_etsy_get_order_found(monkeypatch):
    import app.integrations.etsy as etsy_mod

    adapter = _configured_etsy(monkeypatch)
    calls: list[str] = []

    def fake(url, headers, timeout):
        calls.append(url)
        assert headers["x-api-key"] == "etsy_fake_key"
        assert headers["Authorization"] == "Bearer etsy_fake_oauth"
        assert "/shops/1234567/receipts/1234567890" in url
        return 200, _fake_receipt()

    monkeypatch.setattr(etsy_mod, "_http_get_json", fake)
    order = adapter.get_order("#1234567890")
    assert order is not None
    assert order["order_ref"] == "1234567890"
    assert order["status"] == "shipped"
    assert order["total_cents"] == 3499
    assert order["currency"] == "EUR"
    assert order["carrier"] is None  # honest: no tracking on receipts
    assert order["eta_date"] is None
    assert calls, "must go through the HTTP layer (mocked)"


def test_etsy_get_order_not_found_404(monkeypatch):
    import app.integrations.etsy as etsy_mod

    adapter = _configured_etsy(monkeypatch)

    def fake_404(url, headers, timeout):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(etsy_mod, "_http_get_json", fake_404)
    assert adapter.get_order("9999999999") is None


def test_etsy_get_order_rejects_non_numeric_ref(monkeypatch):
    import app.integrations.etsy as etsy_mod

    adapter = _configured_etsy(monkeypatch)
    calls: list[str] = []

    def fake(url, headers, timeout):
        calls.append(url)
        return 200, {}

    monkeypatch.setattr(etsy_mod, "_http_get_json", fake)
    assert adapter.get_order("ABC-123") is None
    assert adapter.get_order(None) is None
    assert adapter.get_order("   ") is None
    assert calls == [], "non-numeric refs must never hit the network"


@pytest.mark.parametrize(
    "flags,expected",
    [
        ({"was_shipped": True, "was_paid": True}, "shipped"),
        ({"was_shipped": False, "was_paid": True}, "processing"),
        ({"was_shipped": False, "was_paid": False}, "processing"),
        ({}, "processing"),  # missing flags → safe default
    ],
)
def test_etsy_status_mapping(flags, expected):
    receipt = _fake_receipt(**flags)
    if not flags:
        # missing flags entirely (not just False) -> safe default
        receipt.pop("was_shipped", None)
        receipt.pop("was_paid", None)
    assert EtsyAdapter._map_status(receipt) == expected
    assert EtsyAdapter._map_receipt(receipt)["status"] == expected


def test_etsy_list_orders(monkeypatch):
    import app.integrations.etsy as etsy_mod

    adapter = _configured_etsy(monkeypatch)

    def fake(url, headers, timeout):
        assert "limit=2" in url
        return 200, {"count": 2, "results": [_fake_receipt(), _fake_receipt()]}

    monkeypatch.setattr(etsy_mod, "_http_get_json", fake)
    orders = adapter.list_orders(limit=2)
    assert len(orders) == 2
    assert all(o["order_ref"] == "1234567890" for o in orders)


def test_etsy_get_fulfillment_always_none(monkeypatch):
    # Etsy receipts carry no tracking — honest None, no HTTP call needed.
    adapter = _configured_etsy(monkeypatch)
    assert adapter.get_fulfillment("1234567890") is None


# --- Etsy: product mapping -----------------------------------------------


def _fake_listing(**overrides):
    listing = {
        "listing_id": 987654321,
        "title": "AquaPure 750",
        "description": "<p>Trinkflasche aus Edelstahl</p>",
        "price": {"amount": 3499, "divisor": 100, "currency_code": "EUR"},
    }
    listing.update(overrides)
    return listing


def test_etsy_get_product_by_id(monkeypatch):
    import app.integrations.etsy as etsy_mod

    adapter = _configured_etsy(monkeypatch)

    def fake(url, headers, timeout):
        assert "/listings/987654321" in url
        return 200, _fake_listing()

    monkeypatch.setattr(etsy_mod, "_http_get_json", fake)
    product = adapter.get_product("987654321")
    assert product is not None
    assert product["name"] == "AquaPure 750"
    assert product["description"] == "Trinkflasche aus Edelstahl"
    assert product["sku"] is None  # honest: SKU lives in inventory
    assert product["attributes_json"] == "{}"


def test_etsy_search_products(monkeypatch):
    import app.integrations.etsy as etsy_mod

    adapter = _configured_etsy(monkeypatch)

    def fake(url, headers, timeout):
        assert "shop_id=1234567" in url
        return 200, {"count": 1, "results": [_fake_listing()]}

    monkeypatch.setattr(etsy_mod, "_http_get_json", fake)
    results = adapter.search_products("AquaPure")
    assert len(results) == 1
    assert results[0]["etsy_listing_id"] == 987654321


def test_etsy_401_raises_german_guidance(monkeypatch):
    import app.integrations.etsy as etsy_mod

    adapter = _configured_etsy(monkeypatch)

    def fake_401(url, headers, timeout):
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(etsy_mod, "_http_get_json", fake_401)
    with pytest.raises(IntegrationError) as excinfo:
        adapter.get_order("1234567890")
    assert "ETSY_OAUTH_TOKEN" in str(excinfo.value)


# --- eBay: factory selection + config ------------------------------------


def test_factory_ebay_falls_back_to_demo_when_unconfigured(
    monkeypatch, isolated_conn, test_settings
):
    _ebay_settings(monkeypatch)
    test_settings.store_backend = "ebay"
    adapter = get_store_integration(test_settings, isolated_conn, DEMO_STORE_ID)
    assert isinstance(adapter, DemoStoreAdapter)
    assert adapter.backend_name == "demo"


def test_factory_ebay_selects_real_adapter_when_configured(
    monkeypatch, isolated_conn, test_settings
):
    _ebay_settings(monkeypatch)
    test_settings.store_backend = "ebay"
    test_settings.ebay_client_id = "ebay_fake_id"
    test_settings.ebay_client_secret = "ebay_fake_secret"
    adapter = get_store_integration(test_settings, isolated_conn, DEMO_STORE_ID)
    assert isinstance(adapter, EbayAdapter)
    assert adapter.backend_name == "ebay"


def test_ebay_missing_env_raises_german_error(monkeypatch):
    settings = _ebay_settings(monkeypatch)
    with pytest.raises(IntegrationConfigError) as excinfo:
        EbayAdapter(settings, DEMO_STORE_ID)
    message = str(excinfo.value)
    assert "not configured" in message
    assert "EBAY_CLIENT_ID" in message
    assert "EBAY_CLIENT_SECRET" in message
    assert "docs/EBAY-ADAPTER.md" in message


def test_ebay_marketplace_defaults_to_de(monkeypatch):
    adapter = _configured_ebay(monkeypatch)
    assert adapter._marketplace_id == "EBAY_DE"


# --- eBay: auth ----------------------------------------------------------


def test_ebay_direct_token_used_without_token_flow(monkeypatch):
    import app.integrations.ebay as ebay_mod

    adapter = _configured_ebay(monkeypatch)
    calls: list[str] = []

    def fake_post(url, headers, body, timeout):
        calls.append(url)
        raise AssertionError("token endpoint must not be called")

    monkeypatch.setattr(ebay_mod, "_http_post_form", fake_post)
    assert adapter._get_token() == "ebay_fake_token"
    assert calls == []


def test_ebay_client_credentials_flow(monkeypatch):
    import app.integrations.ebay as ebay_mod

    settings = _ebay_settings(
        monkeypatch,
        EBAY_CLIENT_ID="ebay_fake_id",
        EBAY_CLIENT_SECRET="ebay_fake_secret",
    )
    adapter = EbayAdapter(settings, DEMO_STORE_ID)
    seen: dict[str, str] = {}

    def fake_post(url, headers, body, timeout):
        seen["url"] = url
        seen["auth"] = headers["Authorization"]
        seen["body"] = body
        return 200, {"access_token": "ebay_flow_token", "expires_in": 7200}

    monkeypatch.setattr(ebay_mod, "_http_post_form", fake_post)
    token = adapter._get_token()
    assert token == "ebay_flow_token"
    assert "identity/v1/oauth2/token" in seen["url"]
    assert seen["auth"].startswith("Basic ")
    assert "grant_type=client_credentials" in seen["body"]
    # Second call uses the cache — no new HTTP.
    assert adapter._get_token() == "ebay_flow_token"


def test_ebay_api_headers_carry_bearer_and_marketplace(monkeypatch):
    import app.integrations.ebay as ebay_mod

    adapter = _configured_ebay(monkeypatch)

    def fake(url, headers, timeout):
        assert headers["Authorization"] == "Bearer ebay_fake_token"
        assert headers["X-EBAY-C-MARKETPLACE-ID"] == "EBAY_DE"
        return 200, {"orders": []}

    monkeypatch.setattr(ebay_mod, "_http_get_json", fake)
    assert adapter.list_orders() == []


# --- eBay: order mapping -------------------------------------------------


def _fake_ebay_order(**overrides):
    order = {
        "orderId": "12-34567-89012",
        "orderFulfillmentStatus": "FULFILLED",
        "orderPaymentStatus": "PAID",
        "pricingSummary": {"total": {"value": "34.99", "currency": "EUR"}},
        "lineItems": [],
    }
    order.update(overrides)
    return order


def test_ebay_get_order_found(monkeypatch):
    import app.integrations.ebay as ebay_mod

    adapter = _configured_ebay(monkeypatch)
    calls: list[str] = []

    def fake(url, headers, timeout):
        calls.append(url)
        assert "/sell/fulfillment/v1/order/12-34567-89012" in url
        return 200, _fake_ebay_order()

    monkeypatch.setattr(ebay_mod, "_http_get_json", fake)
    order = adapter.get_order("12-34567-89012")
    assert order is not None
    assert order["order_ref"] == "12-34567-89012"
    assert order["status"] == "shipped"
    assert order["total_cents"] == 3499
    assert order["currency"] == "EUR"
    assert order["eta_date"] is None
    assert calls, "must go through the HTTP layer (mocked)"


def test_ebay_get_order_not_found_404(monkeypatch):
    import app.integrations.ebay as ebay_mod

    adapter = _configured_ebay(monkeypatch)

    def fake_404(url, headers, timeout):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(ebay_mod, "_http_get_json", fake_404)
    assert adapter.get_order("99-99999-99999") is None


def test_ebay_get_order_blank_ref_makes_no_http_call(monkeypatch):
    import app.integrations.ebay as ebay_mod

    adapter = _configured_ebay(monkeypatch)
    calls: list[str] = []

    def fake(url, headers, timeout):
        calls.append(url)
        return 200, {}

    monkeypatch.setattr(ebay_mod, "_http_get_json", fake)
    assert adapter.get_order(None) is None
    assert adapter.get_order("   ") is None
    assert calls == []


@pytest.mark.parametrize(
    "fulfillment_status,expected",
    [
        ("FULFILLED", "shipped"),
        ("IN_PROGRESS", "processing"),
        ("NOT_STARTED", "processing"),
        (None, "processing"),
        ("SOMETHING_NEW", "processing"),
    ],
)
def test_ebay_status_mapping(fulfillment_status, expected):
    order = _fake_ebay_order(orderFulfillmentStatus=fulfillment_status)
    assert EbayAdapter._map_order(order)["status"] == expected


def test_ebay_tracking_from_line_items(monkeypatch):
    import app.integrations.ebay as ebay_mod

    adapter = _configured_ebay(monkeypatch)
    order = _fake_ebay_order(
        lineItems=[
            {
                "lineItemFulfillmentDetails": {
                    "shipments": [
                        {
                            "trackingNumber": "EBAY-TRK-1",
                            "shippingCarrierCode": "DHL",
                        }
                    ]
                }
            }
        ]
    )

    def fake(url, headers, timeout):
        return 200, order

    monkeypatch.setattr(ebay_mod, "_http_get_json", fake)
    fulfillment = adapter.get_fulfillment("12-34567-89012")
    assert fulfillment is not None
    assert fulfillment["tracking_ref"] == "EBAY-TRK-1"
    assert fulfillment["carrier"] == "DHL"


def test_ebay_get_fulfillment_none_without_tracking(monkeypatch):
    import app.integrations.ebay as ebay_mod

    adapter = _configured_ebay(monkeypatch)

    def fake(url, headers, timeout):
        return 200, _fake_ebay_order()

    monkeypatch.setattr(ebay_mod, "_http_get_json", fake)
    assert adapter.get_fulfillment("12-34567-89012") is None


def test_ebay_list_orders(monkeypatch):
    import app.integrations.ebay as ebay_mod

    adapter = _configured_ebay(monkeypatch)

    def fake(url, headers, timeout):
        assert "limit=5" in url
        return 200, {"orders": [_fake_ebay_order(), _fake_ebay_order()]}

    monkeypatch.setattr(ebay_mod, "_http_get_json", fake)
    orders = adapter.list_orders(limit=5)
    assert len(orders) == 2
    assert orders[0]["ebay_order_id"] == "12-34567-89012"


# --- eBay: product mapping -----------------------------------------------


def _fake_inventory_item(**overrides):
    item = {
        "sku": "MS-AP750",
        "product": {
            "title": "AquaPure 750",
            "description": "<p>Trinkflasche aus Edelstahl</p>",
        },
    }
    item.update(overrides)
    return item


def test_ebay_get_product_by_sku(monkeypatch):
    import app.integrations.ebay as ebay_mod

    adapter = _configured_ebay(monkeypatch)

    def fake(url, headers, timeout):
        assert "/sell/inventory/v1/inventory_item/MS-AP750" in url
        return 200, _fake_inventory_item()

    monkeypatch.setattr(ebay_mod, "_http_get_json", fake)
    product = adapter.get_product("MS-AP750")
    assert product is not None
    assert product["sku"] == "MS-AP750"
    assert product["name"] == "AquaPure 750"
    assert product["description"] == "Trinkflasche aus Edelstahl"
    assert product["attributes_json"] == "{}"


def test_ebay_search_products_filters_by_query(monkeypatch):
    import app.integrations.ebay as ebay_mod

    adapter = _configured_ebay(monkeypatch)

    def fake(url, headers, timeout):
        assert "/sell/inventory/v1/inventory_item" in url
        return 200, {
            "inventoryItems": [
                _fake_inventory_item(),
                _fake_inventory_item(
                    sku="OTHER-1",
                    product={"title": "Anderes Produkt", "description": ""},
                ),
            ]
        }

    monkeypatch.setattr(ebay_mod, "_http_get_json", fake)
    results = adapter.search_products("aquapure")
    assert len(results) == 1
    assert results[0]["sku"] == "MS-AP750"


def test_ebay_401_raises_german_guidance(monkeypatch):
    import app.integrations.ebay as ebay_mod

    adapter = _configured_ebay(monkeypatch)

    def fake_401(url, headers, timeout):
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(ebay_mod, "_http_get_json", fake_401)
    with pytest.raises(IntegrationError) as excinfo:
        adapter.get_order("12-34567-89012")
    assert "EBAY_CLIENT_ID" in str(excinfo.value)


def test_ebay_retry_on_429(monkeypatch):
    import app.integrations.ebay as ebay_mod

    adapter = _configured_ebay(monkeypatch)
    attempts: list[int] = []

    def fake(url, headers, timeout):
        attempts.append(1)
        if len(attempts) == 1:
            raise urllib.error.HTTPError(url, 429, "Too Many", {}, None)
        return 200, {"orders": []}

    monkeypatch.setattr(ebay_mod, "_http_get_json", fake)
    monkeypatch.setattr(ebay_mod.EbayAdapter, "_sleep_before_retry",
                        staticmethod(lambda *a: None))
    assert adapter.list_orders() == []
    assert len(attempts) == 2
