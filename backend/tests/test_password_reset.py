"""Tests for the forgot-password / reset-password flow."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import status

from app.models import PasswordResetToken
from app.security import hash_token, verify_password


@pytest.mark.db
class TestForgotPassword:
    def test_known_email_returns_generic_message_and_creates_token(self, client, test_user, test_db_session):
        response = client.post("/auth/forgot-password", json={"email": "testuser@example.com"})

        assert response.status_code == status.HTTP_200_OK
        assert "message" in response.json()

        tokens = test_db_session.query(PasswordResetToken).filter_by(user_id=test_user.id).all()
        assert len(tokens) == 1

    def test_unknown_email_returns_same_generic_message(self, client):
        response = client.post("/auth/forgot-password", json={"email": "nobody@example.com"})

        assert response.status_code == status.HTTP_200_OK
        assert "message" in response.json()

    def test_unknown_email_creates_no_token(self, client, test_db_session):
        client.post("/auth/forgot-password", json={"email": "nobody@example.com"})
        assert test_db_session.query(PasswordResetToken).count() == 0


@pytest.mark.db
class TestResetPassword:
    def test_valid_token_resets_password_and_logs_in(self, client, test_user, test_db_session):
        # The raw token is only ever emailed (or logged, with no SMTP
        # configured) — never returned in the response — so tests insert a
        # reset row directly, the same way test_refresh_tokens.py exercises
        # invite acceptance via a token minted at the DB level.
        raw_token = "test-raw-reset-token"
        test_db_session.add(
            PasswordResetToken(
                user_id=test_user.id,
                token_hash=hash_token(raw_token),
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=60),
            )
        )
        test_db_session.commit()

        reset_response = client.post(
            "/auth/reset-password",
            json={"token": raw_token, "password": "brandnewpassword123"},
        )
        assert reset_response.status_code == status.HTTP_200_OK
        data = reset_response.json()
        assert data["access_token"]
        assert data["refresh_token"]

        test_db_session.refresh(test_user)
        assert verify_password("brandnewpassword123", test_user.hashed_password)

        login_response = client.post(
            "/auth/login",
            json={"email": "testuser@example.com", "password": "brandnewpassword123"},
        )
        assert login_response.status_code == status.HTTP_200_OK

    def test_used_token_is_rejected_on_second_use(self, client, test_user, test_db_session):
        raw_token = "reused-reset-token"
        test_db_session.add(
            PasswordResetToken(
                user_id=test_user.id,
                token_hash=hash_token(raw_token),
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=60),
            )
        )
        test_db_session.commit()

        first = client.post("/auth/reset-password", json={"token": raw_token, "password": "firstnewpass123"})
        assert first.status_code == status.HTTP_200_OK

        second = client.post("/auth/reset-password", json={"token": raw_token, "password": "secondnewpass123"})
        assert second.status_code == status.HTTP_400_BAD_REQUEST

    def test_expired_token_is_rejected(self, client, test_user, test_db_session):
        raw_token = "expired-reset-token"
        test_db_session.add(
            PasswordResetToken(
                user_id=test_user.id,
                token_hash=hash_token(raw_token),
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            )
        )
        test_db_session.commit()

        response = client.post("/auth/reset-password", json={"token": raw_token, "password": "somenewpass123"})
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_unknown_token_is_rejected(self, client):
        response = client.post(
            "/auth/reset-password",
            json={"token": "not-a-real-token", "password": "somenewpass123"},
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_reset_revokes_existing_refresh_tokens(self, client, test_user, test_db_session):
        login = client.post("/auth/login", json={"email": "testuser@example.com", "password": "testpassword123"})
        old_refresh_token = login.json()["refresh_token"]

        raw_token = "revoke-check-token"
        test_db_session.add(
            PasswordResetToken(
                user_id=test_user.id,
                token_hash=hash_token(raw_token),
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=60),
            )
        )
        test_db_session.commit()

        reset_response = client.post(
            "/auth/reset-password",
            json={"token": raw_token, "password": "brandnewpassword123"},
        )
        assert reset_response.status_code == status.HTTP_200_OK

        refresh_attempt = client.post("/auth/refresh", json={"refresh_token": old_refresh_token})
        assert refresh_attempt.status_code == status.HTTP_401_UNAUTHORIZED

    def test_short_password_is_rejected(self, client, test_user, test_db_session):
        raw_token = "short-password-token"
        test_db_session.add(
            PasswordResetToken(
                user_id=test_user.id,
                token_hash=hash_token(raw_token),
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=60),
            )
        )
        test_db_session.commit()

        response = client.post("/auth/reset-password", json={"token": raw_token, "password": "short"})
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
