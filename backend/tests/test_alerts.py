"""Tests for the proactive CAC/ROAS alert feature (app/services/alerts.py,
app/routes/alerts.py).

_roas_streak_is_bad (the decision rule) is a pure function tested
directly; check_roas_alert's orchestration (dedupe/cooldown/email) is
tested by monkeypatching that function's result; and TestCheckRoasAlert-
EndToEnd runs the whole thing on real orders and ad spend, now that
DAILY_SQL reads them directly instead of a continuous aggregate that only
refreshed on its own schedule.
"""
from datetime import datetime, timezone

import pytest
from fastapi import status

from app.models import AlertLog, StoreAlertPreference
from app.services import alerts as alerts_service
from app.services.alerts import (
    _roas_streak_is_bad,
    check_cac_alerts,
    check_roas_alert,
    run_check_for_store,
)


def _seed_orders(client, auth_header, store_id, rows):
    response = client.post(f"/stores/{store_id}/orders", headers=auth_header, json=rows)
    assert response.status_code == status.HTTP_201_CREATED


def _seed_ad_spend(client, auth_header, store_id, rows):
    response = client.post(f"/stores/{store_id}/ad-spend", headers=auth_header, json=rows)
    assert response.status_code == status.HTTP_201_CREATED


def _prefs(store_id, **overrides):
    defaults = dict(store_id=store_id, enabled=True, cac_threshold=None, roas_threshold=1.0, roas_days_n=3)
    defaults.update(overrides)
    return StoreAlertPreference(**defaults)


@pytest.mark.db
class TestAlertPreferencesRoute:
    def test_get_defaults_when_no_row_saved(self, client, auth_header, test_store):
        response = client.get(f"/stores/{test_store.id}/alert-preferences", headers=auth_header)
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "enabled": False,
            "cac_threshold": None,
            "roas_threshold": 1.0,
            "roas_days_n": 3,
        }

    def test_put_persists_and_get_reflects_it(self, client, auth_header, test_store):
        payload = {"enabled": True, "cac_threshold": 25.0, "roas_threshold": 1.2, "roas_days_n": 5}
        put_response = client.put(f"/stores/{test_store.id}/alert-preferences", headers=auth_header, json=payload)
        assert put_response.status_code == status.HTTP_200_OK
        assert put_response.json() == payload

        get_response = client.get(f"/stores/{test_store.id}/alert-preferences", headers=auth_header)
        assert get_response.json() == payload

    def test_viewer_cannot_change_preferences(self, client, test_store, test_db_session, viewer_user):
        login = client.post("/auth/login", json={"email": "viewer@example.com", "password": "viewerpassword123"})
        viewer_header = {"Authorization": f"Bearer {login.json()['access_token']}"}

        response = client.put(
            f"/stores/{test_store.id}/alert-preferences",
            headers=viewer_header,
            json={"enabled": True, "cac_threshold": None, "roas_threshold": 1.0, "roas_days_n": 3},
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.db
class TestCheckCacAlerts:
    def test_fires_once_per_channel_per_month_over_threshold(self, client, auth_header, test_store, test_db_session):
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
                    "customer_email": "meta-buyer@example.com",
                    "attribution_utm_source": "meta",
                },
            ],
        )
        _seed_ad_spend(
            client,
            auth_header,
            test_store.id,
            [{"time": "2026-03-01T00:00:00Z", "platform": "meta", "campaign_id": "c1", "spend": 40.0}],
        )

        prefs = _prefs(test_store.id, cac_threshold=30.0)
        now = datetime(2026, 3, 20, tzinfo=timezone.utc)

        fired = check_cac_alerts(test_db_session, test_store, prefs, now)
        assert len(fired) == 1
        assert "meta" in fired[0]
        assert test_db_session.query(AlertLog).filter_by(store_id=test_store.id, alert_type="cac").count() == 1

        # Same month, same channel, still over threshold — must not resend.
        fired_again = check_cac_alerts(test_db_session, test_store, prefs, now)
        assert fired_again == []
        assert test_db_session.query(AlertLog).filter_by(store_id=test_store.id, alert_type="cac").count() == 1

    def test_no_alert_when_under_threshold(self, client, auth_header, test_store, test_db_session):
        _seed_orders(
            client,
            auth_header,
            test_store.id,
            [
                {
                    "order_id": "order-1",
                    "time": "2026-04-05T00:00:00Z",
                    "gross_amount": 100.0,
                    "currency": "USD",
                    "customer_email": "cheap@example.com",
                    "attribution_utm_source": "meta",
                },
            ],
        )
        _seed_ad_spend(
            client,
            auth_header,
            test_store.id,
            [{"time": "2026-04-01T00:00:00Z", "platform": "meta", "campaign_id": "c1", "spend": 10.0}],
        )

        prefs = _prefs(test_store.id, cac_threshold=30.0)
        fired = check_cac_alerts(test_db_session, test_store, prefs, datetime(2026, 4, 20, tzinfo=timezone.utc))
        assert fired == []

    def test_no_alert_when_threshold_not_configured(self, client, auth_header, test_store, test_db_session):
        _seed_orders(
            client,
            auth_header,
            test_store.id,
            [
                {
                    "order_id": "order-1",
                    "time": "2026-05-05T00:00:00Z",
                    "gross_amount": 100.0,
                    "currency": "USD",
                    "customer_email": "whoever@example.com",
                    "attribution_utm_source": "meta",
                },
            ],
        )
        _seed_ad_spend(
            client,
            auth_header,
            test_store.id,
            [{"time": "2026-05-01T00:00:00Z", "platform": "meta", "campaign_id": "c1", "spend": 9999.0}],
        )

        prefs = _prefs(test_store.id, cac_threshold=None)
        fired = check_cac_alerts(test_db_session, test_store, prefs, datetime(2026, 5, 20, tzinfo=timezone.utc))
        assert fired == []


