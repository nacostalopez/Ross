"""Tests verifying every connector normalizes its output to a consistent schema.

Meta and Google both feed the ad_spend table and must produce the same field
set. Shopify feeds the orders table and must satisfy OrderCreate. HTTP calls
are mocked — these are schema/shape checks, not live integration tests.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.connectors.google import GoogleAdsConnector
from app.connectors.linkedin import LinkedInConnector
from app.connectors.mercadolibre import MercadoLibreConnector
from app.connectors.mercadopago import MercadoPagoConnector
from app.connectors.meta import MetaConnector
from app.connectors.shopify import ShopifyConnector
from app.connectors.tiendanube import TiendanubeConnector
from app.connectors.tiktok import TikTokConnector

AD_SPEND_REQUIRED_FIELDS = {
    "time",
    "platform",
    "campaign_id",
    "campaign_name",
    "adset_id",
    "spend",
    "impressions",
    "clicks",
}
ORDER_REQUIRED_FIELDS = {
    "order_id",
    "time",
    "gross_amount",
    "discounts",
    "shipping_fee",
    "payment_gateway_fee",
    "cogs_total",
    "currency",
    "attribution_utm_source",
    "attribution_utm_campaign",
    "utm_medium",
    "utm_content",
    "click_id",
    "landing_url",
}


def _mock_response(payload):
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status = MagicMock()
    return response


def _recent_window():
    """Mercado Ads only reports the last 90 days, so its tests can't use fixed 2026-01 dates."""
    end = datetime.now(timezone.utc)
    return end - timedelta(days=10), end


def _mercadolibre_ads_responses():
    """Campaign list (one campaign that spent, one idle) then that campaign's daily metrics."""
    day = (datetime.now(timezone.utc) - timedelta(days=3)).date().isoformat()
    return [
        _mock_response(
            {
                "paging": {"total": 2, "offset": 0, "limit": 50},
                "results": [
                    {"id": 77, "name": "Crecimiento A", "metrics": {"cost": 12.5}},
                    {"id": 78, "name": "Idle", "metrics": {"cost": 0}},
                ],
            }
        ),
        _mock_response([{"date": day, "cost": 12.5, "prints": 1000, "clicks": 20}]),
    ]


