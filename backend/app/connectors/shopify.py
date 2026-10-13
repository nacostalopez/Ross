"""Shopify connector implementation."""

import base64
import hashlib
import hmac
import os
import re
from datetime import datetime, timezone
from typing import List, Optional
from urllib.parse import parse_qs, urlencode

import requests
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.connectors import HTTP_TIMEOUT, BaseConnector, OAuthToken, require_config
from app.connectors.attribution import extract_click_id

_SHOP = re.compile(r"^[a-z0-9][a-z0-9-]*\.myshopify\.com$")


def normalize_shop(shop: Optional[str]) -> str:
    """The shop's myshopify.com domain, or ValueError. The OAuth callback's `shop` is a query
    parameter that comes from the browser: without this check the token exchange (which carries the
    app's client secret) can be pointed at any host."""
    value = (shop or "").strip().lower()
    if value and "." not in value:
        value = f"{value}.myshopify.com"
    if not _SHOP.match(value):
        raise ValueError("shop must be a *.myshopify.com domain")
    return value


class ShopifySettings(BaseSettings):
    """Shopify-specific configuration."""

    shopify_api_key: str = ""
    shopify_api_secret: str = ""
    # Lands back on the frontend's index.html (plain query params, no
    # dedicated route) — nginx here serves static files with no SPA
    # fallback, so a path like /auth/shopify/callback would 404.
    shopify_redirect_uri: str = "http://localhost:3100/index.html?connector=shopify"
    shopify_scopes: str = "read_orders,write_orders,read_products,write_products"

    model_config = SettingsConfigDict(env_file=".env")


