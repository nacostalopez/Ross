"""Tests for the outgoing-email helper (app/email.py)."""
from unittest.mock import MagicMock, patch

import pytest

from app.email import send_email


class TestSendEmail:
    def test_no_smtp_host_configured_does_not_attempt_to_send(self, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "")
        with patch("app.email.smtplib.SMTP") as mock_smtp:
            send_email(to="someone@example.com", subject="Hi", body="Body text")
        mock_smtp.assert_not_called()

    def test_production_never_logs_the_body_of_an_unsent_email(self, monkeypatch, caplog):
        """Reset and invite links are in the body; in production they must not reach the logs."""
        from app.config import settings

        monkeypatch.setenv("SMTP_HOST", "")
        monkeypatch.setattr(settings, "environment", "production")
        with caplog.at_level("INFO", logger="ross.email"):
            send_email(to="someone@example.com", subject="Restablecé tu contraseña", body="https://ross.ar/?reset=SECRET")

        record = caplog.records[-1]
        assert record.getMessage() == "email_not_sent_no_smtp_configured"
        assert not hasattr(record, "body")
        assert not hasattr(record, "to")
        assert "SECRET" not in caplog.text

    def test_configured_smtp_sends_message(self, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_PORT", "587")
        monkeypatch.setenv("SMTP_USERNAME", "user@example.com")
        monkeypatch.setenv("SMTP_PASSWORD", "secret")
        monkeypatch.setenv("SMTP_FROM_EMAIL", "ross@aramal.co")

        mock_server = MagicMock()
        mock_server.__enter__.return_value = mock_server

        with patch("app.email.smtplib.SMTP", return_value=mock_server) as mock_smtp:
            send_email(to="someone@example.com", subject="Hi", body="Body text")

        mock_smtp.assert_called_once_with("smtp.example.com", 587)
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with("user@example.com", "secret")
        mock_server.send_message.assert_called_once()

        sent_message = mock_server.send_message.call_args[0][0]
        assert sent_message["To"] == "someone@example.com"
        assert sent_message["Subject"] == "Hi"
        assert sent_message["From"] == "ross@aramal.co"


@pytest.mark.db
class TestCreateInviteWithNoSmtpConfigured:
    def test_invite_still_succeeds_without_smtp(self, client, auth_header):
        """SMTP_HOST is unset in the test environment — create_invite must
        still 201 and return a usable token (email is best-effort/logged)."""
        response = client.post(
            "/accounts/invites",
            headers=auth_header,
            json={"email": "smtp-fallback@example.com", "role": "viewer"},
        )
        assert response.status_code == 201
        assert response.json()["token"]
