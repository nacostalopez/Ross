"""MercadoPago connector for ad spend tracking."""
import hashlib
import hmac
from datetime import datetime, timedelta
from typing import List, Optional
from urllib.parse import urlencode

import requests
from pydantic_settings import BaseSettings

from app.connectors import BaseConnector, OAuthToken


class MercadoPagoSettings(BaseSettings):
    """MercadoPago-specific configuration."""
    mercadopago_client_id: str = ""
    mercadopago_client_secret: str = ""
    mercadopago_redirect_uri: str = "http://localhost:3100/auth/mercadopago/callback"
    mercadopago_scopes: str = "offline_access read write"

    class Config:
        env_file = ".env"


class MercadoPagoConnector(BaseConnector):
    """MercadoPago connector for OAuth and ad spend tracking.

    Modeled as pull-based only, like MetaConnector/GoogleAdsConnector, even
    though MercadoPago also supports payment webhooks — ad_spend metrics are
    already daily-bucketed (see metrics.py's time_bucket queries), so a
    real-time webhook path isn't needed and would add a second audit table,
    rate limit, and e2e test for no metrics benefit. validate_webhook_signature
    is still implemented correctly (not stubbed) for interface honesty and to
    make a future real-time route a small addition, same as Meta today.
    """

    API_VERSION = "v1"
    API_BASE = "https://api.mercadopago.com"
    # Bump API_VERSION when MercadoPago deprecates it and log what changed here.
    BREAKING_CHANGES = [
        "v1: initial implementation",
    ]

    def __init__(self, store_id: str, seller_id: Optional[str] = None):
        super().__init__(store_id, "mercadopago")
        self.settings = MercadoPagoSettings()
        self.seller_id = seller_id  # MercadoPago collector/user id

    def get_oauth_url(self, state: str) -> str:
        """Get MercadoPago OAuth authorization URL."""
        params = {
            "client_id": self.settings.mercadopago_client_id,
            "response_type": "code",
            "platform_id": "mp",
            "redirect_uri": self.settings.mercadopago_redirect_uri,
            "state": state,
        }
        return f"https://auth.mercadopago.com/authorization?{urlencode(params)}"

    def exchange_auth_code(self, code: str, redirect_uri: str) -> OAuthToken:
        """Exchange authorization code for access token."""
        url = f"{self.API_BASE}/oauth/token"

        payload = {
            "client_id": self.settings.mercadopago_client_id,
            "client_secret": self.settings.mercadopago_client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        }

        response = requests.post(url, json=payload)
        response.raise_for_status()

        data = response.json()
        self.seller_id = str(data.get("user_id", self.seller_id or ""))

        expires_at = None
        if "expires_in" in data:
            expires_at = datetime.utcnow() + timedelta(seconds=data["expires_in"])

        return OAuthToken(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_at=expires_at,
        )

    def refresh_access_token(self, refresh_token: str) -> OAuthToken:
        """Refresh access token using refresh token (MercadoPago tokens expire ~180 days)."""
        url = f"{self.API_BASE}/oauth/token"

        payload = {
            "client_id": self.settings.mercadopago_client_id,
            "client_secret": self.settings.mercadopago_client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }

        response = requests.post(url, json=payload)
        response.raise_for_status()

        data = response.json()

        expires_at = None
        if "expires_in" in data:
            expires_at = datetime.utcnow() + timedelta(seconds=data["expires_in"])

        return OAuthToken(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", refresh_token),
            expires_at=expires_at,
        )

    def validate_webhook_signature(self, body: str, signature: str) -> bool:
        """Validate a MercadoPago webhook notification signature.

        MercadoPago's x-signature header has the form "ts=<ts>,v1=<hash>",
        where <hash> is HMAC-SHA256 (hex) over a manifest string built from
        the x-request-id header, the notification's data.id, and ts. This
        connector isn't wired to a webhook route today (see class docstring),
        so `signature` is passed in as the already-parsed manifest string.

        Args:
            body: The manifest string (id/request-id/ts) to verify, not the
                raw JSON body — MercadoPago's scheme signs the manifest, not
                the payload itself.
            signature: The v1 hash value extracted from x-signature.

        Returns:
            True if signature is valid
        """
        expected = hmac.new(
            self.settings.mercadopago_client_secret.encode(),
            body.encode(),
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(expected, signature)

    def process_webhook(self, event_type: str, data: dict) -> dict:
        """Process webhook event. Not wired to any route today (see class docstring)."""
        if event_type == "payment":
            return {"status": "acknowledged"}
        else:
            raise ValueError(f"Unsupported event type: {event_type}")

    def fetch_ad_spend(
        self,
        access_token: str,
        start_date: datetime,
        end_date: datetime,
        breakdown_by: str = "campaign",
    ) -> List[dict]:
        """Fetch ad spend data from MercadoPago's advertising reporting API.

        Args:
            access_token: MercadoPago API access token
            start_date: Start date for data fetch
            end_date: End date for data fetch
            breakdown_by: "campaign", "adset", or "ad" (kept for parity with
                Meta/Google's signature; MercadoPago's reporting API groups by
                campaign by default)

        Returns:
            List of ad spend records, shaped like Meta/Google's.
        """
        if not self.seller_id:
            raise ValueError("seller_id required for ad spend fetch")

        url = f"{self.API_BASE}/advertising/{self.seller_id}/reports"

        headers = {"Authorization": f"Bearer {access_token}"}
        params = {
            "date_from": start_date.date().isoformat(),
            "date_to": end_date.date().isoformat(),
            "group_by": breakdown_by,
            "limit": 100,
            "offset": 0,
        }

        spend_records = []

        while True:
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()

            data = response.json()
            results = data.get("results", [])

            for result in results:
                record = {
                    "time": datetime.fromisoformat(
                        result.get("date", datetime.utcnow().isoformat())
                    ),
                    "platform": "mercadopago",
                    "campaign_id": str(result.get("campaign_id", "")),
                    "campaign_name": result.get("campaign_name", ""),
                    "adset_id": str(result.get("adset_id", "")),
                    "spend": float(result.get("spend", 0)),
                    "impressions": int(result.get("impressions", 0)),
                    "clicks": int(result.get("clicks", 0)),
                }
                spend_records.append(record)

            if len(results) < params["limit"]:
                break
            params["offset"] += params["limit"]

        return spend_records

    def fetch_historical_data(
        self,
        access_token: str,
        start_date: datetime,
        end_date: datetime,
    ) -> dict:
        """Fetch historical ad spend data."""
        return {
            "spend_records": self.fetch_ad_spend(access_token, start_date, end_date)
        }
