"""Shopify connector implementation."""

import base64
import hashlib
import hmac
from datetime import datetime
from typing import Optional
from urllib.parse import parse_qs, urlencode

import requests
from pydantic_settings import BaseSettings

from app.connectors import BaseConnector, OAuthToken
from app.connectors.attribution import extract_click_id


class ShopifySettings(BaseSettings):
    """Shopify-specific configuration."""

    shopify_api_key: str = ""
    shopify_api_secret: str = ""
    # Lands back on the frontend's index.html (plain query params, no
    # dedicated route) — nginx here serves static files with no SPA
    # fallback, so a path like /auth/shopify/callback would 404.
    shopify_redirect_uri: str = "http://localhost:3100/index.html?connector=shopify"
    shopify_scopes: str = "read_orders,write_orders,read_products,write_products"

    class Config:
        env_file = ".env"


class ShopifyConnector(BaseConnector):
    """Shopify connector for OAuth and webhooks."""

    API_VERSION = "2024-01"
    API_BASE = "https://{shop}.myshopify.com/admin/api/{version}"
    # Bump API_VERSION when Shopify deprecates it and log what changed here.
    BREAKING_CHANGES = [
        "2024-01: initial implementation",
    ]

    def __init__(self, store_id: str, shop_domain: Optional[str] = None):
        super().__init__(store_id, "shopify")
        self.settings = ShopifySettings()
        self.shop_domain = shop_domain  # e.g., "mystore.myshopify.com"

    def get_oauth_url(self, state: str) -> str:
        """Get OAuth authorization URL.

        Args:
            state: CSRF token for security

        Returns:
            Authorization URL to redirect user to
        """
        if not self.shop_domain:
            raise ValueError("shop_domain required for OAuth")

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
        url = f"https://{shop}/admin/oauth/access_token"

        payload = {
            "client_id": self.settings.shopify_api_key,
            "client_secret": self.settings.shopify_api_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        }

        response = requests.post(url, json=payload)
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
        calculated = hmac.new(
            self.settings.shopify_api_secret.encode(),
            body.encode(),
            hashlib.sha256,
        ).digest()
        calculated_b64 = base64.b64encode(calculated).decode()

        return hmac.compare_digest(calculated_b64, signature)

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
            "time": shopify_order.get("created_at", datetime.utcnow().isoformat()),
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

    def fetch_historical_data(self, start_date: datetime, end_date: datetime, access_token: str):
        """Fetch historical orders from Shopify.

        Args:
            start_date: Start date for fetching
            end_date: End date for fetching
            access_token: Shopify API access token

        Returns:
            List of orders in standardized format
        """
        if not self.shop_domain:
            raise ValueError("shop_domain required for API calls")

        orders = []
        url = f"{self.API_BASE.format(shop=self.shop_domain.replace('.myshopify.com', ''), version=self.API_VERSION)}/orders.json"

        headers = {
            "X-Shopify-Access-Token": access_token,
            "Content-Type": "application/json",
        }

        # Shopify API pagination
        params = {
            "status": "any",
            "limit": 250,
            "created_at_min": start_date.isoformat(),
            "created_at_max": end_date.isoformat(),
        }

        while url:
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()

            data = response.json()

            for shopify_order in data.get("orders", []):
                orders.append(self._process_order_webhook(shopify_order))

            # Check for next page (Shopify uses Link header for pagination)
            link_header = response.headers.get("Link", "")
            url = None
            if 'rel="next"' in link_header:
                # Extract next URL from Link header
                next_url = link_header.split('rel="next"')[0].split(",")[-1].strip()
                if next_url.startswith("<") and next_url.endswith(">"):
                    url = next_url[1:-1]

            params = {}  # Clear params for subsequent requests

        return orders
