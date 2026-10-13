"""The access token of a stored credential, renewed first if it is about to expire.

Lives here, not in app/routes/connectors.py, so the routes and the conversion senders in
app/services/capi.py share one implementation: the rotation, the row lock and the audit are
the same everywhere, and a token that cannot be renewed says "reconnect" instead of failing
with whatever the provider answered.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import requests
from sqlalchemy.orm import Session

from app.connectors import RECONNECT_MESSAGE, ReconnectRequired, redact
from app.models import StoreCredential, TokenRefreshAudit
from app.security import decrypt_secret, encrypt_secret
from app.services.connector_status import _upsert_connector_status

TOKEN_REFRESH_MARGIN = timedelta(minutes=5)


def needs_reconnect(expires_at, has_refresh_token: bool, now=None) -> bool:
    """True when the access token has expired and there is no refresh token to renew it with."""
    now = now or datetime.now(timezone.utc)
    return bool(expires_at and expires_at <= now and not has_refresh_token)


def _rejected_by_provider(error: Exception) -> bool:
    """The provider answered 400/401 to the refresh: the refresh token is spent, revoked or wrong."""
    response = getattr(error, "response", None)
    return isinstance(error, requests.HTTPError) and getattr(response, "status_code", None) in (400, 401)


def fresh_access_token(db: Session, credential: StoreCredential, connector) -> str:
    """The credential's decrypted access token, refreshed first if it's about to expire.

    The refresh runs with the row locked (SELECT ... FOR UPDATE): Mercado Libre refresh tokens are
    single-use, so two concurrent refreshes (a manual sync racing a notification) would leave the
    loser presenting a spent token and the store disconnected for good.

    Raises ReconnectRequired when the token has expired and cannot be renewed (no refresh token, or
    the provider refused the one it has); the connector's status then says so.
    """
    now = datetime.now(timezone.utc)
    if not credential.expires_at or credential.expires_at - TOKEN_REFRESH_MARGIN > now:
        return decrypt_secret(credential.access_token)

    locked = db.query(StoreCredential).filter_by(id=credential.id).with_for_update().populate_existing().one()
    # Someone else may have refreshed while this waited on the lock.
    if locked.expires_at and locked.expires_at - TOKEN_REFRESH_MARGIN > now:
        db.commit()
        return decrypt_secret(locked.access_token)
    if not locked.refresh_token:
        db.commit()
        if needs_reconnect(locked.expires_at, False, now):
            _upsert_connector_status(
                db, locked.store_id, connector.provider, synced=True, success=False, error=RECONNECT_MESSAGE
            )
            raise ReconnectRequired()
        return decrypt_secret(locked.access_token)

    try:
        token = connector.refresh_access_token(decrypt_secret(locked.refresh_token))
    except Exception as refresh_error:
        db.rollback()
        db.add(
            TokenRefreshAudit(
                id=uuid4(),
                store_id=locked.store_id,
                provider=connector.provider,
                success=False,
                error_message=redact(refresh_error),
            )
        )
        db.commit()
        if _rejected_by_provider(refresh_error):
            _upsert_connector_status(
                db, locked.store_id, connector.provider, synced=True, success=False, error=RECONNECT_MESSAGE
            )
            raise ReconnectRequired() from refresh_error
        raise

    locked.access_token = encrypt_secret(token.access_token)
    if token.refresh_token:
        locked.refresh_token = encrypt_secret(token.refresh_token)
    locked.expires_at = token.expires_at
    db.add(TokenRefreshAudit(id=uuid4(), store_id=locked.store_id, provider=connector.provider, success=True))
    db.commit()
    return token.access_token
