"""GET /stores/{id}/metrics/daily against real data: rows show up as soon as
orders are ingested, including a backfill weeks in the past (the daily
chart used to read a continuous aggregate that missed both)."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import status


@pytest.mark.db
class TestDailyMetrics:
    def test_daily_rows_right_after_ingest_including_a_backfill(self, client, auth_header, test_store):
        today = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
        backfill_day = today - timedelta(days=40)
        orders = [
            {"order_id": "d-old", "time": backfill_day.isoformat(), "gross_amount": 80.0, "cogs_total": 30.0, "currency": "USD"},
            {"order_id": "d-new-1", "time": today.isoformat(), "gross_amount": 100.0, "cogs_total": 40.0, "currency": "USD"},
            {"order_id": "d-new-2", "time": today.isoformat(), "gross_amount": 50.0, "cogs_total": 10.0, "currency": "USD"},
        ]
        assert client.post(f"/stores/{test_store.id}/orders", headers=auth_header, json=orders).status_code == status.HTTP_201_CREATED
        spend = [{"time": today.isoformat(), "platform": "meta", "campaign_id": "c1", "spend": 25.0}]
        assert client.post(f"/stores/{test_store.id}/ad-spend", headers=auth_header, json=spend).status_code == status.HTTP_201_CREATED

        response = client.get(
            f"/stores/{test_store.id}/metrics/daily",
            headers=auth_header,
            params={"start": (today - timedelta(days=60)).isoformat(), "end": (today + timedelta(hours=1)).isoformat()},
        )

        assert response.status_code == status.HTTP_200_OK
        rows = response.json()
        assert len(rows) == 2
        old, new = rows
        assert old["total_orders"] == 1
        assert old["total_revenue"] == pytest.approx(80.0)
        assert old["ad_spend"] == pytest.approx(0.0)
        assert new["total_orders"] == 2
        assert new["total_revenue"] == pytest.approx(150.0)
        assert new["total_net_profit"] == pytest.approx(100.0)
        assert new["ad_spend"] == pytest.approx(25.0)
