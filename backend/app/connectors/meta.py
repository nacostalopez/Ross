"""Meta (Facebook) Ads connector for ad spend tracking."""

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import requests
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.connectors import HTTP_TIMEOUT, BaseConnector, OAuthToken, redact, require_config

logger = logging.getLogger("ross.meta")


def _expiry(data: dict) -> Optional[datetime]:
    """When a token answer says it expires, as a datetime; None if it does not say."""
    seconds = data.get("expires_in")
    return datetime.now(timezone.utc) + timedelta(seconds=int(seconds)) if seconds else None


# Insights fields by level: a campaign row has no ad set or ad id, an ad set row has no ad id.
INSIGHT_FIELDS = {
    "campaign": ["campaign_id", "campaign_name", "spend", "impressions", "clicks", "date_start", "date_stop"],
    "adset": [
        "campaign_id", "campaign_name", "adset_id", "adset_name", "spend", "impressions", "clicks", "date_start",
        "date_stop",
    ],
    "ad": [
        "campaign_id", "campaign_name", "adset_id", "adset_name", "ad_id", "ad_name", "spend", "impressions",
        "clicks", "date_start", "date_stop",
    ],
}


class MetaSettings(BaseSettings):
    """Meta-specific configuration."""

    meta_app_id: str = ""
    meta_app_secret: str = ""
    # Lands back on the frontend's index.html (plain query params, no
    # dedicated route) — nginx here serves static files with no SPA
    # fallback, so a path like /auth/meta/callback would 404.
    meta_redirect_uri: str = "http://localhost:3100/index.html?connector=meta"
    meta_scopes: str = "ads_management,ads_read"

    model_config = SettingsConfigDict(env_file=".env")


