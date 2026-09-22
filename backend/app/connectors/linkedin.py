"""LinkedIn Ads connector for ad spend tracking."""

from datetime import datetime, timedelta, timezone
from typing import List, Optional

import requests
from pydantic_settings import BaseSettings

from app.connectors import BaseConnector, OAuthToken


class LinkedInSettings(BaseSettings):
    """LinkedIn-specific configuration."""

    linkedin_client_id: str = ""
    linkedin_client_secret: str = ""
    # Lands back on the frontend's index.html (plain query params, no
    # dedicated route) — nginx here serves static files with no SPA
    # fallback, so a path like /auth/linkedin/callback would 404.
    linkedin_redirect_uri: str = "http://localhost:3100/index.html?connector=linkedin"
    linkedin_scopes: str = "r_ads,r_ads_reporting"

    class Config:
        env_file = ".env"


class LinkedInConnector(BaseConnector):
    """LinkedIn Ads (Marketing API) connector for OAuth and ad spend tracking."""

    # Rest.li versioned API — LinkedIn-Version is a "YYYYMM" release, not a
    # path segment like Meta's/Google's API_VERSION. Bump it when LinkedIn
    # sunsets the current one (~1yr window) and log what changed here.
    API_VERSION = "202401"
    API_BASE = "https://api.linkedin.com/rest"
    AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
    TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
    BREAKING_CHANGES = [
        "202401: initial implementation",
    ]

    def __init__(self, store_id: str, ad_account_id: Optional[str] = None):
        super().__init__(store_id, "linkedin")
        self.settings = LinkedInSettings()
        self.ad_account_id = ad_account_id  # e.g., "512345678" (sponsoredAccount id, no urn prefix)

    def _headers(self, access_token: str) -> dict:
        return {
            "Authorization": f"Bearer {access_token}",
            "LinkedIn-Version": self.API_VERSION,
            "X-Restli-Protocol-Version": "2.0.0",
        }

    def get_oauth_url(self, state: str) -> str:
        """Get LinkedIn OAuth authorization URL."""
        params = {
            "response_type": "code",
            "client_id": self.settings.linkedin_client_id,
            "redirect_uri": self.settings.linkedin_redirect_uri,
            "state": state,
            "scope": self.settings.linkedin_scopes,
        }

        from urllib.parse import urlencode

        return f"{self.AUTH_URL}?{urlencode(params)}"

    def exchange_auth_code(self, code: str, redirect_uri: str) -> OAuthToken:
        """Exchange authorization code for access token.

        LinkedIn's token endpoint takes form-encoded params (not JSON, and
        not query-string-on-a-GET like Meta) and, for Marketing Developer
        Platform apps, returns both an access_token (~60 day expiry) and a
        refresh_token (~1yr expiry) — unlike Meta's non-expiring token.
        """
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self.settings.linkedin_client_id,
            "client_secret": self.settings.linkedin_client_secret,
            "redirect_uri": redirect_uri,
        }

        response = requests.post(self.TOKEN_URL, data=payload)
        response.raise_for_status()

        data = response.json()

        expires_at = None
        if "expires_in" in data:
            expires_at = datetime.utcnow() + timedelta(seconds=data["expires_in"])

        return OAuthToken(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_at=expires_at,
        )

    def refresh_access_token(self, refresh_token: str) -> OAuthToken:
        """Refresh access token using refresh token."""
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.settings.linkedin_client_id,
            "client_secret": self.settings.linkedin_client_secret,
        }

        response = requests.post(self.TOKEN_URL, data=payload)
        response.raise_for_status()

        data = response.json()

        expires_at = None
        if "expires_in" in data:
            expires_at = datetime.utcnow() + timedelta(seconds=data["expires_in"])

        return OAuthToken(
            access_token=data["access_token"],
            # LinkedIn may or may not rotate the refresh token itself; keep
            # the new one when given, otherwise the old one stays valid.
            refresh_token=data.get("refresh_token", refresh_token),
            expires_at=expires_at,
        )

    def validate_webhook_signature(self, body: str, signature: str) -> bool:
        """The LinkedIn Marketing API has no ad-spend webhooks to validate."""
        return True

    def process_webhook(self, event_type: str, data: dict) -> dict:
        """The LinkedIn Marketing API has no ad-spend webhooks to process."""
        raise NotImplementedError("LinkedIn Ads does not use webhooks")

    def fetch_ad_spend(
        self,
        access_token: str,
        start_date: datetime,
        end_date: datetime,
    ) -> List[dict]:
        """Fetch ad spend data from the LinkedIn Ad Analytics endpoint,
        pivoted by campaign.

        Args:
            access_token: LinkedIn API access token
            start_date: Start date for data fetch
            end_date: End date for data fetch

        Returns:
            List of ad spend records
        """
        if not self.ad_account_id:
            raise ValueError("ad_account_id required for ad spend fetch")

        records = []
        for element in self._fetch_analytics(access_token, start_date, end_date, pivot="CAMPAIGN"):
            campaign_id = _urn_id(element.get("pivotValues", [None])[0])
            records.append(
                {
                    "time": _element_date(element),
                    "platform": "linkedin",
                    "campaign_id": campaign_id,
                    # LinkedIn's analytics response only returns the campaign
                    # URN, not its human name — that needs a separate
                    # /rest/adCampaigns/{id} call per campaign, deliberately
                    # left for later rather than adding N+1 API calls here
                    # (same trade-off as Meta's thumbnail_url).
                    "campaign_name": "",
                    "adset_id": "",
                    "spend": float(element.get("costInLocalCurrency", 0)),
                    "impressions": int(element.get("impressions", 0)),
                    "clicks": int(element.get("clicks", 0)),
                }
            )
        return records

    def fetch_creative_performance(
        self,
        access_token: str,
        start_date: datetime,
        end_date: datetime,
    ) -> List[dict]:
        """Ad-level (creative) breakdown for the creative_performance table.

        Same Ad Analytics endpoint as fetch_ad_spend, pivoted by CREATIVE
        instead of CAMPAIGN. campaign_id is left blank here — the CREATIVE
        pivot doesn't return the parent campaign without an extra lookup —
        and thumbnail_url isn't fetched, same reasoning as fetch_ad_spend's
        campaign_name above.
        """
        if not self.ad_account_id:
            raise ValueError("ad_account_id required for creative performance fetch")

        records = []
        for element in self._fetch_analytics(access_token, start_date, end_date, pivot="CREATIVE"):
            ad_id = _urn_id(element.get("pivotValues", [None])[0])
            records.append(
                {
                    "time": _element_date(element),
                    "platform": "linkedin",
                    "campaign_id": "",
                    "campaign_name": "",
                    "adset_id": "",
                    "ad_id": ad_id,
                    "ad_name": f"Ad {ad_id}",
                    "thumbnail_url": None,
                    "spend": float(element.get("costInLocalCurrency", 0)),
                    "impressions": int(element.get("impressions", 0)),
                    "clicks": int(element.get("clicks", 0)),
                }
            )
        return records

    def _fetch_analytics(
        self,
        access_token: str,
        start_date: datetime,
        end_date: datetime,
        pivot: str,
    ) -> List[dict]:
        """Shared call against /adAnalytics (Rest.li "finder" query).

        Rest.li 2.0's structured query params use literal `(...)`/`:` as
        syntax, not data to percent-encode — building this string by hand
        (rather than urlencode, which would mangle it) mirrors how Google's
        GAQL string is built in app/connectors/google.py.
        """
        url = f"{self.API_BASE}/adAnalytics"
        date_range = (
            f"(start:(year:{start_date.year},month:{start_date.month},day:{start_date.day}),"
            f"end:(year:{end_date.year},month:{end_date.month},day:{end_date.day}))"
        )
        account_urn = f"urn:li:sponsoredAccount:{self.ad_account_id}"
        params = (
            f"q=analytics&pivot={pivot}&dateRange={date_range}"
            f"&timeGranularity=DAILY&accounts=List({account_urn})"
            "&fields=dateRange,pivotValues,impressions,clicks,costInLocalCurrency"
        )

        response = requests.get(f"{url}?{params}", headers=self._headers(access_token))
        response.raise_for_status()

        return response.json().get("elements", [])

    def fetch_historical_data(
        self,
        access_token: str,
        start_date: datetime,
        end_date: datetime,
    ) -> dict:
        """Fetch historical ad spend data."""
        return {"spend_records": self.fetch_ad_spend(access_token, start_date, end_date)}


def _urn_id(urn: Optional[str]) -> str:
    """"urn:li:sponsoredCampaign:12345" -> "12345"; "" if urn is falsy/malformed."""
    if not urn:
        return ""
    return urn.rsplit(":", 1)[-1]


def _element_date(element: dict) -> datetime:
    """Analytics elements carry a dateRange, not a single date — DAILY
    granularity makes start == end, so the range's start is the record's day."""
    start = element.get("dateRange", {}).get("start")
    if not start:
        return datetime.utcnow()
    return datetime(start["year"], start["month"], start["day"], tzinfo=timezone.utc)
