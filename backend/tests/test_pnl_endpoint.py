"""Tests for GET /metrics/pnl — the full P&L breakdown, unlike
/metrics/summary which only exposes the net figures."""
from datetime import datetime, timezone

import pytest
from fastapi import status


@pytest.mark.db
class TestPnlEndpoint:
    def _seed_orders(self, client, auth_header, store_id, rows):
        response = client.post(f"/stores/{store_id}/orders", headers=auth_header, json=rows)
        assert response.status_code == status.HTTP_201_CREATED

    def _seed_ad_spend(self, client, auth_header, store_id, rows):
        response = client.post(f"/stores/{store_id}/ad-spend", headers=auth_header, json=rows)
        assert response.status_code == status.HTTP_201_CREATED

    def test_breaks_down_every_line_item(self, client, auth_header, test_store):
        self._seed_orders(
            client,
            auth_header,
            test_store.id,
            [
                {
                    "order_id": "order-1",
                    "time": datetime(2026, 3, 5, tzinfo=timezone.utc).isoformat(),
                    "gross_amount": 100.0,
                    "discounts": 5.0,
                    "shipping_fee": 8.0,
                    "payment_gateway_fee": 3.0,
                    "cogs_total": 20.0,
                    "currency": "USD",
                },
            ],
        )
        self._seed_ad_spend(
            client,
            auth_header,
            test_store.id,
            [{"time": datetime(2026, 3, 4, tzinfo=timezone.utc).isoformat(), "platform": "meta", "campaign_id": "c1", "spend": 10.0}],
        )

        response = client.get(
            f"/stores/{test_store.id}/metrics/pnl"
            f"?start=2026-03-01T00:00:00Z&end=2026-03-10T00:00:00Z",
            headers=auth_header,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["revenue"] == 100.0
        assert data["discounts"] == 5.0
        assert data["shipping_fee"] == 8.0
        assert data["payment_gateway_fee"] == 3.0
        assert data["cogs_total"] == 20.0
        # net_profit is orders' own GENERATED column: 100 - 5 - 8 - 3 - 20 = 64
        assert data["net_profit"] == 64.0
        assert data["total_ad_spend"] == 10.0
        assert data["real_profit_after_ads"] == 54.0

    def test_empty_range_returns_zeros_not_an_error(self, client, auth_header, test_store):
        response = client.get(
            f"/stores/{test_store.id}/metrics/pnl"
            f"?start=2026-01-01T00:00:00Z&end=2026-01-31T00:00:00Z",
            headers=auth_header,
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data == {
            "revenue": 0.0,
            "discounts": 0.0,
            "shipping_fee": 0.0,
            "payment_gateway_fee": 0.0,
            "cogs_total": 0.0,
            "net_profit": 0.0,
            "total_ad_spend": 0.0,
            "real_profit_after_ads": 0.0,
        }
