"""Every metrics endpoint, against one fixed dataset, compared with a stored snapshot.

The metrics are moving out of the route into a service (docs/ia/fase-4-1-diseno.md). The unit tests
pin each figure one at a time; this pins the whole response of every endpoint at once, so a number that
moves while the code moves shows up as a diff instead of going unnoticed.

To record the snapshot (only when a figure is meant to change):
    UPDATE_METRICS_SNAPSHOT=1 pytest tests/test_metrics_snapshot.py
"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import status

SNAPSHOT = Path(__file__).parent / "snapshots" / "metrics_endpoints.json"

RANGE = "start=2026-01-01T00:00:00Z&end=2026-05-01T00:00:00Z"


def _orders():
    # Three customers on three channels, one of them buying again on another channel and in another month.
    rows = [
        ("o-1", "2026-01-05T10:00:00Z", 100.0, 5.0, 8.0, 3.0, 20.0, "ana@example.com", "meta"),
        ("o-2", "2026-01-18T15:30:00Z", 60.0, 0.0, 6.0, 2.0, 12.0, "ana@example.com", "meta"),
        ("o-3", "2026-02-02T09:00:00Z", 80.0, 10.0, 7.0, 3.0, 25.0, "beto@example.com", "google"),
        ("o-4", "2026-02-20T18:45:00Z", 45.0, 0.0, 5.0, 2.0, 9.0, "ana@example.com", "google"),
        ("o-5", "2026-03-07T12:00:00Z", 120.0, 12.0, 9.0, 4.0, 40.0, "carla@example.com", None),
        ("o-6", "2026-03-07T20:00:00Z", 35.0, 0.0, 4.0, 1.0, 8.0, "beto@example.com", "tiktok"),
        ("o-7", "2026-04-11T08:15:00Z", 150.0, 15.0, 10.0, 5.0, 55.0, "dani@example.com", "meta"),
    ]
    out = []
    for order_id, time, gross, discounts, shipping, gateway, cogs, email, utm in rows:
        row = {
            "order_id": order_id,
            "time": time,
            "gross_amount": gross,
            "discounts": discounts,
            "shipping_fee": shipping,
            "payment_gateway_fee": gateway,
            "cogs_total": cogs,
            "currency": "USD",
            "customer_email": email,
        }
        if utm:
            row["attribution_utm_source"] = utm
        out.append(row)
    return out


def _ad_spend():
    return [
        {"time": "2026-01-02T00:00:00Z", "platform": "meta", "campaign_id": "c1", "spend": 40.0},
        {"time": "2026-01-20T00:00:00Z", "platform": "google", "campaign_id": "c2", "spend": 15.0},
        {"time": "2026-02-01T00:00:00Z", "platform": "meta", "campaign_id": "c1", "spend": 30.0},
        {"time": "2026-02-15T00:00:00Z", "platform": "google", "campaign_id": "c2", "spend": 22.5},
        {"time": "2026-03-05T00:00:00Z", "platform": "tiktok", "campaign_id": "c3", "spend": 18.0},
        {"time": "2026-04-10T00:00:00Z", "platform": "meta", "campaign_id": "c1", "spend": 55.0},
    ]


def _creatives():
    return [
        {
            "time": "2026-01-05T00:00:00Z", "platform": "meta", "campaign_id": "c1", "campaign_name": "Camp",
            "ad_id": "ad-a", "ad_name": "Creative A", "spend": 40.0, "impressions": 4000, "clicks": 80,
        },
        {
            "time": "2026-02-05T00:00:00Z", "platform": "meta", "campaign_id": "c1", "campaign_name": "Camp",
            "ad_id": "ad-a", "ad_name": "Creative A", "spend": 30.0, "impressions": 2500, "clicks": 40,
        },
        {
            "time": "2026-02-06T00:00:00Z", "platform": "google", "campaign_id": "c2", "campaign_name": "Search",
            "ad_id": "ad-b", "ad_name": "Creative B", "spend": 22.5, "impressions": 0, "clicks": 0,
        },
    ]


def _forecast_inputs(now):
    # Relative to now because the route forecasts from the present. Revenue climbs 10 a day.
    orders, spend = [], []
    for i in range(12, 0, -1):
        day = now - timedelta(days=i)
        orders.append(
            {"order_id": f"f-{i}", "time": day.isoformat(), "gross_amount": (12 - i) * 10.0 + 50.0, "currency": "USD"}
        )
        spend.append({"time": day.isoformat(), "platform": "meta", "campaign_id": "cf", "spend": 20.0})
    return orders, spend


def _normalize(value):
    """Round floats so the snapshot doesn't depend on the last digit of a float, and drop the dates of the forecast."""
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items()}
    return value


@pytest.mark.db
def test_every_metrics_endpoint_matches_the_snapshot(client, auth_header, test_store):
    base = f"/stores/{test_store.id}"

    def post(path, rows):
        assert client.post(f"{base}/{path}", headers=auth_header, json=rows).status_code == status.HTTP_201_CREATED

    post("orders", _orders())
    post("ad-spend", _ad_spend())
    post("creative-performance", _creatives())

    now = datetime.now(timezone.utc)
    forecast_orders, forecast_spend = _forecast_inputs(now)
    post("orders", forecast_orders)
    post("ad-spend", forecast_spend)

    paths = {
        "summary": f"/metrics/summary?{RANGE}",
        "pnl": f"/metrics/pnl?{RANGE}",
        "daily": f"/metrics/daily?{RANGE}",
        "creatives": f"/metrics/creatives?{RANGE}",
        "ltv_cohorts": f"/metrics/ltv-cohorts?{RANGE}&months=4",
        "cac_by_channel": f"/metrics/cac-by-channel?{RANGE}",
        "attribution_by_channel": f"/metrics/attribution-by-channel?{RANGE}",
        "forecast": "/metrics/forecast?history_days=30&forecast_days=7",
    }
    actual = {}
    for name, path in paths.items():
        response = client.get(f"{base}{path}", headers=auth_header)
        assert response.status_code == status.HTTP_200_OK, (name, response.text)
        actual[name] = response.json()

    # The forecast days are dates counted from today; keep their position and drop the date itself.
    actual["forecast"]["days"] = [{k: v for k, v in day.items() if k != "day"} for day in actual["forecast"]["days"]]
    actual = _normalize(actual)

    if os.getenv("UPDATE_METRICS_SNAPSHOT"):
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_text(json.dumps(actual, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        return

    assert SNAPSHOT.exists(), "No snapshot yet: run once with UPDATE_METRICS_SNAPSHOT=1"
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    for name in paths:
        assert actual[name] == expected[name], f"/metrics/{name} changed"