class MetaConnector(BaseConnector):
    """Meta (Facebook) Ads connector for OAuth and ad spend tracking."""

    # Overridable without a deploy of code: META_API_VERSION. v19.0 was past Meta's two-year window.
    API_VERSION = os.environ.get("META_API_VERSION", "v25.0")
    API_BASE = f"https://graph.facebook.com/{API_VERSION}"
    # Bump API_VERSION when Meta deprecates it (~2yr cycle) and log what changed here.
    BREAKING_CHANGES = [
        "v19.0: initial implementation",
        "v25.0 (2026-10): moved off the expired v19.0; Insights now asked with level + time_increment=1",
    ]

    def __init__(self, store_id: str, ad_account_id: Optional[str] = None):
        super().__init__(store_id, "meta")
        self.settings = MetaSettings()
        self.ad_account_id = ad_account_id  # e.g., "act_123456789"

    def get_oauth_url(self, state: str) -> str:
        """Get Meta OAuth authorization URL."""
        require_config("meta", META_APP_ID=self.settings.meta_app_id, META_REDIRECT_URI=self.settings.meta_redirect_uri)
        params = {
            "client_id": self.settings.meta_app_id,
            "redirect_uri": self.settings.meta_redirect_uri,
            "scope": self.settings.meta_scopes,
            "response_type": "code",
            "state": state,
        }

        from urllib.parse import urlencode

        return f"https://www.facebook.com/{self.API_VERSION}/dialog/oauth?{urlencode(params)}"

    def exchange_auth_code(self, code: str, redirect_uri: str) -> OAuthToken:
        """Exchange authorization code for access token."""
        require_config("meta", META_APP_ID=self.settings.meta_app_id, META_APP_SECRET=self.settings.meta_app_secret)
        url = f"{self.API_BASE}/oauth/access_token"

        params = {
            "client_id": self.settings.meta_app_id,
            "client_secret": self.settings.meta_app_secret,
            "redirect_uri": redirect_uri,
            "code": code,
        }

        response = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
        response.raise_for_status()

        data = response.json()
        short_lived = OAuthToken(
            access_token=data["access_token"],
            refresh_token=None,  # Meta has no refresh tokens: a long-lived token is renewed by connecting again
            expires_at=_expiry(data),
        )
        return self._long_lived(short_lived)

    def _long_lived(self, token: OAuthToken) -> OAuthToken:
        """Swap the short-lived token (about 2 hours) for a long-lived one (about 60 days).

        If the swap fails the short-lived token is kept, with its real expiry, so the connector
        shows "reconnect" in hours instead of failing without explanation."""
        try:
            response = requests.get(
                f"{self.API_BASE}/oauth/access_token",
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": self.settings.meta_app_id,
                    "client_secret": self.settings.meta_app_secret,
                    "fb_exchange_token": token.access_token,
                },
                timeout=HTTP_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
            return OAuthToken(access_token=data["access_token"], refresh_token=None, expires_at=_expiry(data))
        except Exception as error:
            logger.warning("meta_long_lived_exchange_failed", extra={"error": redact(error)})
            return token

    def validate_webhook_signature(self, body: str, signature: str) -> bool:
        """Validate webhook signature.

        Meta sends X-Hub-Signature header with SHA1 HMAC.
        """
        expected = hmac.new(
            self.settings.meta_app_secret.encode(),
            body.encode(),
            hashlib.sha1,
        ).hexdigest()

        return hmac.compare_digest(f"sha1={expected}", signature)

    def process_webhook(self, event_type: str, data: dict) -> dict:
        """Process webhook event.

        For ads, we typically receive:
        - campaign performance updates
        - billing events
        - ad account changes
        """
        if event_type == "ad_account_update":
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
        """Fetch ad spend data from Meta Ads API.

        Args:
            access_token: Meta API access token
            start_date: Start date for data fetch
            end_date: End date for data fetch
            breakdown_by: "campaign", "adset", or "ad"

        Returns:
            List of ad spend records
        """
        if not self.ad_account_id:
            raise ValueError("ad_account_id required for ad spend fetch")

        url = f"{self.API_BASE}/{self.ad_account_id}/insights"

        if breakdown_by not in INSIGHT_FIELDS:
            raise ValueError(f"breakdown_by must be one of {sorted(INSIGHT_FIELDS)}")

        # `level` picks what each row is (a campaign, an ad set, an ad) and `time_increment=1` makes
        # it one row per day. There is no "campaign" breakdown: asking for one is an API error, and
        # without time_increment the whole range comes back as one row dated at its first day.
        params = {
            "time_range": json.dumps(
                {
                    "since": start_date.date().isoformat(),
                    "until": end_date.date().isoformat(),
                }
            ),
            "fields": ",".join(INSIGHT_FIELDS[breakdown_by]),
            "level": breakdown_by,
            "time_increment": 1,
            "limit": 100,
        }
        # The token travels in a header, not in the URL: an HTTP error quotes the URL.
        headers = {"Authorization": f"Bearer {access_token}"}

        spend_records = []

        while url:
            response = requests.get(url, params=params, headers=headers, timeout=HTTP_TIMEOUT)
            response.raise_for_status()

            data = response.json()

            for insight in data.get("data", []):
                record = {
                    "time": datetime.fromisoformat(insight.get("date_start", datetime.now(timezone.utc).isoformat())),
                    "platform": "meta",
                    "campaign_id": str(insight.get("campaign_id", "")),
                    "campaign_name": insight.get("campaign_name", ""),
                    "adset_id": str(insight.get("adset_id", "")),
                    "spend": float(insight.get("spend", 0)),
                    "impressions": int(insight.get("impressions", 0)),
                    "clicks": int(insight.get("clicks", 0)),
                }
                spend_records.append(record)

            # The last page has cursors too; the way to know there is another is the `next` link.
            url = data.get("paging", {}).get("next")
            params = None

        return spend_records

    def fetch_creative_performance(
        self,
        access_token: str,
        start_date: datetime,
        end_date: datetime,
    ) -> List[dict]:
        """Ad-level (creative) breakdown for the creative_performance table.

        Same Insights endpoint as fetch_ad_spend, requested at ad level
        instead of aggregated to campaign. thumbnail_url isn't fetched yet —
        Meta doesn't return it on Insights; getting it needs a separate
        `/{ad_id}/adcreatives` call per ad, deliberately left for later
        rather than adding N+1 API calls here.
        """
        if not self.ad_account_id:
            raise ValueError("ad_account_id required for creative performance fetch")

        url = f"{self.API_BASE}/{self.ad_account_id}/insights"

        params = {
            "time_range": json.dumps(
                {
                    "since": start_date.date().isoformat(),
                    "until": end_date.date().isoformat(),
                }
            ),
            "fields": ",".join(INSIGHT_FIELDS["ad"]),
            "level": "ad",
            "time_increment": 1,
            "limit": 100,
        }
        headers = {"Authorization": f"Bearer {access_token}"}

        records = []

        while url:
            response = requests.get(url, params=params, headers=headers, timeout=HTTP_TIMEOUT)
            response.raise_for_status()

            data = response.json()

            for insight in data.get("data", []):
                records.append(
                    {
                        "time": datetime.fromisoformat(insight.get("date_start", datetime.now(timezone.utc).isoformat())),
                        "platform": "meta",
                        "campaign_id": str(insight.get("campaign_id", "")),
                        "campaign_name": insight.get("campaign_name", ""),
                        "adset_id": str(insight.get("adset_id", "")),
                        "ad_id": str(insight.get("ad_id", "")),
                        "ad_name": insight.get("ad_name", ""),
                        "thumbnail_url": None,
                        "spend": float(insight.get("spend", 0)),
                        "impressions": int(insight.get("impressions", 0)),
                        "clicks": int(insight.get("clicks", 0)),
                    }
                )

            url = data.get("paging", {}).get("next")
            params = None

        return records

    def send_purchase_event(
        self,
        access_token: str,
        pixel_id: str,
        order_id: str,
        order_time: datetime,
        value: float,
        currency: str,
        email_hash: Optional[str],
        phone_hash: Optional[str],
    ) -> None:
        """Send a server-side Purchase event to the Meta Conversions API.

        em/ph must already be SHA-256 hashes normalized the way Meta expects
        (see app/services/customers.py — that's the whole point of hashing
        with their exact recipe up front). event_id is deterministic from
        order_id so a re-sent event (there shouldn't be one — the caller,
        app/services/capi.py, checks capi_events for idempotency first) would
        still dedupe correctly against any browser-pixel Purchase event with
        the same id.
        """
        url = f"{self.API_BASE}/{pixel_id}/events"
        user_data = {}
        if email_hash:
            user_data["em"] = [email_hash]
        if phone_hash:
            user_data["ph"] = [phone_hash]

        payload = {
            "data": [
                {
                    "event_name": "Purchase",
                    "event_time": int(order_time.timestamp()),
                    "event_id": f"order:{order_id}",
                    "action_source": "website",
                    "user_data": user_data,
                    "custom_data": {"value": value, "currency": currency},
                }
            ]
        }

        response = requests.post(
            url, headers={"Authorization": f"Bearer {access_token}"}, json=payload, timeout=HTTP_TIMEOUT
        )
        response.raise_for_status()

    def fetch_historical_data(
        self,
        access_token: str,
        start_date: datetime,
        end_date: datetime,
    ) -> dict:
        """Fetch historical ad spend data."""
        return {"spend_records": self.fetch_ad_spend(access_token, start_date, end_date)}
