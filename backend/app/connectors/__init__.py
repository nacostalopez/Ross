"""Base classes and interfaces for connector implementations."""
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

# (connect, read) seconds for every call to a provider: a hung provider must fail in seconds, not
# hold a worker for as long as the connection stays open.
HTTP_TIMEOUT = (5, 30)


RECONNECT_MESSAGE = "La conexión venció o fue revocada: hay que volver a conectarla."


class ReconnectRequired(Exception):
    """The stored token is expired or was revoked and cannot be renewed: only the user, by connecting
    again, can fix it. Its text is what connector_status shows."""

    def __init__(self):
        super().__init__(RECONNECT_MESSAGE)


class ConnectorNotConfigured(Exception):
    """A connector's credentials or redirect URI are missing in this environment. The API answers
    503 with a message for the user; `missing` (env var names, never values) goes to the log."""

    def __init__(self, provider: str, missing: list):
        super().__init__(f"{provider} is not configured: missing {', '.join(missing)}")
        self.provider = provider
        self.missing = missing


def require_config(provider: str, **values) -> None:
    """Raise ConnectorNotConfigured if any value is blank. An empty client id used to produce a
    provider URL with `client_id=` and no error until the provider's own page refused it."""
    missing = [name for name, value in values.items() if not (value or "").strip()]
    if missing:
        raise ConnectorNotConfigured(provider, missing)


_URL_QUERY = re.compile(r"(https?://[^\s?'\"<>]+)\?[^\s'\"<>]*")
_SECRET_PARAM = re.compile(
    r"(?<![A-Za-z_])(access_token|refresh_token|client_secret|app_secret|fb_exchange_token|auth_code|code|secret)=[^&\s'\"<>]+",
    re.IGNORECASE,
)


def redact(error) -> str:
    """The text of an exception that is safe to store and show. `requests` puts the whole URL,
    query string included, in an HTTPError, and Meta takes tokens as query parameters: without this
    an access token or client secret ends up in connector_status and in the API's 400 body."""
    text = str(error) or type(error).__name__
    text = _URL_QUERY.sub(r"\g<1>?[redacted]", text)
    return _SECRET_PARAM.sub(r"\g<1>=[redacted]", text)


@dataclass
class OAuthToken:
    """OAuth token information."""
    access_token: str
    refresh_token: Optional[str] = None
    expires_at: Optional[datetime] = None
    token_type: str = "Bearer"


class BaseConnector(ABC):
    """Base class for all connectors (Shopify, Meta, Google, etc.)."""

    def __init__(self, store_id, provider):
        self.store_id = store_id
        self.provider = provider

    @abstractmethod
    def get_oauth_url(self, state: str) -> str:
        """Get OAuth authorization URL."""
        pass

    @abstractmethod
    def exchange_auth_code(self, code: str, redirect_uri: str) -> OAuthToken:
        """Exchange authorization code for access token."""
        pass

    @abstractmethod
    def validate_webhook_signature(self, body: str, signature: str) -> bool:
        """Validate webhook signature to ensure it came from the provider."""
        pass

    @abstractmethod
    def process_webhook(self, event_type: str, data: dict) -> dict:
        """Process webhook event and return standardized data."""
        pass

    @abstractmethod
    def fetch_historical_data(self, start_date: datetime, end_date: datetime) -> dict:
        """Fetch historical data from the connector (e.g., orders, ad spend)."""
        pass