@pytest.mark.connector
class TestAdSpendSchemaConsistency:
    """Meta and Google must both feed the same ad_spend table with the same shape."""

    def test_meta_ad_spend_record_shape(self):
        connector = MetaConnector(store_id="store-1", ad_account_id="act_123")
        response = _mock_response(
            {
                "data": [
                    {
                        "campaign_id": "1",
                        "campaign_name": "Test Campaign",
                        "adset_id": "10",
                        "spend": "12.50",
                        "impressions": "1000",
                        "clicks": "20",
                        "date_start": "2026-01-01",
                    }
                ],
                "paging": {},
            }
        )
        with patch("app.connectors.meta.requests.get", return_value=response):
            records = connector.fetch_ad_spend(
                "fake-token",
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 1, 31, tzinfo=timezone.utc),
            )
        assert len(records) == 1
        assert AD_SPEND_REQUIRED_FIELDS.issubset(records[0].keys())
        assert records[0]["platform"] == "meta"

    def test_google_ad_spend_record_shape(self):
        connector = GoogleAdsConnector(store_id="store-1", customer_id="123")
        response = _mock_response(
            {
                "results": [
                    {
                        "campaign": {"id": "1", "name": "Test Campaign"},
                        "ad_group": {"id": "10"},
                        "metrics": {"cost_micros": 12500000, "impressions": 1000, "clicks": 20},
                        "segments": {"date": "2026-01-01"},
                    }
                ],
            }
        )
        with patch("app.connectors.google.requests.post", return_value=response):
            records = connector.fetch_ad_spend(
                "fake-token",
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 1, 31, tzinfo=timezone.utc),
            )
        assert len(records) == 1
        assert AD_SPEND_REQUIRED_FIELDS.issubset(records[0].keys())
        assert records[0]["platform"] == "google"

    def test_mercadolibre_ad_spend_record_shape(self):
        connector = MercadoLibreConnector(store_id="store-1", seller_id="seller-123")
        start, end = _recent_window()
        with patch("app.connectors.mercadolibre.requests.get", side_effect=_mercadolibre_ads_responses()):
            records = connector.fetch_ad_spend("fake-token", start, end, advertiser_id="adv-1")
        assert len(records) == 1
        assert AD_SPEND_REQUIRED_FIELDS.issubset(records[0].keys())
        assert records[0]["platform"] == "mercadolibre"
        assert records[0]["campaign_id"] == "77"
        assert records[0]["campaign_name"] == "Crecimiento A"
        assert records[0]["spend"] == 12.5
        assert records[0]["impressions"] == 1000

    def test_tiktok_ad_spend_record_shape(self):
        connector = TikTokConnector(store_id="store-1", advertiser_id="adv-123")
        response = _mock_response(
            {
                "code": 0,
                "data": {
                    "list": [
                        {
                            "dimensions": {"campaign_id": "1", "stat_time_day": "2026-01-01 00:00:00"},
                            "metrics": {
                                "campaign_id": "1",
                                "campaign_name": "Test Campaign",
                                "spend": "12.50",
                                "impressions": "1000",
                                "clicks": "20",
                            },
                        }
                    ],
                    "page_info": {"total_page": 1},
                },
            }
        )
        with patch("app.connectors.tiktok.requests.get", return_value=response):
            records = connector.fetch_ad_spend(
                "fake-token",
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 1, 31, tzinfo=timezone.utc),
            )
        assert len(records) == 1
        assert AD_SPEND_REQUIRED_FIELDS.issubset(records[0].keys())
        assert records[0]["platform"] == "tiktok"

    def test_linkedin_ad_spend_record_shape(self):
        connector = LinkedInConnector(store_id="store-1", ad_account_id="512345678")
        response = _mock_response(
            {
                "elements": [
                    {
                        "dateRange": {"start": {"year": 2026, "month": 1, "day": 1}},
                        "pivotValues": ["urn:li:sponsoredCampaign:123"],
                        "impressions": 1000,
                        "clicks": 20,
                        "costInLocalCurrency": "12.50",
                    }
                ]
            }
        )
        with patch("app.connectors.linkedin.requests.get", return_value=response):
            records = connector.fetch_ad_spend(
                "fake-token",
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 1, 31, tzinfo=timezone.utc),
            )
        assert len(records) == 1
        assert AD_SPEND_REQUIRED_FIELDS.issubset(records[0].keys())
        assert records[0]["platform"] == "linkedin"
        assert records[0]["campaign_id"] == "123"

    def test_ad_spend_connectors_produce_identical_field_sets(self):
        """Whatever field one ad-spend connector adds, the others must too."""
        meta_connector = MetaConnector(store_id="store-1", ad_account_id="act_123")
        google_connector = GoogleAdsConnector(store_id="store-1", customer_id="123")
        mercadolibre_connector = MercadoLibreConnector(store_id="store-1", seller_id="seller-123")

        meta_response = _mock_response(
            {
                "data": [
                    {
                        "campaign_id": "1",
                        "campaign_name": "C",
                        "adset_id": "10",
                        "spend": "1",
                        "impressions": "1",
                        "clicks": "1",
                        "date_start": "2026-01-01",
                    }
                ],
                "paging": {},
            }
        )
        google_response = _mock_response(
            {
                "results": [
                    {
                        "campaign": {"id": "1", "name": "C"},
                        "ad_group": {"id": "10"},
                        "metrics": {"cost_micros": 1000000, "impressions": 1, "clicks": 1},
                        "segments": {"date": "2026-01-01"},
                    }
                ],
            }
        )
        with patch("app.connectors.meta.requests.get", return_value=meta_response):
            meta_records = meta_connector.fetch_ad_spend(
                "t", datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 31, tzinfo=timezone.utc)
            )
        with patch("app.connectors.google.requests.post", return_value=google_response):
            google_records = google_connector.fetch_ad_spend(
                "t", datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 31, tzinfo=timezone.utc)
            )
        with patch("app.connectors.mercadolibre.requests.get", side_effect=_mercadolibre_ads_responses()):
            mercadolibre_records = mercadolibre_connector.fetch_ad_spend("t", *_recent_window(), advertiser_id="adv-1")

        assert set(meta_records[0].keys()) == set(google_records[0].keys()) == set(mercadolibre_records[0].keys())


