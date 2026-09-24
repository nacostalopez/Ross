"""Tiendanube connector implementation."""

import base64
import hashlib
import hmac
from datetime import datetime
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from pydantic_settings import BaseSettings

from app.connectors import BaseConnector, OAuthToken
from app.connectors.attribution import extract_click_id


class TiendanubeSettings(BaseSettings):
    """Tiendanube-specific configuration."""

    tiendanube_client_id: str = ""
    tiendanube_client_secret: str = ""
    tiendanube_redirect_uri: str = "http://localhost:3100/auth/tiendanube/callback"
    tiendanube_scopes: str = "read_orders,read_products"

    class Config:
        env_file = ".env"


class TiendanubeConnector(BaseConnector):
    """Tiendanube connector for OAuth and webhooks."""

    API_VERSION = "v1"
    API_BASE = "https://api.tiendanube.com/{version}/{store_id}"
    # Bump API_VERSION when Tiendanube deprecates it and log what changed here.
    BREAKING_CHANGES = [
        "v1: initial implementation",
    ]

    def __init__(self, store_id: str, tn_store_id: Optional[str] = None):
        super().__init__(store_id, "tiendanube")
        self.settings = TiendanubeSettings()
        self.tn_store_id = tn_store_id  # Tiendanube's own numeric store id (their "user_id")

    def get_oauth_url(self, state: str) -> str:
        """Get OAuth authorization URL.

        Tiendanube's app-install flow doesn't take redirect_uri/scope at
        authorize time (those are declared in the partner portal) — only the
        client_id (app id) and a CSRF state token.

        Args:
            state: CSRF token for security

        Returns:
            Authorization URL to redirect user to
        """
        params = {"state": state}
        return f"https://www.tiendanube.com/apps/{self.settings.tiendanube_client_id}/authorize?{urlencode(params)}"

    def exchange_auth_code(self, code: str, redirect_uri: str) -> OAuthToken:
        """Exchange authorization code for access token.

        Args:
            code: Authorization code from Tiendanube
            redirect_uri: Redirect URI used in auth request (unused by Tiendanube's
                token endpoint, kept for BaseConnector signature parity)

        Returns:
            OAuthToken with access_token; also sets self.tn_store_id from the
            response's user_id, since every later API call needs it.
        """
        url = "https://www.tiendanube.com/apps/authorize/token"

        payload = {
            "client_id": self.settings.tiendanube_client_id,
            "client_secret": self.settings.tiendanube_client_secret,
            "grant_type": "authorization_code",
            "code": code,
        }

        response = requests.post(url, json=payload)
        response.raise_for_status()

        data = response.json()
        self.tn_store_id = str(data["user_id"])

        # Tiendanube access tokens don't expire.
        return OAuthToken(
            access_token=data["access_token"],
            refresh_token=None,
            expires_at=None,
        )

    def validate_webhook_signature(self, body: str, signature: str) -> bool:
        """Validate webhook signature.

        Tiendanube sends an HMAC-SHA256 of the raw body, base64-encoded,
        computed with the app's client secret — same shape as Shopify's.

        Args:
            body: Raw webhook body (as string)
            signature: Value from the webhook signature header

        Returns:
            True if signature is valid
        """
        calculated = hmac.new(
            self.settings.tiendanube_client_secret.encode(),
            body.encode(),
            hashlib.sha256,
        ).digest()
        calculated_b64 = base64.b64encode(calculated).decode()

        return hmac.compare_digest(calculated_b64, signature)

    def process_webhook(self, event_type: str, data: dict) -> dict:
        """Process webhook event.

        Args:
            event_type: Tiendanube event topic (e.g., "order/created")
            data: Webhook payload

        Returns:
            Standardized data for ingestion
        """
        if event_type in ("order/created", "order/updated"):
            return self._process_order_webhook(data)
        else:
            raise ValueError(f"Unsupported event type: {event_type}")

    def _process_order_webhook(self, tn_order: dict) -> dict:
        """Convert a Tiendanube order to standardized format.

        Args:
            tn_order: Order object from Tiendanube

        Returns:
            Standardized order data
        """
        gross_amount = float(tn_order.get("total", 0))
        discounts = float(tn_order.get("discount", 0))
        shipping_fee = float(tn_order.get("shipping_cost_customer", 0))

        # Tiendanube doesn't expose payment gateway fees or line-item COGS via
        # the orders API — same known limitation as Shopify's connector.
        gateway_fee = 0
        cogs_total = 0

        net_profit = gross_amount - discounts - shipping_fee - gateway_fee - cogs_total

        utm_source = None
        utm_campaign = None
        utm_medium = None
        utm_content = None
        click_id = None
        landing_url = tn_order.get("landing_url") or ""
        if landing_url:
            query = parse_qs(urlparse(landing_url).query)
            utm_source = query.get("utm_source", [None])[0]
            utm_campaign = query.get("utm_campaign", [None])[0]
            utm_medium = query.get("utm_medium", [None])[0]
            utm_content = query.get("utm_content", [None])[0]
            click_id = extract_click_id(
                {"fbclid": query.get("fbclid", [None])[0], "gclid": query.get("gclid", [None])[0]}
            )

        customer = tn_order.get("customer") or {}

        return {
            "order_id": str(tn_order["id"]),
            "time": tn_order.get("created_at", datetime.utcnow().isoformat()),
            "gross_amount": gross_amount,
            "discounts": discounts,
            "shipping_fee": shipping_fee,
            "payment_gateway_fee": gateway_fee,
            "cogs_total": cogs_total,
            "net_profit": net_profit,
            "currency": tn_order.get("currency", "USD"),
            "attribution_utm_source": utm_source,
            "attribution_utm_campaign": utm_campaign,
            "utm_medium": utm_medium,
            "utm_content": utm_content,
            "click_id": click_id,
            "landing_url": landing_url[:2048] if landing_url else None,
            "customer_email": customer.get("email"),
            "customer_phone": customer.get("phone"),
            "external_customer_id": str(customer["id"]) if customer.get("id") else None,
        }

    def fetch_historical_data(self, start_date: datetime, end_date: datetime, access_token: str):
        """Fetch historical orders from Tiendanube.

        Args:
            start_date: Start date for fetching
            end_date: End date for fetching
            access_token: Tiendanube API access token

        Returns:
            List of orders in standardized format
        """
        if not self.tn_store_id:
            raise ValueError("tn_store_id required for API calls")

        url = f"{self.API_BASE.format(version=self.API_VERSION, store_id=self.tn_store_id)}/orders"

        headers = {
            "Authentication": f"bearer {access_token}",
            "Content-Type": "application/json",
            "User-Agent": "Ross (support@escal.app)",
        }

        orders = []
        page = 1
        per_page = 200

        while True:
            params = {
                "page": page,
                "per_page": per_page,
                "created_at_min": start_date.isoformat(),
                "created_at_max": end_date.isoformat(),
            }
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()

            batch = response.json()
            for tn_order in batch:
                orders.append(self._process_order_webhook(tn_order))

            if len(batch) < per_page:
                break
            page += 1

        return orders
