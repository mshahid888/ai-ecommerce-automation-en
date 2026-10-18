"""Store integrations (Phase 9/9b): interface, adapters, factory.

``get_store_integration()`` is the single place that decides which
``StoreIntegration`` the pipeline uses. ``STORE_BACKEND`` (env):
``demo`` (default) → ``DemoStoreAdapter``;
``shopify_simulated`` → ``SimulatedShopifyAdapter``;
``shopify`` → ``ShopifyAdapter`` (real Admin API). When ``shopify``
is selected but ``SHOPIFY_*`` is not configured, the factory logs a
warning and falls back to ``demo`` — the pipeline never
crashes on a missing shop configuration.
``woocommerce`` → ``WooCommerceAdapter`` (real REST API). When
``woocommerce`` is selected but ``WOOCOMMERCE_*`` is not configured,
the factory logs a warning and falls back to ``demo``.
``etsy`` → ``EtsyAdapter`` (real Open API v3);
``ebay`` → ``EbayAdapter`` (real Sell APIs). When a real backend
is selected but not configured, the factory logs a warning
and falls back to ``demo``.
Unknown values fall back to ``demo``.
"""

from __future__ import annotations

import logging
import sqlite3

log = logging.getLogger(__name__)
from typing import TYPE_CHECKING

log = logging.getLogger(__name__)

from .base import (
    IntegrationConfigError,
    IntegrationError,
    StoreIntegration,
)
from .demo import DemoStoreAdapter
from .ebay import EbayAdapter
from .etsy import EtsyAdapter
from .shopify import ShopifyAdapter
from .shopify_simulated import SimulatedShopifyAdapter
from .tiktok_shop import TikTokShopAdapter
from .woocommerce import WooCommerceAdapter
from .webhooks import (
    record_tiktok_shop_webhook_event,
    record_webhook_event,
    record_woocommerce_webhook_event,
    verify_tiktok_shop_webhook,
    verify_webhook_hmac,
    verify_woocommerce_webhook_signature,
)

if TYPE_CHECKING:  # pragma: no cover
    from ..config import Settings

__all__ = [
    "DemoStoreAdapter",
    "EbayAdapter",
    "EtsyAdapter",
    "IntegrationConfigError",
    "IntegrationError",
    "ShopifyAdapter",
    "SimulatedShopifyAdapter",
    "WooCommerceAdapter",
    "StoreIntegration",
    "TikTokShopAdapter",
    "get_store_integration",
    "record_tiktok_shop_webhook_event",
    "record_webhook_event",
    "verify_tiktok_shop_webhook",
    "verify_webhook_hmac",
]

log = logging.getLogger(__name__)


def get_store_integration(
    settings: "Settings", conn: sqlite3.Connection, store_id: str
) -> StoreIntegration:
    """Build the configured store backend for one pipeline run."""
    backend = (settings.store_backend or "demo").strip().lower()
    if backend == "shopify_simulated":
        return SimulatedShopifyAdapter(conn, store_id)
    if backend == "shopify":
        try:
            return ShopifyAdapter(settings, store_id)
        except IntegrationConfigError as exc:
            # "not configured": report honestly, keep serving via demo.
            log.warning(
                "Shopify backend not configured (%s) — falling back "
                "to the demo backend.",
                exc,
            )
            return DemoStoreAdapter(conn, store_id)
    if backend == "etsy":
        try:
            return EtsyAdapter(settings, store_id)
        except IntegrationConfigError as exc:
            # "not configured": report honestly, keep serving via demo.
            log.warning(
                "Etsy backend not configured (%s) — falling back "
                "to the demo backend.",
                exc,
            )
            return DemoStoreAdapter(conn, store_id)
    if backend == "woocommerce":
        try:
            return WooCommerceAdapter(settings, store_id)
        except IntegrationConfigError as exc:
            # "not configured": report honestly, keep serving via demo.
            log.warning(
                "WooCommerce backend not configured (%s) — falling back "
                "to the demo backend.",
                exc,
            )
            return DemoStoreAdapter(conn, store_id)
    if backend == "ebay":
        try:
            return EbayAdapter(settings, store_id)
        except IntegrationConfigError as exc:
            # "not configured": report honestly, keep serving via demo.
            log.warning(
                "eBay backend not configured (%s) — falling back "
                "to the demo backend.",
                exc,
            )
            return DemoStoreAdapter(conn, store_id)
    if backend == "tiktok_shop":
        try:
            return TikTokShopAdapter(settings, store_id)
        except IntegrationConfigError as exc:
            # "not configured": report honestly, keep serving via demo.
            log.warning(
                "TikTok Shop backend not configured (%s) — falling back "
                "to the demo backend.",
                exc,
            )
            return DemoStoreAdapter(conn, store_id)
    # "demo" default; unknown values fall back to demo (never crash prod).
    return DemoStoreAdapter(conn, store_id)
