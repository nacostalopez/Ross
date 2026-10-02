"""Tests for the weekly email report feature (app/services/reports.py,
app/routes/reports.py)."""
from datetime import datetime, timezone

import pytest
from fastapi import status

from app.models import StoreReportPreference
from app.services.reports import build_weekly_summary, run_report_for_store


def _seed_orders(client, auth_header, store_id, rows):
    response = client.post(f"/stores/{store_id}/orders", headers=auth_header, json=rows)
    assert response.status_code == status.HTTP_201_CREATED


def _seed_ad_spend(client, auth_header, store_id, rows):
    response = client.post(f"/stores/{store_id}/ad-spend", headers=auth_header, json=rows)
    assert response.status_code == status.HTTP_201_CREATED


@pytest.mark.db
class TestReportPreferencesRoute:
    def test_get_defaults_to_disabled_when_no_row_saved(self, client, auth_header, test_store):
        response = client.get(f"/stores/{test_store.id}/report-preferences", headers=auth_header)
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"enabled": False}

    def test_put_persists_and_get_reflects_it(self, client, auth_header, test_store):
        put_response = client.put(
            f"/stores/{test_store.id}/report-preferences", headers=auth_header, json={"enabled": True}
        )
        assert put_response.status_code == status.HTTP_200_OK
        assert put_response.json() == {"enabled": True}

        get_response = client.get(f"/stores/{test_store.id}/report-preferences", headers=auth_header)
        assert get_response.json() == {"enabled": True}

    def test_viewer_cannot_change_preferences(self, client, test_store, viewer_user):
        login = client.post("/auth/login", json={"email": "viewer@example.com", "password": "viewerpassword123"})
        viewer_header = {"Authorization": f"Bearer {login.json()['access_token']}"}

        response = client.put(
            f"/stores/{test_store.id}/report-preferences", headers=viewer_header, json={"enabled": True}
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.db
class TestBuildWeeklySummary:
    def test_includes_revenue_profit_and_true_roas(self, client, auth_header, test_store, test_db_session):
        _seed_orders(
            client,
            auth_header,
            test_store.id,
            [
                {
                    "order_id": "order-1",
                    "time": "2026-03-05T00:00:00Z",
                    "gross_amount": 100.0,
                    "currency": "USD",
                    "customer_email": "buyer@example.com",
                    "attribution_utm_source": "meta",
                },
            ],
        )
        _seed_ad_spend(
            client,
            auth_header,
            test_store.id,
            [{"time": "2026-03-04T00:00:00Z", "platform": "meta", "campaign_id": "c1", "spend": 50.0}],
        )

        summary = build_weekly_summary(test_db_session, test_store, datetime(2026, 3, 6, tzinfo=timezone.utc))

        assert test_store.name in summary
        assert "Ingresos: $100.00" in summary
        assert "Gasto en ads: $50.00" in summary
        assert "True ROAS: 2.00x" in summary
        assert "CAC por canal este mes:" in summary
        assert "meta: $50.00" in summary

    def test_no_cac_section_when_no_new_customers_this_month(self, client, test_store, test_db_session):
        summary = build_weekly_summary(test_db_session, test_store, datetime(2026, 3, 6, tzinfo=timezone.utc))
        assert "CAC por canal este mes:" not in summary
        assert "Ingresos: $0.00" in summary
        assert "True ROAS: —" in summary


@pytest.mark.db
class TestRunReportForStore:
    def test_noop_when_disabled(self, test_db_session, test_store):
        result = run_report_for_store(test_db_session, test_store, now=datetime(2026, 3, 6, tzinfo=timezone.utc))
        assert result is False

    def test_sends_when_enabled(self, test_db_session, test_store):
        test_db_session.add(StoreReportPreference(store_id=test_store.id, enabled=True))
        test_db_session.commit()

        result = run_report_for_store(test_db_session, test_store, now=datetime(2026, 3, 6, tzinfo=timezone.utc))
        assert result is True


@pytest.mark.db
class TestSendNowEndpoint:
    def test_send_now_returns_body_even_when_disabled(self, client, auth_header, test_store):
        response = client.post(f"/stores/{test_store.id}/report-preferences/send-now", headers=auth_header)
        assert response.status_code == status.HTTP_200_OK
        assert test_store.name in response.json()["body"]

    def test_viewer_cannot_send_now(self, client, test_store, viewer_user):
        login = client.post("/auth/login", json={"email": "viewer@example.com", "password": "viewerpassword123"})
        viewer_header = {"Authorization": f"Bearer {login.json()['access_token']}"}

        response = client.post(f"/stores/{test_store.id}/report-preferences/send-now", headers=viewer_header)
        assert response.status_code == status.HTTP_403_FORBIDDEN