class TestRoasStreakIsBad:
    def _day(self, net_profit, ad_spend):
        return {"total_net_profit": net_profit, "ad_spend": ad_spend}

    def test_true_when_last_n_days_all_below_threshold(self):
        rows = [self._day(50, 100), self._day(40, 100), self._day(30, 100)]
        assert _roas_streak_is_bad(rows, threshold=1.0, days_n=3) is True

    def test_false_when_one_good_day_in_the_window(self):
        rows = [self._day(50, 100), self._day(150, 100), self._day(30, 100)]
        assert _roas_streak_is_bad(rows, threshold=1.0, days_n=3) is False

    def test_false_with_fewer_spend_days_than_required(self):
        rows = [self._day(50, 100), self._day(40, 100)]
        assert _roas_streak_is_bad(rows, threshold=1.0, days_n=3) is False

    def test_zero_spend_days_are_skipped_not_counted_as_bad(self):
        # Only 2 real spend days even though there are 4 rows — a 0-spend
        # day has no true ROAS to evaluate, so it doesn't fill a slot.
        rows = [self._day(50, 100), self._day(0, 0), self._day(40, 100)]
        assert _roas_streak_is_bad(rows, threshold=1.0, days_n=3) is False

    def test_exactly_at_threshold_is_not_bad(self):
        # < threshold, not <=  — exactly 1.0x ROAS isn't "below" it.
        rows = [self._day(100, 100), self._day(100, 100), self._day(100, 100)]
        assert _roas_streak_is_bad(rows, threshold=1.0, days_n=3) is False


@pytest.mark.db
class TestCheckRoasAlertOrchestration:
    """_roas_streak_is_bad is monkeypatched so these isolate dedupe and
    cooldown; TestCheckRoasAlertEndToEnd covers the real data path."""

    def test_sends_and_logs_when_streak_is_bad(self, test_db_session, test_store, monkeypatch):
        monkeypatch.setattr(alerts_service, "_roas_streak_is_bad", lambda *a, **k: True)
        prefs = _prefs(test_store.id)

        fired = check_roas_alert(test_db_session, test_store, prefs, datetime(2026, 6, 1, tzinfo=timezone.utc))

        assert fired is True
        assert test_db_session.query(AlertLog).filter_by(store_id=test_store.id, alert_type="roas").count() == 1

    def test_does_not_resend_within_cooldown(self, test_db_session, test_store, monkeypatch):
        monkeypatch.setattr(alerts_service, "_roas_streak_is_bad", lambda *a, **k: True)
        prefs = _prefs(test_store.id, roas_days_n=3)

        first = check_roas_alert(test_db_session, test_store, prefs, datetime(2026, 6, 1, tzinfo=timezone.utc))
        second = check_roas_alert(test_db_session, test_store, prefs, datetime(2026, 6, 2, tzinfo=timezone.utc))

        assert first is True
        assert second is False
        assert test_db_session.query(AlertLog).filter_by(store_id=test_store.id, alert_type="roas").count() == 1

    def test_resends_after_cooldown_expires(self, test_db_session, test_store, monkeypatch):
        monkeypatch.setattr(alerts_service, "_roas_streak_is_bad", lambda *a, **k: True)
        prefs = _prefs(test_store.id, roas_days_n=3)

        check_roas_alert(test_db_session, test_store, prefs, datetime(2026, 6, 1, tzinfo=timezone.utc))
        later = check_roas_alert(test_db_session, test_store, prefs, datetime(2026, 6, 10, tzinfo=timezone.utc))

        assert later is True
        assert test_db_session.query(AlertLog).filter_by(store_id=test_store.id, alert_type="roas").count() == 2

    def test_no_alert_when_streak_is_not_bad(self, test_db_session, test_store, monkeypatch):
        monkeypatch.setattr(alerts_service, "_roas_streak_is_bad", lambda *a, **k: False)
        prefs = _prefs(test_store.id)

        fired = check_roas_alert(test_db_session, test_store, prefs, datetime(2026, 6, 1, tzinfo=timezone.utc))

        assert fired is False
        assert test_db_session.query(AlertLog).count() == 0


