"""Mercado Pago connector: a merchant's own payments (sales + processing fees).

Mercado Pago doesn't sell advertising (Mercado Ads belongs to Mercado Libre
— see app/connectors/mercadolibre.py), so this connector never touches
ad_spend. What it has is every payment the merchant collected, which is
used two ways depending on the store (see routes/connectors.py's
sync-payments):

- Stores whose platform is "mercadopago" sell directly through it (payment
  links, QR, their own checkout): each approved payment IS an order.
- Any other store already gets its orders from its own platform, so
  ingesting payments too would double-count revenue. There, payments only
  fill in the processing fee on the matching order (Shopify/Tiendanube's
  order APIs don't expose it — payment_gateway_fee is 0 for them otherwise).

Payments that belong to a Mercado Libre sale are always skipped: the
Mercado Libre connector owns those orders, fee included.
"""

import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from urllib.parse import urlencode

import requests
from pydantic_settings import BaseSettings

from app.connectors import BaseConnector, OAuthToken


class MercadoPagoSettings(BaseSettings):
    """Mercado Pago-specific configuration."""

    mercadopago_client_id: str = ""
    mercadopago_client_secret: str = ""
    mercadopago_redirect_uri: str = "http://localhost:3100/index.html?connector=mercadopago"
    # Kept for .env compatibility; Mercado Pago's authorize URL takes no scope
    # (the app's permissions are set in its panel).
    mercadopago_scopes: str = "offline_access read write"
    # The "clave secreta" from Tus integraciones > Webhooks — NOT the
    # client secret. Mercado Pago signs notifications with this one.
    mercadopago_webhook_secret: str = ""

    class Config:
        env_file = ".env"


def build_signature_manifest(data_id: Optional[str], request_id: Optional[str], ts: str) -> str:
    """The string Mercado Pago signs, per its official SDK
    (sdk-nodejs src/utils/webhook): "id:<data.id>;request-id:<x-request-id>;ts:<ts>;",
    with a pair left out entirely when its value is missing."""
    parts = []
    if data_id:
        parts.append(f"id:{data_id}")
    if request_id:
        parts.append(f"request-id:{request_id}")
    parts.append(f"ts:{ts}")
    return ";".join(parts) + ";"


def parse_signature_header(x_signature: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Split "ts=<ts>,v1=<hash>" into (ts, v1_hash)."""
    ts = v1 = None
    for part in (x_signature or "").split(","):
        key, _, value = part.partition("=")
        key, value = key.strip().lower(), value.strip()
        if key == "ts":
            ts = value
        elif key == "v1":
            v1 = value
    return ts, v1


def verify_notification_signature(
    secret: str, x_signature: Optional[str], x_request_id: Optional[str], data_id: Optional[str]
) -> bool:
    """True if a Mercado Pago webhook's x-signature header is genuine."""
    if not secret:
        return False
    ts, received = parse_signature_header(x_signature)
    if not ts or not received:
        return False
    manifest = build_signature_manifest(data_id, x_request_id, ts)
    expected = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)


# Payment statuses that mean money actually changed hands and stayed there.
SALE_STATUSES = {"approved"}
# A payment that was a sale and then stopped being one — its order (if
# this connector created it) must go.
REVERSED_STATUSES = {"refunded", "charged_back", "cancelled"}


