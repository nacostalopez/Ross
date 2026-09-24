"""Mercado Libre connector: marketplace orders + Product Ads (Mercado Ads) spend.

Mercado Libre is both a sales channel (its orders land in `orders`, like
Shopify/Tiendanube's) and an ad platform (Product Ads spend lands in
`ad_spend` as platform "mercadolibre"), so this one connector feeds both
tables from a single OAuth grant.
"""

from datetime import date, datetime, timedelta, timezone
from typing import List, Optional
from urllib.parse import urlencode

import requests
from pydantic_settings import BaseSettings

from app.connectors import BaseConnector, OAuthToken


class MercadoLibreSettings(BaseSettings):
    """Mercado Libre-specific configuration."""

    mercadolibre_client_id: str = ""
    mercadolibre_client_secret: str = ""
    mercadolibre_redirect_uri: str = "http://localhost:3100/index.html?connector=mercadolibre"
    # The consent screen lives on the seller's country domain
    # (auth.mercadolibre.com.ar / .com.mx / auth.mercadolivre.com.br ...);
    # the API itself is the same host for every country.
    mercadolibre_auth_domain: str = "auth.mercadolibre.com.ar"

    class Config:
        env_file = ".env"


# Orders in these states never became a sale (or stopped being one) —
# ingesting them would inflate revenue, and one that was ingested while
# "paid" and later cancelled has to be removed again.
NON_SALE_STATUSES = {"cancelled", "invalid"}
SALE_STATUSES = {"paid", "partially_refunded"}