@pytest.mark.db
class TestCheckRoasAlertEndToEnd:
    """No monkeypatching: orders and ad spend go in through the API and
    check_roas_alert reads them back through DAILY_SQL."""

    NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)

    def _seed_days(self, client, auth_header, store_id, cogs, spend):
        days = ["2026-05-29", "2026-05-30", "2026-05-31"]
        _seed_orders(
            client,
            auth_header,
            store_id,
            [
                {"order_id": f"roas-{day}", "time": f"{day}T12:00:00Z", "gross_amount": 100.0, "cogs_total": cogs, "currency": "USD"}
                for day in days
            ],
        )
        _seed_ad_spend(
            client,
            auth_header,
            store_id,
            [{"time": f"{day}T12:00:00Z", "platform": "meta", "campaign_id": "c1", "spend": spend} for day in days],
        )

    def test_fires_on_three_days_below_threshold(self, client, auth_header, test_store, test_db_session):
        # Net profit 50 against 100 of spend: true ROAS 0.5 each day, under 1.0.
        self._seed_days(client, auth_header, test_store.id, cogs=50.0, spend=100.0)

        fired = check_roas_alert(test_db_session, test_store, _prefs(test_store.id, roas_days_n=3), self.NOW)

        assert fired is True
        assert test_db_session.query(AlertLog).filter_by(store_id=test_store.id, alert_type="roas").count() == 1

    def test_quiet_when_roas_is_above_threshold(self, client, auth_header, test_store, test_db_session):
        # Net profit 80 against 20 of spend: true ROAS 4.0 each day.
        self._seed_days(client, auth_header, test_store.id, cogs=20.0, spend=20.0)

        fired = check_roas_alert(test_db_session, test_store, _prefs(test_store.id, roas_days_n=3), self.NOW)

        assert fired is False
        assert test_db_session.query(AlertLog).count() == 0


@pytest.mark.db
class TestRunCheckForStore:
    def test_noop_when_disabled(self, test_db_session, test_store):
        test_db_session.add(_prefs(test_store.id, enabled=False, cac_threshold=1.0))
        test_db_session.commit()

        result = run_check_for_store(test_db_session, test_store, now=datetime(2026, 6, 1, tzinfo=timezone.utc))

        assert result.cac_alerts_sent == []
        assert result.roas_alert_sent is False

    def test_noop_when_no_preferences_row(self, test_db_session, test_store):
        result = run_check_for_store(test_db_session, test_store, now=datetime(2026, 6, 1, tzinfo=timezone.utc))
        assert result.cac_alerts_sent == []
        assert result.roas_alert_sent is False


@pytest.mark.db
class TestCheckNowEndpoint:
    def test_check_now_runs_and_returns_result(self, client, auth_header, test_store):
        client.put(
            f"/stores/{test_store.id}/alert-preferences",
            headers=auth_header,
            json={"enabled": True, "cac_threshold": None, "roas_threshold": 1.0, "roas_days_n": 3},
        )

        response = client.post(f"/stores/{test_store.id}/alert-preferences/check-now", headers=auth_header)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"cac_alerts_sent": [], "roas_alert_sent": False}