CREATIVE_PERFORMANCE_REQUIRED_FIELDS = {
    "time",
    "platform",
    "campaign_id",
    "campaign_name",
    "adset_id",
    "ad_id",
    "ad_name",
    "thumbnail_url",
    "spend",
    "impressions",
    "clicks",
}


@pytest.mark.connector
class TestCreativePerformanceSchemaConsistency:
    """Meta and Google must both feed the same creative_performance table with the same shape."""

    def test_meta_creative_performance_record_shape(self):
        connector = MetaConnector(store_id="store-1", ad_account_id="act_123")
        response = _mock_response(
            {
                "data": [
                    {
                        "campaign_id": "1",
                        "campaign_name": "Test Campaign",
                        "adset_id": "10",
                        "ad_id": "100",
                        "ad_name": "Creative A",
                        "spend": "12.50",
                        "impressions": "1000",
                        "clicks": "20",
                        "date_start": "2026-01-01",
                    }
                ],
                "paging": {},
            }
        )
        with patch("app.connectors.meta.requests.get", return_value=response):
            records = connector.fetch_creative_performance(
                "fake-token",
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 1, 31, tzinfo=timezone.utc),
            )
        assert len(records) == 1
        assert CREATIVE_PERFORMANCE_REQUIRED_FIELDS.issubset(records[0].keys())
        assert records[0]["platform"] == "meta"
        assert records[0]["ad_id"] == "100"
        assert records[0]["ad_name"] == "Creative A"

    def test_google_creative_performance_record_shape(self):
        connector = GoogleAdsConnector(store_id="store-1", customer_id="123")
        response = _mock_response(
            {
                "results": [
                    {
                        "campaign": {"id": "1", "name": "Test Campaign"},
                        "ad_group": {"id": "10"},
                        "ad_group_ad": {"ad": {"id": "100", "name": "Creative A"}},
                        "metrics": {"cost_micros": 12500000, "impressions": 1000, "clicks": 20},
                        "segments": {"date": "2026-01-01"},
                    }
                ],
            }
        )
        with patch("app.connectors.google.requests.post", return_value=response):
            records = connector.fetch_creative_performance(
                "fake-token",
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 1, 31, tzinfo=timezone.utc),
            )
        assert len(records) == 1
        assert CREATIVE_PERFORMANCE_REQUIRED_FIELDS.issubset(records[0].keys())
        assert records[0]["platform"] == "google"
        assert records[0]["ad_id"] == "100"
        assert records[0]["ad_name"] == "Creative A"

    def test_google_unnamed_ad_falls_back_to_id(self):
        """Responsive search ads often have no human name."""
        connector = GoogleAdsConnector(store_id="store-1", customer_id="123")
        response = _mock_response(
            {
                "results": [
                    {
                        "campaign": {"id": "1", "name": "Test Campaign"},
                        "ad_group": {"id": "10"},
                        "ad_group_ad": {"ad": {"id": "100"}},
                        "metrics": {"cost_micros": 1000000, "impressions": 100, "clicks": 2},
                        "segments": {"date": "2026-01-01"},
                    }
                ],
            }
        )
        with patch("app.connectors.google.requests.post", return_value=response):
            records = connector.fetch_creative_performance(
                "fake-token",
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 1, 31, tzinfo=timezone.utc),
            )
        assert records[0]["ad_name"] == "Ad 100"

    def test_tiktok_creative_performance_record_shape(self):
        connector = TikTokConnector(store_id="store-1", advertiser_id="adv-123")
        response = _mock_response(
            {
                "code": 0,
                "data": {
                    "list": [
                        {
                            "dimensions": {"ad_id": "100", "stat_time_day": "2026-01-01 00:00:00"},
                            "metrics": {
                                "campaign_id": "1",
                                "campaign_name": "Test Campaign",
                                "ad_name": "Creative A",
                                "spend": "12.50",
                                "impressions": "1000",
                                "clicks": "20",
                            },
                        }
                    ],
                    "page_info": {"total_page": 1},
                },
            }
        )
        with patch("app.connectors.tiktok.requests.get", return_value=response):
            records = connector.fetch_creative_performance(
                "fake-token",
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 1, 31, tzinfo=timezone.utc),
            )
        assert len(records) == 1
        assert CREATIVE_PERFORMANCE_REQUIRED_FIELDS.issubset(records[0].keys())
        assert records[0]["platform"] == "tiktok"
        assert records[0]["ad_id"] == "100"
        assert records[0]["ad_name"] == "Creative A"

    def test_linkedin_creative_performance_record_shape(self):
        connector = LinkedInConnector(store_id="store-1", ad_account_id="512345678")
        response = _mock_response(
            {
                "elements": [
                    {
                        "dateRange": {"start": {"year": 2026, "month": 1, "day": 1}},
                        "pivotValues": ["urn:li:sponsoredCreative:100"],
                        "impressions": 1000,
                        "clicks": 20,
                        "costInLocalCurrency": "12.50",
                    }
                ]
            }
        )
        with patch("app.connectors.linkedin.requests.get", return_value=response):
            records = connector.fetch_creative_performance(
                "fake-token",
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 1, 31, tzinfo=timezone.utc),
            )
        assert len(records) == 1
        assert CREATIVE_PERFORMANCE_REQUIRED_FIELDS.issubset(records[0].keys())
        assert records[0]["platform"] == "linkedin"
        assert records[0]["ad_id"] == "100"
        # No parent-campaign lookup for the CREATIVE pivot (see connector docstring).
        assert records[0]["campaign_id"] == ""

    def test_tiktok_requires_advertiser_id(self):
        connector = TikTokConnector(store_id="store-1")
        with pytest.raises(ValueError, match="advertiser_id required"):
            connector.fetch_creative_performance(
                "t", datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 31, tzinfo=timezone.utc)
            )

    def test_linkedin_requires_ad_account_id(self):
        connector = LinkedInConnector(store_id="store-1")
        with pytest.raises(ValueError, match="ad_account_id required"):
            connector.fetch_creative_performance(
                "t", datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 31, tzinfo=timezone.utc)
            )

    def test_creative_performance_connectors_produce_identical_field_sets(self):
        meta_connector = MetaConnector(store_id="store-1", ad_account_id="act_123")
        google_connector = GoogleAdsConnector(store_id="store-1", customer_id="123")

        meta_response = _mock_response(
            {
                "data": [
                    {
                        "campaign_id": "1",
                        "campaign_name": "C",
                        "adset_id": "10",
                        "ad_id": "100",
                        "ad_name": "A",
                        "spend": "1",
                        "impressions": "1",
                        "clicks": "1",
                        "date_start": "2026-01-01",
                    }
                ],
                "paging": {},
            }
        )
        google_response = _mock_response(
            {
                "results": [
                    {
                        "campaign": {"id": "1", "name": "C"},
                        "ad_group": {"id": "10"},
                        "ad_group_ad": {"ad": {"id": "100", "name": "A"}},
                        "metrics": {"cost_micros": 1000000, "impressions": 1, "clicks": 1},
                        "segments": {"date": "2026-01-01"},
                    }
                ],
            }
        )

        with patch("app.connectors.meta.requests.get", return_value=meta_response):
            meta_records = meta_connector.fetch_creative_performance(
                "t", datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 31, tzinfo=timezone.utc)
            )
        with patch("app.connectors.google.requests.post", return_value=google_response):
            google_records = google_connector.fetch_creative_performance(
                "t", datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 31, tzinfo=timezone.utc)
            )

        assert set(meta_records[0].keys()) == set(google_records[0].keys())

    def test_meta_requires_ad_account_id(self):
        connector = MetaConnector(store_id="store-1")
        with pytest.raises(ValueError, match="ad_account_id required"):
            connector.fetch_creative_performance(
                "t", datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 31, tzinfo=timezone.utc)
            )

    def test_google_requires_customer_id(self):
        connector = GoogleAdsConnector(store_id="store-1")
        with pytest.raises(ValueError, match="customer_id required"):
            connector.fetch_creative_performance(
                "t", datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 31, tzinfo=timezone.utc)
            )


