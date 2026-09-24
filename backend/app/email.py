"""Outgoing transactional email (invite emails, etc.) via generic SMTP.

Colocated-settings pattern, same as every connector in app/connectors/ —
works with zero setup (SMTP_HOST unset -> logs instead of sending, so local
dev and tests never need real credentials) and is opt-in for anything beyond
that (Gmail, SendGrid/Mailgun/SES SMTP, or any other SMTP server).
"""
import logging
import smtplib
from email.message import EmailMessage

from pydantic_settings import BaseSettings

logger = logging.getLogger("ross.email")


class EmailSettings(BaseSettings):
    smtp_host: str = ""  # empty = not configured, send_email() no-ops (logs only)
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = "ross@aramal.co"
    smtp_use_tls: bool = True

    class Config:
        env_file = ".env"


def send_email(to: str, subject: str, body: str) -> None:
    """Send a plain-text email. Raises on failure so callers can decide
    whether that should be fatal to the request (typically it shouldn't —
    email delivery is best-effort, not the only path to the content)."""
    settings = EmailSettings()

    if not settings.smtp_host:
        logger.info("email_not_sent_no_smtp_configured", extra={"to": to, "subject": subject, "body": body})
        return

    message = EmailMessage()
    message["From"] = settings.smtp_from_email
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
        if settings.smtp_use_tls:
            server.starttls()
        if settings.smtp_username:
            server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(message)

    logger.info("email_sent", extra={"to": to, "subject": subject})
