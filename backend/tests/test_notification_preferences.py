"""Tests for GET/PUT /notification-preferences and the per-channel gating
in app/services/notifications.py::send_to_store."""
from unittest.mock import patch

import pytest
from fastapi import status

from app.models import NotificationChannelPreference
from app.services.notifications import send_to_store


@pytest.mark.db
class TestNotificationPreferencesRoute:
    def test_defaults_to_both_channels_enabled_when_no_row_saved(self, client, auth_header):
        response = client.get("/notification-preferences", headers=auth_header)
        assert response.status_code == status.HTTP_200_OK
        prefs = {p["event_type"]: p for p in response.json()["preferences"]}
        assert set(prefs.keys()) == {"cac_alert", "roas_alert", "weekly_report"}
        assert all(p["email_enabled"] and p["push_enabled"] for p in prefs.values())

    def test_put_persists_and_get_reflects_it(self, client, auth_header):
        payload = {
            "preferences": [
                {"event_type": "cac_alert", "email_enabled": True, "push_enabled": False},
                {"event_type": "roas_alert", "email_enabled": False, "push_enabled": True},
                {"event_type": "weekly_report", "email_enabled": True, "push_enabled": True},
            ]
        }
        put_response = client.put("/notification-preferences", headers=auth_header, json=payload)
        assert put_response.status_code == status.HTTP_200_OK

        get_response = client.get("/notification-preferences", headers=auth_header)
        prefs = {p["event_type"]: p for p in get_response.json()["preferences"]}
        assert prefs["cac_alert"] == {"event_type": "cac_alert", "email_enabled": True, "push_enabled": False}
        assert prefs["roas_alert"] == {"event_type": "roas_alert", "email_enabled": False, "push_enabled": True}

    def test_second_put_updates_instead_of_duplicating(self, client, auth_header, test_db_session, test_user):
        client.put(
            "/notification-preferences",
            headers=auth_header,
            json={"preferences": [{"event_type": "cac_alert", "email_enabled": True, "push_enabled": True}]},
        )
        client.put(
            "/notification-preferences",
            headers=auth_header,
            json={"preferences": [{"event_type": "cac_alert", "email_enabled": False, "push_enabled": False}]},
        )
        rows = (
            test_db_session.query(NotificationChannelPreference)
            .filter_by(user_id=test_user.id, event_type="cac_alert")
            .all()
        )
        assert len(rows) == 1
        assert rows[0].email_enabled is False
        assert rows[0].push_enabled is False

    def test_requires_auth(self, client):
        assert client.get("/notification-preferences").status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.db
class TestSendToStoreChannelGating:
    def test_skips_push_when_disabled_for_event_type(self, test_db_session, test_store, test_user):
        test_db_session.add(
            NotificationChannelPreference(
                user_id=test_user.id, event_type="roas_alert", email_enabled=True, push_enabled=False
            )
        )
        test_db_session.commit()

        with patch("app.services.notifications.send_email") as mock_email, patch(
            "app.services.notifications.send_push_to_user"
        ) as mock_push:
            send_to_store(test_db_session, test_store, "subj", "body", event_type="roas_alert")

        mock_email.assert_called_once()
        mock_push.assert_not_called()

    def test_skips_email_when_disabled_for_event_type(self, test_db_session, test_store, test_user):
        test_db_session.add(
            NotificationChannelPreference(
                user_id=test_user.id, event_type="weekly_report", email_enabled=False, push_enabled=True
            )
        )
        test_db_session.commit()

        with patch("app.services.notifications.send_email") as mock_email, patch(
            "app.services.notifications.send_push_to_user"
        ) as mock_push:
            send_to_store(test_db_session, test_store, "subj", "body", event_type="weekly_report")

        mock_email.assert_not_called()
        mock_push.assert_called_once()

    def test_no_saved_row_sends_both_channels(self, test_db_session, test_store, test_user):
        with patch("app.services.notifications.send_email") as mock_email, patch(
            "app.services.notifications.send_push_to_user"
        ) as mock_push:
            send_to_store(test_db_session, test_store, "subj", "body", event_type="cac_alert")

        mock_email.assert_called_once()
        mock_push.assert_called_once()

    def test_preference_for_a_different_event_type_does_not_apply(self, test_db_session, test_store, test_user):
        # A push-disabled row for cac_alert must not affect roas_alert.
        test_db_session.add(
            NotificationChannelPreference(
                user_id=test_user.id, event_type="cac_alert", email_enabled=True, push_enabled=False
            )
        )
        test_db_session.commit()

        with patch("app.services.notifications.send_email") as mock_email, patch(
            "app.services.notifications.send_push_to_user"
        ) as mock_push:
            send_to_store(test_db_session, test_store, "subj", "body", event_type="roas_alert")

        mock_email.assert_called_once()
        mock_push.assert_called_once()