class ShopifyConnector(BaseConnector):
    """Shopify connector for OAuth and webhooks."""

    # Overridable without a deploy of code: SHOPIFY_API_VERSION. Shopify serves a retired version as the
    # oldest supported one, which hides field changes; naming a current version keeps them visible.
    API_VERSION = os.environ.get("SHOPIFY_API_VERSION", "2026-07")
    API_BASE = "https://{shop}.myshopify.com/admin/api/{version}"
    # Bump API_VERSION when Shopify deprecates it and log what changed here.
    BREAKING_CHANGES = [
        "2024-01: initial implementation",
        "2026-07 (2026-10): moved to a supported stable version",
    ]

    def __init__(self, store_id: str, shop_domain: Optional[str] = None):
        super().__init__(store_id, "shopify")
        self.settings = ShopifySettings()
        # e.g., "mystore.myshopify.com"; anything that is not a myshopify.com domain is refused
        self.shop_domain = normalize_shop(shop_domain) if shop_domain else shop_domain

    def get_oauth_url(self, state: str) -> str:
        """Get OAuth authorization URL.

        Args:
            state: CSRF token for security

        Returns:
            Authorization URL to redirect user to
        """
        if not self.shop_domain:
            raise ValueError("shop_domain required for OAuth")
        require_config(
            "shopify",
            SHOPIFY_API_KEY=self.settings.shopify_api_key,
            SHOPIFY_REDIRECT_URI=self.settings.shopify_redirect_uri,
        )

        params = {
            "client_id": self.settings.shopify_api_key,
            "scope": self.settings.shopify_scopes,
            "redirect_uri": self.settings.shopify_redirect_uri,
            "state": state,
        }

        shop = self.shop_domain.replace(".myshopify.com", "")
        return f"https://{shop}.myshopify.com/admin/oauth/authorize?{urlencode(params)}"

    def exchange_auth_code(self, code: str, redirect_uri: str, shop: str) -> OAuthToken:
        """Exchange authorization code for access token.

        Args:
            code: Authorization code from Shopify
            redirect_uri: Redirect URI used in auth request
            shop: Shop domain (e.g., "mystore.myshopify.com")

        Returns:
            OAuthToken with access_token
        """
        require_config(
            "shopify",
            SHOPIFY_API_KEY=self.settings.shopify_api_key,
            SHOPIFY_API_SECRET=self.settings.shopify_api_secret,
        )
        url = f"https://{normalize_shop(shop)}/admin/oauth/access_token"

        payload = {
            "client_id": self.settings.shopify_api_key,
            "client_secret": self.settings.shopify_api_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        }

        response = requests.post(url, json=payload, timeout=HTTP_TIMEOUT)
        response.raise_for_status()

        data = response.json()

        # Shopify doesn't return expiry for access tokens (they don't expire)
        # but refresh tokens expire in 6 months
        return OAuthToken(
            access_token=data["access_token"],
            refresh_token=None,  # Shopify doesn't use refresh tokens
            expires_at=None,  # Access tokens don't expire
        )

    def validate_webhook_signature(self, body: str, signature: str) -> bool:
        """Validate webhook signature.

        Shopify sends X-Shopify-Hmac-SHA256 header with HMAC-SHA256 of the body.

        Args:
            body: Raw webhook body (as string)
            signature: Value from X-Shopify-Hmac-SHA256 header

        Returns:
            True if signature is valid
        """
        secret = self.settings.shopify_api_secret
        # With no secret configured the HMAC key would be empty and anyone could compute a valid
        # signature; a missing header is simply not signed.
        if not secret or not signature:
            return False
        calculated = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
        calculated_b64 = base64.b64encode(calculated).decode()

        return hmac.compare_digest(calculated_b64.encode(), signature.encode())

    def process_webhook(self, event_type: str, data: dict) -> dict:
        """Process webhook event.

        Args:
            event_type: Shopify event type (e.g., "orders/create")
            data: Webhook payload

        Returns:
            Standardized data for ingestion
        """
        if event_type == "orders/create" or event_type == "orders/updated":
            return self._process_order_webhook(data)
        else:
            raise ValueError(f"Unsupported event type: {event_type}")

    def _process_order_webhook(self, shopify_order: dict) -> dict:
        """Convert Shopify order to standardized format.

        Args:
            shopify_order: Order object from Shopify webhook

        Returns:
            Standardized order data
        """
        # Calculate net profit: revenue - cogs - shipping - fees
        gross_amount = float(shopify_order.get("total_price", 0))
        discounts = float(shopify_order.get("total_discounts", 0))
        shipping_fee = sum(float(line.get("price", 0)) for line in shopify_order.get("shipping_lines", []))

        # Attempt to extract gateway fee from transactions
        gateway_fee = 0
        for transaction in shopify_order.get("transactions", []):
            if transaction.get("kind") == "capture" and transaction.get("gateway_fee"):
                gateway_fee += float(transaction.get("gateway_fee", 0))

        # Try to calculate COGS from line items
        cogs_total = 0
        for line_item in shopify_order.get("line_items", []):
            # This requires product data; we'll fetch it separately
            # For now, assume COGS is provided or we'll calculate it
            pass

        net_profit = gross_amount - discounts - shipping_fee - gateway_fee - cogs_total

        # Extract UTM/click-id parameters from note (this dev environment's
        # stand-in for Shopify's note_attributes/landing_site, since no real
        # store is connected yet to see their actual shape).
        note_params = parse_qs(shopify_order.get("note") or "")
        utm_source = note_params.get("utm_source", [None])[0]
        utm_campaign = note_params.get("utm_campaign", [None])[0]
        utm_medium = note_params.get("utm_medium", [None])[0]
        utm_content = note_params.get("utm_content", [None])[0]
        click_id = extract_click_id(
            {"fbclid": note_params.get("fbclid", [None])[0], "gclid": note_params.get("gclid", [None])[0]}
        )
        landing_url = shopify_order.get("landing_site")

        customer = shopify_order.get("customer") or {}

        return {
            "order_id": str(shopify_order["id"]),
            "time": shopify_order.get("created_at", datetime.now(timezone.utc).isoformat()),
            "gross_amount": gross_amount,
            "discounts": discounts,
            "shipping_fee": shipping_fee,
            "payment_gateway_fee": gateway_fee,
            "cogs_total": cogs_total,
            "net_profit": net_profit,
            "currency": shopify_order.get("currency", "USD"),
            "attribution_utm_source": utm_source,
            "attribution_utm_campaign": utm_campaign,
            "utm_medium": utm_medium,
            "utm_content": utm_content,
            "click_id": click_id,
            "landing_url": landing_url,
            "customer_email": customer.get("email"),
            "customer_phone": customer.get("phone"),
            "external_customer_id": str(customer["id"]) if customer.get("id") else None,
        }

    @staticmethod
    def is_cancelled(shopify_order: dict) -> bool:
        """A cancelled order stopped being a sale; Shopify keeps it, with cancelled_at set."""
        return bool(shopify_order.get("cancelled_at"))

    def fetch_orders(self, access_token: str, start_date: datetime, end_date: datetime) -> List[dict]:
        """All of the shop's orders created in [start_date, end_date], raw (not converted).

        status=any includes cancelled and archived (closed) orders, so a
        backfill can also remove the ones that were cancelled. Without the
        read_all_orders scope Shopify only serves the last 60 days.
        """
        if not self.shop_domain:
            raise ValueError("shop_domain required for API calls")

        url = f"{self.API_BASE.format(shop=self.shop_domain.replace('.myshopify.com', ''), version=self.API_VERSION)}/orders.json"
        headers = {
            "X-Shopify-Access-Token": access_token,
            "Content-Type": "application/json",
        }
        params = {
            "status": "any",
            "limit": 250,
            "created_at_min": start_date.isoformat(),
            "created_at_max": end_date.isoformat(),
        }

        orders = []
        while url:
            response = requests.get(url, headers=headers, params=params, timeout=HTTP_TIMEOUT)
            response.raise_for_status()
            orders.extend(response.json().get("orders", []))

            # Cursor pagination: the next page's URL (with its page_info) is in
            # the Link header, and it must be requested without the filters.
            link_header = response.headers.get("Link", "")
            url = None
            if 'rel="next"' in link_header:
                next_url = link_header.split('rel="next"')[0].split(",")[-1].strip().rstrip(";").strip()
                if next_url.startswith("<") and next_url.endswith(">"):
                    url = next_url[1:-1]
            params = {}

        return orders

    def fetch_historical_data(self, start_date: datetime, end_date: datetime, access_token: str):
        """Historical orders in standardized format, cancelled ones left out."""
        return [
            self._process_order_webhook(o)
            for o in self.fetch_orders(access_token, start_date, end_date)
            if not self.is_cancelled(o)
        ]