class MercadoPagoConnector(BaseConnector):
    """Mercado Pago connector for OAuth and payments."""

    API_VERSION = "v1"
    API_BASE = "https://api.mercadopago.com"
    # Bump API_VERSION when Mercado Pago deprecates it and log what changed here.
    BREAKING_CHANGES = [
        "v1: initial implementation",
        "v1: dropped fetch_ad_spend (/advertising/{id}/reports isn't a Mercado Pago endpoint); payments search instead",
    ]
    PAGE_SIZE = 100

    def __init__(self, store_id: str, seller_id: Optional[str] = None):
        super().__init__(store_id, "mercadopago")
        self.settings = MercadoPagoSettings()
        self.seller_id = seller_id  # Mercado Pago collector/user id

    def get_oauth_url(self, state: str) -> str:
        """Get Mercado Pago OAuth authorization URL."""
        params = {
            "client_id": self.settings.mercadopago_client_id,
            "response_type": "code",
            "platform_id": "mp",
            "redirect_uri": self.settings.mercadopago_redirect_uri,
            "state": state,
        }
        return f"https://auth.mercadopago.com/authorization?{urlencode(params)}"

    def _token_request(self, payload: dict) -> dict:
        response = requests.post(f"{self.API_BASE}/oauth/token", json=payload)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _to_token(data: dict, fallback_refresh: Optional[str] = None) -> OAuthToken:
        expires_at = None
        if "expires_in" in data:
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=data["expires_in"])
        return OAuthToken(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", fallback_refresh),
            expires_at=expires_at,
        )

    def exchange_auth_code(self, code: str, redirect_uri: str) -> OAuthToken:
        """Exchange authorization code for access token; also sets self.seller_id."""
        data = self._token_request(
            {
                "client_id": self.settings.mercadopago_client_id,
                "client_secret": self.settings.mercadopago_client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            }
        )
        self.seller_id = str(data.get("user_id", self.seller_id or ""))
        return self._to_token(data)

    def refresh_access_token(self, refresh_token: str) -> OAuthToken:
        """Refresh access token (Mercado Pago tokens expire after ~180 days)."""
        data = self._token_request(
            {
                "client_id": self.settings.mercadopago_client_id,
                "client_secret": self.settings.mercadopago_client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            }
        )
        return self._to_token(data, fallback_refresh=refresh_token)

    def validate_webhook_signature(self, body: str, signature: str) -> bool:
        """Validate a Mercado Pago notification.

        Mercado Pago signs a manifest built from headers and the query
        string, not the body, so the pieces don't fit this two-argument
        interface cleanly: `body` is the already-built manifest (see
        build_signature_manifest) and `signature` the v1 hash. Routes call
        verify_notification_signature() directly instead.
        """
        if not self.settings.mercadopago_webhook_secret:
            return False
        expected = hmac.new(
            self.settings.mercadopago_webhook_secret.encode(), body.encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def process_webhook(self, event_type: str, data: dict) -> dict:
        """Convert a payment (already re-fetched from the API) to an orders row."""
        if event_type == "payment":
            return self.payment_to_order(data)
        raise ValueError(f"Unsupported event type: {event_type}")

    @staticmethod
    def is_mercadolibre_payment(payment: dict) -> bool:
        """Whether a payment belongs to a Mercado Libre sale (owned by that connector)."""
        order = payment.get("order") or {}
        return order.get("type") == "mercadolibre"

    @staticmethod
    def processing_fee(payment: dict) -> float:
        """What Mercado Pago kept from this payment.

        fee_details lists each fee and who paid it; only the ones charged
        to the merchant (the collector) reduce their margin — a financing
        fee the buyer paid for installments doesn't. Falls back to
        gross minus net received when fee_details is absent.
        """
        fee_details = payment.get("fee_details")
        if fee_details is not None:
            return round(
                sum(float(f.get("amount") or 0) for f in fee_details if f.get("fee_payer", "collector") == "collector"),
                4,
            )
        gross = float(payment.get("transaction_amount") or 0)
        net = (payment.get("transaction_details") or {}).get("net_received_amount")
        return round(max(gross - float(net), 0), 4) if net is not None else 0.0

    def payment_to_order(self, payment: dict) -> dict:
        """Convert a Mercado Pago payment to the standardized orders row.

        order_id is "mp:<payment id>" so it can never collide with an
        order id from another platform in the same store.
        """
        payer = payment.get("payer") or {}
        phone = payer.get("phone") or {}
        phone_str = f"{phone.get('area_code') or ''}{phone.get('number') or ''}".strip() or None
        refunded = float(payment.get("transaction_amount_refunded") or 0)

        return {
            "order_id": f"mp:{payment['id']}",
            "time": payment.get("date_approved") or payment.get("date_created") or datetime.utcnow().isoformat(),
            "gross_amount": float(payment.get("transaction_amount") or 0),
            # A partial refund leaves the payment "approved"; it's money
            # given back, which is what discounts already means on the P&L.
            "discounts": refunded,
            "shipping_fee": 0,
            "payment_gateway_fee": self.processing_fee(payment),
            "cogs_total": 0,
            "currency": payment.get("currency_id") or "ARS",
            "attribution_utm_source": None,
            "attribution_utm_campaign": None,
            "utm_medium": None,
            "utm_content": None,
            "click_id": None,
            "landing_url": None,
            "customer_email": payer.get("email"),
            "customer_phone": phone_str,
            "external_customer_id": f"mp:{payer['id']}" if payer.get("id") else None,
        }

    def fetch_payment(self, access_token: str, payment_id: str) -> dict:
        """GET a single payment."""
        response = requests.get(
            f"{self.API_BASE}/v1/payments/{payment_id}", headers={"Authorization": f"Bearer {access_token}"}
        )
        response.raise_for_status()
        return response.json()

    def fetch_payments(self, access_token: str, start_date: datetime, end_date: datetime) -> List[dict]:
        """Every payment created in [start_date, end_date], raw.

        The search only reaches back 12 months and rejects ranges of 365
        days or more — both are Mercado Pago limits, surfaced as the API's
        own 400 rather than silently clamped, so a backfill request that
        asks for too much says so.
        """
        headers = {"Authorization": f"Bearer {access_token}"}
        fmt = "%Y-%m-%dT%H:%M:%S.000-00:00"
        params = {
            "sort": "date_created",
            "criteria": "asc",
            "range": "date_created",
            "begin_date": start_date.strftime(fmt),
            "end_date": end_date.strftime(fmt),
            "limit": self.PAGE_SIZE,
            "offset": 0,
        }

        payments = []
        while True:
            response = requests.get(f"{self.API_BASE}/v1/payments/search", headers=headers, params=params)
            response.raise_for_status()
            results = response.json().get("results", [])
            payments.extend(results)
            if len(results) < params["limit"]:
                break
            params["offset"] += params["limit"]
        return payments

    def fetch_historical_data(self, start_date: datetime, end_date: datetime, access_token: str) -> List[dict]:
        """Approved, non-Mercado Libre payments as orders rows."""
        return [
            self.payment_to_order(p)
            for p in self.fetch_payments(access_token, start_date, end_date)
            if p.get("status") in SALE_STATUSES and not self.is_mercadolibre_payment(p)
        ]
