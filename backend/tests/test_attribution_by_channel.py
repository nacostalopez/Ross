"""Tests for GET /metrics/attribution-by-channel — multi-touch counterpart
to CAC by channel (see test_cac_by_channel.py): every order in the range
counts toward its own channel, instead of the whole customer being credited
to their first order's channel only.
"""

import pytest
from fastapi import status


@pytest.mark.db
class TestAttributionByChannel:
    def _seed_orders(self, client, auth_header, store_id, rows):
        response = client.post(f"/stores/{store_id}/orders", headers=auth_header, json=rows)
        assert response.status_code == status.HTTP_201_CREATED

    def _seed_ad_spend(self, client, auth_header, store_id, rows):
        response = client.post(f"/stores/{store_id}/ad-spend", headers=auth_header, json=rows)
        assert response.status_code == status.HTTP_201_CREATED

    def test_second_order_channel_counts_as_a_repeat_order_on_its_own_channel(
        self, client, auth_header, test_store
    ):
        """Unlike CAC-by-channel, a later order on a different channel is
        not swallowed by the customer's first-order channel — it shows up
        under its own channel as a repeat order."""
        self._seed_orders(
            client,
            auth_header,
            test_store.id,
            [
                {
                    "order_id": "order-1",
                    "time": "2026-07-05T00:00:00Z",
                    "gross_amount": 60.0,
                    "currency": "USD",
                    "customer_email": "loyal@example.com",
                    "attribution_utm_source": "meta",
                },
                {
                    "order_id": "order-2",
                    "time": "2026-07-20T00:00:00Z",
                    "gross_amount": 20.0,
                    "currency": "USD",
                    "customer_email": "loyal@example.com",
                    "attribution_utm_source": "google",
                },
            ],
        )

        response = client.get(
            f"/stores/{test_store.id}/metrics/attribution-by-channel"
            "?start=2026-07-01T00:00:00Z&end=2026-07-31T00:00:00Z",
            headers=auth_header,
        )
        assert response.status_code == status.HTTP_200_OK
        data = {row["channel"]: row for row in response.json()}

        assert data["meta"]["orders"] == 1
        assert data["meta"]["repeat_orders"] == 0
        assert data["meta"]["revenue"] == 60.0

        assert data["google"]["orders"] == 1
        assert data["google"]["repeat_orders"] == 1
        assert data["google"]["revenue"] == 20.0

    def test_repeat_order_counts_even_when_acquisition_order_is_outside_the_range(
        self, client, auth_header, test_store
    ):
        """Period-based, not cohort-based: a customer acquired before
        :start still has their in-range repeat order attributed, unlike
        CAC-by-channel/LTV-cohorts which filter by customers.first_order_at."""
        self._seed_orders(
            client,
            auth_header,
            test_store.id,
            [
                {
                    "order_id": "order-1",
                    "time": "2026-08-05T00:00:00Z",
                    "gross_amount": 60.0,
                    "currency": "USD",
                    "customer_email": "returning@example.com",
                    "attribution_utm_source": "meta",
                },
                {
                    "order_id": "order-2",
                    "time": "2026-09-05T00:00:00Z",
                    "gross_amount": 30.0,
                    "currency": "USD",
                    "customer_email": "returning@example.com",
                    "attribution_utm_source": "google",
                },
            ],
        )

        response = client.get(
            f"/stores/{test_store.id}/metrics/attribution-by-channel"
            "?start=2026-09-01T00:00:00Z&end=2026-09-30T00:00:00Z",
            headers=auth_header,
        )
        data = response.json()
        assert len(data) == 1
        assert data[0]["channel"] == "google"
        assert data[0]["orders"] == 1
        assert data[0]["repeat_orders"] == 1
        assert data[0]["revenue"] == 30.0

    def test_unrecognized_utm_source_falls_back_to_other_with_no_spend(self, client, auth_header, test_store):
        self._seed_orders(
            client,
            auth_header,
            test_store.id,
            [
                {
                    "order_id": "order-1",
                    "time": "2026-10-05T00:00:00Z",
                    "gross_amount": 30.0,
                    "currency": "USD",
                    "customer_email": "organic@example.com",
                    "attribution_utm_source": "newsletter",
                },
            ],
        )

        response = client.get(
            f"/stores/{test_store.id}/metrics/attribution-by-channel"
            "?start=2026-10-01T00:00:00Z&end=2026-10-31T00:00:00Z",
            headers=auth_header,
        )
        data = response.json()
        assert len(data) == 1
        assert data[0]["channel"] == "other"
        assert data[0]["spend"] is None
        assert data[0]["roas"] is None

    def test_roas_computed_from_spend_and_attributed_revenue(self, client, auth_header, test_store):
        self._seed_orders(
            client,
            auth_header,
            test_store.id,
            [
                {
                    "order_id": "order-1",
                    "time": "2026-11-05T00:00:00Z",
                    "gross_amount": 100.0,
                    "currency": "USD",
                    "customer_email": "meta-buyer@example.com",
                    "attribution_utm_source": "meta",
                },
            ],
        )
        self._seed_ad_spend(
            client,
            auth_header,
            test_store.id,
            [
                {"time": "2026-11-01T00:00:00Z", "platform": "meta", "campaign_id": "c1", "spend": 25.0},
            ],
        )

        response = client.get(
            f"/stores/{test_store.id}/metrics/attribution-by-channel"
            "?start=2026-11-01T00:00:00Z&end=2026-11-30T00:00:00Z",
            headers=auth_header,
        )
        data = {row["channel"]: row for row in response.json()}
        assert data["meta"]["spend"] == 25.0
        assert data["meta"]["roas"] == 4.0