@pytest.mark.connector
class TestOrderSchemaConsistency:
    """Shopify's normalized order output must satisfy OrderCreate's required fields."""

    def test_shopify_order_webhook_shape(self):
        connector = ShopifyConnector(store_id="store-1")
        shopify_order = {
            "id": 123456,
            "total_price": "100.00",
            "total_discounts": "0.00",
            "shipping_lines": [{"price": "4.99"}],
            "transactions": [{"kind": "capture", "gateway_fee": "3.20"}],
            "line_items": [],
            "created_at": "2026-01-01T00:00:00Z",
            "currency": "USD",
            "note": "utm_source=meta&utm_campaign=demo&utm_medium=paid_social&utm_content=v1&fbclid=fb123",
            "landing_site": "/products/demo?utm_source=meta",
            "customer": {"id": 789, "email": "buyer@example.com", "phone": "+5491112345678"},
        }
        result = connector.process_webhook("orders/create", shopify_order)
        assert ORDER_REQUIRED_FIELDS.issubset(result.keys())
        assert result["order_id"] == "123456"
        assert result["attribution_utm_source"] == "meta"
        assert result["attribution_utm_campaign"] == "demo"
        assert result["utm_medium"] == "paid_social"
        assert result["utm_content"] == "v1"
        assert result["click_id"] == "fb:fb123"
        assert result["landing_url"] == "/products/demo?utm_source=meta"
        assert result["customer_email"] == "buyer@example.com"
        assert result["customer_phone"] == "+5491112345678"
        assert result["external_customer_id"] == "789"

    def test_shopify_order_webhook_with_no_customer(self):
        """A webhook payload with no customer object must not raise."""
        connector = ShopifyConnector(store_id="store-1")
        shopify_order = {
            "id": 123456,
            "total_price": "100.00",
            "created_at": "2026-01-01T00:00:00Z",
            "currency": "USD",
        }
        result = connector.process_webhook("orders/create", shopify_order)
        assert result["customer_email"] is None
        assert result["customer_phone"] is None
        assert result["external_customer_id"] is None

    def test_tiendanube_order_webhook_shape(self):
        connector = TiendanubeConnector(store_id="store-1")
        tn_order = {
            "id": 654321,
            "total": "100.00",
            "discount": "0.00",
            "shipping_cost_customer": "4.99",
            "created_at": "2026-01-01T00:00:00Z",
            "currency": "USD",
            "landing_url": "https://mystore.com/?utm_source=meta&utm_campaign=demo&utm_medium=cpc&utm_content=v2&gclid=g456",
            "customer": {"id": 321, "email": "buyer@example.com", "phone": "+5491112345678"},
        }
        result = connector.process_webhook("order/created", tn_order)
        assert ORDER_REQUIRED_FIELDS.issubset(result.keys())
        assert result["order_id"] == "654321"
        assert result["attribution_utm_source"] == "meta"
        assert result["attribution_utm_campaign"] == "demo"
        assert result["utm_medium"] == "cpc"
        assert result["utm_content"] == "v2"
        assert result["click_id"] == "g:g456"
        assert result["landing_url"] == tn_order["landing_url"]
        assert result["customer_email"] == "buyer@example.com"
        assert result["customer_phone"] == "+5491112345678"
        assert result["external_customer_id"] == "321"

    def test_mercadolibre_order_shape(self):
        connector = MercadoLibreConnector(store_id="store-1", seller_id="207035636")
        ml_order = {
            "id": 1068825849,
            "status": "paid",
            "date_created": "2026-02-25T15:53:38.000-04:00",
            "currency_id": "ARS",
            "total_amount": 1500,
            "order_items": [
                {"item": {"id": "MLA1"}, "sale_fee": 150.0, "quantity": 2, "unit_price": 500},
                {"item": {"id": "MLA2"}, "sale_fee": 50.0, "quantity": 1, "unit_price": 500},
            ],
            "buyer": {"id": 207040551, "nickname": "TETE"},
        }
        result = connector.process_webhook("orders_v2", ml_order)
        assert ORDER_REQUIRED_FIELDS.issubset(result.keys())
        assert result["order_id"] == "1068825849"
        assert result["gross_amount"] == 1500
        # sale_fee is per unit: 150*2 + 50*1
        assert result["payment_gateway_fee"] == 350
        assert result["attribution_utm_source"] == "mercadolibre"
        assert result["customer_email"] is None
        assert result["external_customer_id"] == "ml:207040551"

    def test_mercadopago_payment_to_order_shape(self):
        connector = MercadoPagoConnector(store_id="store-1", seller_id="seller-1")
        payment = {
            "id": 555,
            "status": "approved",
            "date_approved": "2026-03-01T10:00:00.000-03:00",
            "currency_id": "ARS",
            "transaction_amount": 1000,
            "transaction_amount_refunded": 100,
            "fee_details": [
                {"type": "mercadopago_fee", "amount": 60.5, "fee_payer": "collector"},
                {"type": "financing_fee", "amount": 99, "fee_payer": "payer"},
            ],
            "payer": {"id": 42, "email": "buyer@example.com"},
        }
        result = connector.process_webhook("payment", payment)
        assert ORDER_REQUIRED_FIELDS.issubset(result.keys())
        assert result["order_id"] == "mp:555"
        assert result["payment_gateway_fee"] == 60.5  # the payer's financing fee isn't the merchant's cost
        assert result["discounts"] == 100
        assert result["customer_email"] == "buyer@example.com"
        assert result["external_customer_id"] == "mp:42"

    def test_mercadopago_fee_falls_back_to_net_received(self):
        payment = {"transaction_amount": 1000, "transaction_details": {"net_received_amount": 930}}
        assert MercadoPagoConnector.processing_fee(payment) == 70

    def test_mercadopago_detects_mercadolibre_payments(self):
        assert MercadoPagoConnector.is_mercadolibre_payment({"order": {"type": "mercadolibre", "id": 1}})
        assert not MercadoPagoConnector.is_mercadolibre_payment({"order": {"type": "mercadopago"}})
        assert not MercadoPagoConnector.is_mercadolibre_payment({})


@pytest.mark.connector
class TestMercadoLibreAdsWindow:
    def test_clamps_to_90_day_lookback(self):
        connector = MercadoLibreConnector(store_id="store-1")
        now = datetime.now(timezone.utc)
        start, end = connector.ads_window(now - timedelta(days=400), now)
        assert start == (now - timedelta(days=90)).date()
        assert end == now.date()

    def test_window_entirely_too_old_is_none(self):
        connector = MercadoLibreConnector(store_id="store-1")
        now = datetime.now(timezone.utc)
        assert connector.ads_window(now - timedelta(days=400), now - timedelta(days=200)) is None

    def test_no_product_ads_means_no_records(self):
        connector = MercadoLibreConnector(store_id="store-1")
        not_enabled = MagicMock(status_code=404)
        with patch("app.connectors.mercadolibre.requests.get", return_value=not_enabled):
            assert connector.fetch_ad_spend("t", *_recent_window()) == []
