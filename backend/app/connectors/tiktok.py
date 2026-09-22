"""TikTok Ads connector for ad spend tracking."""

import json
from datetime import datetime
from typing import List, Optional

import requests
from pydantic_settings import BaseSettings

from app.connectors import BaseConnector, OAuthToken


class TikTokSettings(BaseSettings):
    """TikTok-specific configuration."""

    tiktok_app_id: str = ""
    tiktok_app_secret: str = ""
    # Lands back on the frontend's index.html (plain query params, no
    # dedicated route) — nginx here serves static files with no SPA
    # fallback, so a path like /auth/tiktok/callback would 404.
    tiktok_redirect_uri: str = "http://localhost:3100/index.html?connector=tiktok"

    class Config:
        env_file = ".env"


class TikTokConnector(BaseConnector):
    """TikTok Ads (Business/Marketing API) connector for OAuth and ad spend tracking."""

    API_VERSION = "v1.3"
    API_BASE = f"https://business-api.tiktok.com/open_api/{API_VERSION}"
    AUTH_URL = "https://ads.tiktok.com/marketing_api/auth"
    TOKEN_URL = f"{API_BASE}/oauth2/access_token/"
    # Bump API_VERSION when TikTok deprecates it and log what changed here.
    BREAKING_CHANGES = [
        "v1.3: initial implementation",
    ]

    def __init__(self, store_id: str, advertiser_id: Optional[str] = None):
        super().__init__(store_id, "tiktok")
        self.settings = TikTokSettings()
        self.advertiser_id = advertiser_id  # e.g., "1234567890123456"

    def get_oauth_url(self, state: str) -> str:
        """Get TikTok OAuth authorization URL.

        Unlike Meta/Google, TikTok's Marketing API authorize page takes no
        `scope` param — the permissions an app can request are fixed by
        what TikTok approved for the app during its own developer review,
        not chosen per authorization request.
        """
        params = {
            "app_id": self.settings.tiktok_app_id,
            "state": state,
            "redirect_uri": self.settings.tiktok_redirect_uri,
        }

        from urllib.parse import urlencode

        return f"{self.AUTH_URL}?{urlencode(params)}"

    def exchange_auth_code(self, code: str, redirect_uri: str) -> OAuthToken:
        """Exchange authorization code for access token.

        TikTok wraps every response (including errors) in a
        {"code": 0, "message": "OK", "data": {...}} envelope instead of
        using HTTP status codes for API-level failures — a non-zero `code`
        must be checked explicitly, raise_for_status() alone won't catch it.
        """
        payload = {
            "app_id": self.settings.tiktok_app_id,
            "secret": self.settings.tiktok_app_secret,
            "auth_code": code,
        }

        response = requests.post(self.TOKEN_URL, json=payload)
        response.raise_for_status()

        body = response.json()
        if body.get("code", 0) != 0:
            raise ValueError(f"TikTok token exchange failed: {body.get('message', 'unknown error')}")

        data = body["data"]

        return OAuthToken(
            access_token=data["access_token"],
            refresh_token=None,  # TikTok's Marketing API access tokens don't expire/rotate.
            expires_at=None,
        )

    def validate_webhook_signature(self, body: str, signature: str) -> bool:
        """The TikTok Marketing API has no ad-spend webhooks to validate."""
        return True

    def process_webhook(self, event_type: str, data: dict) -> dict:
        """The TikTok Marketing API has no ad-spend webhooks to process."""
        raise NotImplementedError("TikTok Ads does not use webhooks")

    def fetch_ad_spend(
        self,
        access_token: str,
        start_date: datetime,
        end_date: datetime,
    ) -> List[dict]:
        """Fetch ad spend data from the TikTok Integrated Report endpoint.

        Args:
            access_token: TikTok API access token
            start_date: Start date for data fetch
            end_date: End date for data fetch

        Returns:
            List of ad spend records
        """
        if not self.advertiser_id:
            raise ValueError("advertiser_id required for ad spend fetch")

        return self._fetch_report(access_token, start_date, end_date, level="AUCTION_CAMPAIGN")

    def fetch_creative_performance(
        self,
        access_token: str,
        start_date: datetime,
        end_date: datetime,
    ) -> List[dict]:
        """Ad-level (creative) breakdown for the creative_performance table.

        Same Integrated Report endpoint as fetch_ad_spend, requested at ad
        level (AUCTION_AD) instead of campaign. thumbnail_url isn't fetched
        — TikTok doesn't return it on this report, getting it needs a
        separate `/creative/list/` call per ad, deliberately left for later
        rather than adding N+1 API calls here (same trade-off as Meta).
        """
        if not self.advertiser_id:
            raise ValueError("advertiser_id required for creative performance fetch")

        return self._fetch_report(access_token, start_date, end_date, level="AUCTION_AD")

    def _fetch_report(
        self,
        access_token: str,
        start_date: datetime,
        end_date: datetime,
        level: str,
    ) -> List[dict]:
        """Shared paginated fetch against /report/integrated/get/.

        level="AUCTION_CAMPAIGN" -> ad_spend rows; level="AUCTION_AD" ->
        creative_performance rows (adds ad_id/ad_name/thumbnail_url).
        """
        url = f"{self.API_BASE}/report/integrated/get/"
        headers = {"Access-Token": access_token}

        is_ad_level = level == "AUCTION_AD"
        dimensions = ["ad_id", "stat_time_day"] if is_ad_level else ["campaign_id", "stat_time_day"]
        metrics = ["spend", "impressions", "clicks", "campaign_id", "campaign_name"]
        if is_ad_level:
            metrics += ["ad_name"]

        records: List[dict] = []
        page = 1
        while True:
            params = {
                "advertiser_id": self.advertiser_id,
                "report_type": "BASIC",
                "data_level": level,
                "dimensions": json.dumps(dimensions),
                "metrics": json.dumps(metrics),
                "start_date": start_date.date().isoformat(),
                "end_date": end_date.date().isoformat(),
                "page": page,
                "page_size": 100,
            }

            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()

            body = response.json()
            if body.get("code", 0) != 0:
                raise ValueError(f"TikTok report fetch failed: {body.get('message', 'unknown error')}")

            data = body.get("data", {})
            for row in data.get("list", []):
                dims = row.get("dimensions", {})
                mets = row.get("metrics", {})
                # "2026-01-01 00:00:00" -> keep just the date part.
                stat_day = dims.get("stat_time_day", "").split(" ")[0]
                record = {
                    "time": datetime.fromisoformat(stat_day) if stat_day else datetime.utcnow(),
                    "platform": "tiktok",
                    "campaign_id": str(mets.get("campaign_id", "")),
                    "campaign_name": mets.get("campaign_name", ""),
                    "adset_id": "",  # TikTok's ad-group id isn't requested at this report level.
                    "spend": float(mets.get("spend", 0)),
                    "impressions": int(float(mets.get("impressions", 0))),
                    "clicks": int(float(mets.get("clicks", 0))),
                }
                if is_ad_level:
                    ad_id = str(dims.get("ad_id", ""))
                    record["ad_id"] = ad_id
                    record["ad_name"] = mets.get("ad_name") or f"Ad {ad_id}"
                    record["thumbnail_url"] = None
                records.append(record)

            page_info = data.get("page_info", {})
            if page >= page_info.get("total_page", page):
                break
            page += 1

        return records

    def fetch_historical_data(
        self,
        access_token: str,
        start_date: datetime,
        end_date: datetime,
    ) -> dict:
        """Fetch historical ad spend data."""
        return {"spend_records": self.fetch_ad_spend(access_token, start_date, end_date)}
