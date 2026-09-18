"""Tests for the personal "actividad reciente" feed (AccountActivityLog,
app/services/activity_log.py, GET /accounts/activity)."""
import pytest
from fastapi import status

from app.models import AccountActivityLog


@pytest.mark.db
class TestActivityLogging:
    def test_creating_a_store_logs_activity(self, client, auth_header, test_db_session, test_user):
        response = client.post("/stores", headers=auth_header, json={"name": "Nueva Tienda", "platform": "shopify"})
        assert response.status_code == status.HTTP_201_CREATED

        rows = test_db_session.query(AccountActivityLog).filter_by(user_id=test_user.id).all()
        assert len(rows) == 1
        assert rows[0].action == "store_created"
        assert "Nueva Tienda" in rows[0].detail

    def test_sending_an_invite_logs_activity(self, client, auth_header, test_db_session, test_user):
        response = client.post(
            "/accounts/invites", headers=auth_header, json={"email": "invitee@example.com", "role": "viewer"}
        )
        assert response.status_code == status.HTTP_201_CREATED

        rows = test_db_session.query(AccountActivityLog).filter_by(user_id=test_user.id, action="invite_sent").all()
        assert len(rows) == 1
        assert "invitee@example.com" in rows[0].detail

    def test_enabling_alerts_logs_activity_but_disabling_does_not(
        self, client, auth_header, test_store, test_db_session, test_user
    ):
        client.put(
            f"/stores/{test_store.id}/alert-preferences",
            headers=auth_header,
            json={"enabled": True, "cac_threshold": None, "roas_threshold": 1.0, "roas_days_n": 3},
        )
        client.put(
            f"/stores/{test_store.id}/alert-preferences",
            headers=auth_header,
            json={"enabled": False, "cac_threshold": None, "roas_threshold": 1.0, "roas_days_n": 3},
        )

        rows = (
            test_db_session.query(AccountActivityLog)
            .filter_by(user_id=test_user.id, action="alert_preferences_updated")
            .all()
        )
        assert len(rows) == 1

    def test_enabling_weekly_report_logs_activity(self, client, auth_header, test_store, test_db_session, test_user):
        client.put(f"/stores/{test_store.id}/report-preferences", headers=auth_header, json={"enabled": True})

        rows = (
            test_db_session.query(AccountActivityLog)
            .filter_by(user_id=test_user.id, action="report_preferences_updated")
            .all()
        )
        assert len(rows) == 1
        assert test_store.name in rows[0].detail


@pytest.mark.db
class TestActivityFeedEndpoint:
    def test_returns_newest_first_and_respects_limit(self, client, auth_header, test_db_session, test_user):
        for name in ["Tienda A", "Tienda B", "Tienda C"]:
            client.post("/stores", headers=auth_header, json={"name": name, "platform": "shopify"})

        response = client.get("/accounts/activity?limit=2", headers=auth_header)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 2
        assert "Tienda C" in data[0]["detail"]
        assert "Tienda B" in data[1]["detail"]

    def test_empty_when_nothing_logged_yet(self, client, auth_header):
        response = client.get("/accounts/activity", headers=auth_header)
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == []

    def test_only_returns_the_current_users_own_activity(
        self, client, auth_header, admin_auth_header, test_db_session
    ):
        client.post("/stores", headers=auth_header, json={"name": "Owner Store", "platform": "shopify"})
        client.post("/stores", headers=admin_auth_header, json={"name": "Admin Store", "platform": "shopify"})

        owner_activity = client.get("/accounts/activity", headers=auth_header).json()
        assert len(owner_activity) == 1
        assert "Owner Store" in owner_activity[0]["detail"]

    def test_requires_auth(self, client):
        assert client.get("/accounts/activity").status_code == status.HTTP_401_UNAUTHORIZED
