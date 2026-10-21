"""Tests for GET /metrics/summary — the headline figures the dashboard shows.

These pin the response itself (values and shape), not only that the route answers. The
metrics are about to move out of the route into a service, and the figures must not change.
"""
from datetime import datetime, timezone

import pytest
from fastapi import status


@pytest.mark.db
class TestSummaryEndpoint:
    def _seed_orders(self, client, auth_header, store_id, rows):
        response = client.post(f"/stores/{store_id}/orders", headers=auth_header, json=rows)
        assert response.status_code == status.HTTP_201_CREATED

    def _seed_ad_spend(self, client, auth_header, store_id, rows):
        response = client.post(f"/stores/{store_id}/ad-spend", headers=auth_header, json=rows)
        assert response.status_code == status.HTTP_201_CREATED

    def _summary(self, client, auth_header, store_id, start, end):
        return client.get(f"/stores/{store_id}/metrics/summary?start={start}&end={end}", headers=auth_header)

    def test_returns_the_net_figures_and_true_roas(self, client, auth_header, test_store):
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
                {
                    "order_id": "order-2",
                    "time": datetime(2026, 3, 6, tzinfo=timezone.utc).isoformat(),
                    "gross_amount": 50.0,
                    "discounts": 0.0,
                    "shipping_fee": 5.0,
                    "payment_gateway_fee": 2.0,
                    "cogs_total": 10.0,
                    "currency": "USD",
                },
            ],
        )
        self._seed_ad_spend(
            client,
            auth_header,
            test_store.id,
            [
                {"time": datetime(2026, 3, 4, tzinfo=timezone.utc).isoformat(), "platform": "meta", "campaign_id": "c1", "spend": 10.0},
                {"time": datetime(2026, 3, 5, tzinfo=timezone.utc).isoformat(), "platform": "google", "campaign_id": "c2", "spend": 20.0},
            ],
        )

        response = self._summary(client, auth_header, test_store.id, "2026-03-01T00:00:00Z", "2026-03-10T00:00:00Z")

        assert response.status_code == status.HTTP_200_OK
        # net_profit per order: 100-5-8-3-20 = 64 and 50-0-5-2-10 = 33, so 97 in total.
        assert response.json() == {
            "revenue": 150.0,
            "net_profit": 97.0,
            "total_ad_spend": 30.0,
            "real_profit_after_ads": 67.0,
            # true ROAS = net_profit / ad spend = 97 / 30, rounded to two places.
            "true_roas": 3.23,
        }

    def test_true_roas_is_null_when_there_was_no_ad_spend(self, client, auth_header, test_store):
        self._seed_orders(
            client,
            auth_header,
            test_store.id,
            [
                {
                    "order_id": "order-1",
                    "time": datetime(2026, 3, 5, tzinfo=timezone.utc).isoformat(),
                    "gross_amount": 100.0,
                    "currency": "USD",
                }
            ],
        )

        response = self._summary(client, auth_header, test_store.id, "2026-03-01T00:00:00Z", "2026-03-10T00:00:00Z")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["revenue"] == 100.0
        assert data["total_ad_spend"] == 0.0
        assert data["true_roas"] is None

    def test_empty_range_returns_zeros_and_a_null_roas(self, client, auth_header, test_store):
        response = self._summary(client, auth_header, test_store.id, "2026-01-01T00:00:00Z", "2026-01-31T00:00:00Z")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "revenue": 0.0,
            "net_profit": 0.0,
            "total_ad_spend": 0.0,
            "real_profit_after_ads": 0.0,
            "true_roas": None,
        }

    def test_rows_outside_the_range_are_left_out(self, client, auth_header, test_store):
        self._seed_orders(
            client,
            auth_header,
            test_store.id,
            [
                {
                    "order_id": "old-order",
                    "time": datetime(2025, 6, 1, tzinfo=timezone.utc).isoformat(),
                    "gross_amount": 999.0,
                    "currency": "USD",
                }
            ],
        )

        response = self._summary(client, auth_header, test_store.id, "2026-01-01T00:00:00Z", "2026-01-31T00:00:00Z")

        assert response.json()["revenue"] == 0.0