class MercadoLibreConnector(BaseConnector):
    """Mercado Libre connector for OAuth, orders, and Product Ads spend."""

    API_VERSION = "v1"
    API_BASE = "https://api.mercadolibre.com"
    # Bump API_VERSION when Mercado Libre deprecates it and log what changed here.
    BREAKING_CHANGES = [
        "v1: initial implementation (orders search + Product Ads api-version 2 campaign metrics)",
    ]
    # Mercado Ads only serves metrics up to 90 days back.
    ADS_MAX_LOOKBACK_DAYS = 90
    ORDERS_PAGE_SIZE = 50

    def __init__(self, store_id: str, seller_id: Optional[str] = None):
        super().__init__(store_id, "mercadolibre")
        self.settings = MercadoLibreSettings()
        self.seller_id = seller_id  # Mercado Libre user_id of the connected seller

    def get_oauth_url(self, state: str) -> str:
        """Get Mercado Libre OAuth authorization URL."""
        params = {
            "response_type": "code",
            "client_id": self.settings.mercadolibre_client_id,
            "redirect_uri": self.settings.mercadolibre_redirect_uri,
            "state": state,
        }
        return f"https://{self.settings.mercadolibre_auth_domain}/authorization?{urlencode(params)}"

    def _token_request(self, payload: dict) -> dict:
        # Mercado Libre's token endpoint takes form-encoded bodies, not JSON.
        response = requests.post(
            f"{self.API_BASE}/oauth/token",
            data=payload,
            headers={"accept": "application/json", "content-type": "application/x-www-form-urlencoded"},
        )
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
                "grant_type": "authorization_code",
                "client_id": self.settings.mercadolibre_client_id,
                "client_secret": self.settings.mercadolibre_client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            }
        )
        self.seller_id = str(data.get("user_id", self.seller_id or ""))
        return self._to_token(data)

    def refresh_access_token(self, refresh_token: str) -> OAuthToken:
        """Refresh an access token (they last 6 hours).

        Mercado Libre refresh tokens are single-use: the response carries a
        new one and the old one stops working, so the caller must persist
        the returned refresh_token or the connection is lost at the next
        expiry.
        """
        data = self._token_request(
            {
                "grant_type": "refresh_token",
                "client_id": self.settings.mercadolibre_client_id,
                "client_secret": self.settings.mercadolibre_client_secret,
                "refresh_token": refresh_token,
            }
        )
        return self._to_token(data, fallback_refresh=refresh_token)

    def validate_webhook_signature(self, body: str, signature: str) -> bool:
        """Mercado Libre notifications carry no signature at all.

        The only thing to check up front is that the notification names our
        own application (`signature` is its application_id); the real
        protection is that the route never trusts the payload — it only
        uses `resource` as a pointer and re-fetches that order from the API
        with the seller's own token, so a forged notification can at most
        trigger a harmless re-sync of a real order.
        """
        return bool(signature) and str(signature) == str(self.settings.mercadolibre_client_id)

    def process_webhook(self, event_type: str, data: dict) -> dict:
        """Convert an order (already re-fetched from the API) to standardized format."""
        if event_type == "orders_v2":
            return self.order_to_row(data)
        raise ValueError(f"Unsupported event type: {event_type}")

    def order_to_row(self, ml_order: dict) -> dict:
        """Convert a Mercado Libre order to the standardized orders row.

        - gross_amount is total_amount (items only; shipping paid by the
          buyer isn't the seller's revenue).
        - payment_gateway_fee carries Mercado Libre's selling fee (sale_fee
          is per unit, so times quantity) — it's what the platform keeps
          from each sale, the same role a gateway fee plays elsewhere.
        - shipping_fee stays 0: what the seller pays for free shipping
          lives on /shipments/{id}/costs, one extra call per order — not
          fetched yet (see README).
        - There's no UTM/landing data on a marketplace order; the channel
          is the marketplace itself, so attribution_utm_source is
          "mercadolibre", which the CAC/attribution queries pair with
          Product Ads spend.
        """
        items = ml_order.get("order_items") or []
        sale_fee = sum(float(i.get("sale_fee") or 0) * int(i.get("quantity") or 1) for i in items)
        buyer = ml_order.get("buyer") or {}
        phone = buyer.get("phone") or {}
        phone_str = f"{phone.get('area_code') or ''}{phone.get('number') or ''}".strip() or None

        return {
            "order_id": str(ml_order["id"]),
            "time": ml_order.get("date_created") or ml_order.get("date_closed") or datetime.utcnow().isoformat(),
            "gross_amount": float(ml_order.get("total_amount") or 0),
            "discounts": 0,
            "shipping_fee": 0,
            "payment_gateway_fee": round(sale_fee, 4),
            "cogs_total": 0,
            "currency": ml_order.get("currency_id") or "ARS",
            "attribution_utm_source": "mercadolibre",
            "attribution_utm_campaign": None,
            "utm_medium": "marketplace",
            "utm_content": None,
            "click_id": None,
            "landing_url": None,
            # Mercado Libre masks buyer contact data for most sellers; the
            # buyer id is the stable identity (see resolve_customer_id).
            "customer_email": buyer.get("email"),
            "customer_phone": phone_str,
            "external_customer_id": f"ml:{buyer['id']}" if buyer.get("id") else None,
        }

    def fetch_order(self, access_token: str, resource: str) -> dict:
        """GET a single order by its notification resource path ("/orders/123")."""
        if not resource.startswith("/orders/"):
            raise ValueError(f"Unexpected resource: {resource}")
        response = requests.get(f"{self.API_BASE}{resource}", headers={"Authorization": f"Bearer {access_token}"})
        response.raise_for_status()
        return response.json()

    def fetch_orders(self, access_token: str, start_date: datetime, end_date: datetime) -> List[dict]:
        """All of the seller's orders created in [start_date, end_date], raw (not converted)."""
        if not self.seller_id:
            raise ValueError("seller_id required for orders fetch")

        headers = {"Authorization": f"Bearer {access_token}"}
        params = {
            "seller": self.seller_id,
            "order.date_created.from": start_date.strftime("%Y-%m-%dT%H:%M:%S.000-00:00"),
            "order.date_created.to": end_date.strftime("%Y-%m-%dT%H:%M:%S.000-00:00"),
            "sort": "date_asc",
            "limit": self.ORDERS_PAGE_SIZE,
            "offset": 0,
        }

        orders = []
        while True:
            response = requests.get(f"{self.API_BASE}/orders/search", headers=headers, params=params)
            response.raise_for_status()
            results = response.json().get("results", [])
            orders.extend(results)
            if len(results) < params["limit"]:
                break
            params["offset"] += params["limit"]
        return orders

    def fetch_historical_data(self, start_date: datetime, end_date: datetime, access_token: str) -> List[dict]:
        """Historical orders in standardized format, sales only."""
        return [
            self.order_to_row(o)
            for o in self.fetch_orders(access_token, start_date, end_date)
            if o.get("status") in SALE_STATUSES
        ]

    def _ads_headers(self, access_token: str, api_version: str) -> dict:
        return {"Authorization": f"Bearer {access_token}", "Api-Version": api_version}

    def fetch_advertiser_id(self, access_token: str) -> Optional[str]:
        """The seller's Product Ads advertiser_id, or None if Product Ads isn't enabled.

        A seller can have one advertiser per country site; the connected
        store sells in one country, so the first one is taken.
        """
        response = requests.get(
            f"{self.API_BASE}/advertising/advertisers",
            headers=self._ads_headers(access_token, "1"),
            params={"product_id": "PADS"},
        )
        # 404 = "No permissions found for user_id": Product Ads isn't enabled
        # for this seller — not an error, there's just no spend to sync.
        if response.status_code == 404:
            return None
        response.raise_for_status()
        advertisers = response.json().get("advertisers", [])
        return str(advertisers[0]["advertiser_id"]) if advertisers else None

    def ads_window(self, start_date: datetime, end_date: datetime) -> Optional[tuple[date, date]]:
        """The part of [start_date, end_date] Mercado Ads can still report on
        (its 90-day lookback), as whole days — or None if none of it can."""
        earliest = (datetime.now(timezone.utc) - timedelta(days=self.ADS_MAX_LOOKBACK_DAYS)).date()
        start = max(start_date.date(), earliest)
        end = end_date.date()
        return (start, end) if start <= end else None

    def fetch_ad_spend(
        self,
        access_token: str,
        start_date: datetime,
        end_date: datetime,
        advertiser_id: Optional[str] = None,
    ) -> List[dict]:
        """Daily Product Ads spend per campaign, shaped like Meta/Google's records.

        Two steps, because the advertiser-wide DAILY aggregation sums every
        campaign into one row per day (no campaign_id, which ad_spend
        requires): list the advertiser's campaigns, then ask each one for
        its own daily metrics. start_date is clamped to Mercado Ads' 90-day
        lookback — asking for older data is an API error, not an empty set.
        """
        advertiser_id = advertiser_id or self.fetch_advertiser_id(access_token)
        window = self.ads_window(start_date, end_date)
        if not advertiser_id or not window:
            return []
        start, end = window

        headers = self._ads_headers(access_token, "2")
        date_params = {"date_from": start.isoformat(), "date_to": end.isoformat()}

        campaigns = []
        params = {**date_params, "metrics": "cost", "limit": 50, "offset": 0}
        while True:
            response = requests.get(
                f"{self.API_BASE}/advertising/advertisers/{advertiser_id}/product_ads/campaigns",
                headers=headers,
                params=params,
            )
            response.raise_for_status()
            results = response.json().get("results", [])
            campaigns.extend(results)
            if len(results) < params["limit"]:
                break
            params["offset"] += params["limit"]

        records = []
        for campaign in campaigns:
            # Skip campaigns that spent nothing in the window — saves one
            # request per idle campaign.
            if not float((campaign.get("metrics") or {}).get("cost") or 0):
                continue
            response = requests.get(
                f"{self.API_BASE}/advertising/product_ads/campaigns/{campaign['id']}",
                headers=headers,
                params={**date_params, "metrics": "cost,prints,clicks", "aggregation_type": "DAILY"},
            )
            response.raise_for_status()
            body = response.json()
            # The docs show the daily form as a bare list; accept a
            # {"results": [...]} wrapper too, like the campaigns search uses.
            days = body.get("results", []) if isinstance(body, dict) else body
            for day in days:
                records.append(
                    {
                        "time": datetime.fromisoformat(day["date"]),
                        "platform": "mercadolibre",
                        "campaign_id": str(campaign["id"]),
                        "campaign_name": campaign.get("name", ""),
                        "adset_id": "",
                        "spend": float(day.get("cost") or 0),
                        "impressions": int(day.get("prints") or 0),
                        "clicks": int(day.get("clicks") or 0),
                    }
                )
        return records
