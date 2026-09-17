"""Tests for Web Push notifications (app/services/push.py, app/routes/push.py)."""
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import status
from pywebpush import WebPushException

from app.models import PushSubscription
from app.services.push import send_push_to_user


class TestSendPushToUser:
    def test_no_vapid_key_configured_does_not_attempt_to_send(self, monkeypatch, test_db_session, test_user):
        monkeypatch.setenv("VAPID_PRIVATE_KEY", "")
        with patch("app.services.push.webpush") as mock_webpush:
            send_push_to_user(test_db_session, test_user.id, "Hi", "Body text")
        mock_webpush.assert_not_called()

    def test_configured_vapid_sends_to_every_subscription(self, monkeypatch, test_db_session, test_user):
        monkeypatch.setenv("VAPID_PRIVATE_KEY", "fake-private-key")
        monkeypatch.setenv("VAPID_CLAIM_EMAIL", "admin@example.com")
        test_db_session.add(
            PushSubscription(
                id=uuid4(),
                user_id=test_user.id,
                endpoint="https://push.example.com/abc",
                p256dh_key="p256dh-value",
                auth_key="auth-value",
            )
        )
        test_db_session.commit()

        with patch("app.services.push.webpush") as mock_webpush:
            send_push_to_user(test_db_session, test_user.id, "Hi", "Body text")

        mock_webpush.assert_called_once()
        kwargs = mock_webpush.call_args.kwargs
        assert kwargs["subscription_info"]["endpoint"] == "https://push.example.com/abc"
        assert kwargs["subscription_info"]["keys"] == {"p256dh": "p256dh-value", "auth": "auth-value"}
        assert kwargs["vapid_claims"] == {"sub": "mailto:admin@example.com"}

    def test_dead_subscription_is_removed_on_404(self, monkeypatch, test_db_session, test_user):
        monkeypatch.setenv("VAPID_PRIVATE_KEY", "fake-private-key")
        sub_id = uuid4()
        test_db_session.add(
            PushSubscription(
                id=sub_id,
                user_id=test_user.id,
                endpoint="https://push.example.com/gone",
                p256dh_key="p256dh-value",
                auth_key="auth-value",
            )
        )
        test_db_session.commit()

        fake_response = MagicMock(status_code=410)
        with patch("app.services.push.webpush", side_effect=WebPushException("gone", response=fake_response)):
            send_push_to_user(test_db_session, test_user.id, "Hi", "Body text")

        assert test_db_session.get(PushSubscription, sub_id) is None


@pytest.mark.db
class TestPushRoutes:
    def test_vapid_public_key_defaults_to_empty_when_unconfigured(self, client, auth_header):
        response = client.get("/push/vapid-public-key", headers=auth_header)
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"public_key": ""}

    def test_requires_auth(self, client):
        assert client.get("/push/vapid-public-key").status_code == status.HTTP_401_UNAUTHORIZED
        assert (
            client.post("/push/subscribe", json={"endpoint": "e", "keys": {"p256dh": "a", "auth": "b"}}).status_code
            == status.HTTP_401_UNAUTHORIZED
        )

    def test_subscribe_creates_a_row(self, client, auth_header, test_db_session, test_user):
        response = client.post(
            "/push/subscribe",
            headers=auth_header,
            json={"endpoint": "https://push.example.com/xyz", "keys": {"p256dh": "p1", "auth": "a1"}},
        )
        assert response.status_code == status.HTTP_204_NO_CONTENT

        row = test_db_session.query(PushSubscription).filter_by(user_id=test_user.id).one()
        assert row.endpoint == "https://push.example.com/xyz"
        assert row.p256dh_key == "p1"
        assert row.auth_key == "a1"

    def test_subscribe_twice_with_same_endpoint_updates_instead_of_duplicating(
        self, client, auth_header, test_db_session, test_user
    ):
        endpoint = "https://push.example.com/same"
        client.post(
            "/push/subscribe", headers=auth_header, json={"endpoint": endpoint, "keys": {"p256dh": "old", "auth": "old"}}
        )
        client.post(
            "/push/subscribe", headers=auth_header, json={"endpoint": endpoint, "keys": {"p256dh": "new", "auth": "new"}}
        )

        rows = test_db_session.query(PushSubscription).filter_by(user_id=test_user.id, endpoint=endpoint).all()
        assert len(rows) == 1
        assert rows[0].p256dh_key == "new"

    def test_unsubscribe_removes_the_row(self, client, auth_header, test_db_session, test_user):
        endpoint = "https://push.example.com/bye"
        client.post(
            "/push/subscribe", headers=auth_header, json={"endpoint": endpoint, "keys": {"p256dh": "p", "auth": "a"}}
        )

        response = client.request("DELETE", "/push/subscribe", headers=auth_header, params={"endpoint": endpoint})
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert test_db_session.query(PushSubscription).filter_by(user_id=test_user.id, endpoint=endpoint).first() is None

    def test_subscribe_captures_user_agent(self, client, auth_header, test_db_session, test_user):
        client.post(
            "/push/subscribe",
            headers={**auth_header, "User-Agent": "Mozilla/5.0 (iPhone) Safari/604.1"},
            json={"endpoint": "https://push.example.com/ua", "keys": {"p256dh": "p", "auth": "a"}},
        )
        row = test_db_session.query(PushSubscription).filter_by(user_id=test_user.id, endpoint="https://push.example.com/ua").one()
        assert "iPhone" in row.user_agent


@pytest.mark.db
class TestListAndRevokeSubscriptions:
    def test_list_returns_a_friendly_label_and_no_other_users_devices(
        self, client, auth_header, admin_auth_header, test_db_session
    ):
        client.post(
            "/push/subscribe",
            headers={**auth_header, "User-Agent": "Mozilla/5.0 (Windows NT 10.0) Chrome/120.0 Safari/537.36"},
            json={"endpoint": "https://push.example.com/mine", "keys": {"p256dh": "p", "auth": "a"}},
        )
        client.post(
            "/push/subscribe",
            headers=admin_auth_header,
            json={"endpoint": "https://push.example.com/not-mine", "keys": {"p256dh": "p", "auth": "a"}},
        )

        response = client.get("/push/subscriptions", headers=auth_header)
        assert response.status_code == status.HTTP_200_OK
        subs = response.json()
        assert len(subs) == 1
        assert subs[0]["label"] == "Windows · Chrome"

    def test_revoke_by_id_removes_a_different_devices_row(self, client, auth_header, test_db_session, test_user):
        client.post(
            "/push/subscribe",
            headers=auth_header,
            json={"endpoint": "https://push.example.com/other-device", "keys": {"p256dh": "p", "auth": "a"}},
        )
        sub_id = (
            test_db_session.query(PushSubscription)
            .filter_by(user_id=test_user.id, endpoint="https://push.example.com/other-device")
            .one()
            .id
        )

        response = client.delete(f"/push/subscriptions/{sub_id}", headers=auth_header)
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert test_db_session.get(PushSubscription, sub_id) is None

    def test_cannot_revoke_another_users_subscription(self, client, auth_header, admin_auth_header, test_db_session):
        client.post(
            "/push/subscribe",
            headers=admin_auth_header,
            json={"endpoint": "https://push.example.com/admins", "keys": {"p256dh": "p", "auth": "a"}},
        )
        sub_id = test_db_session.query(PushSubscription).filter_by(endpoint="https://push.example.com/admins").one().id

        response = client.delete(f"/push/subscriptions/{sub_id}", headers=auth_header)
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert test_db_session.get(PushSubscription, sub_id) is not None
