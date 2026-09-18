import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class ShopifyWebhookLog(Base):
    __tablename__ = "shopify_webhooks_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    store_id = Column(UUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"))
    topic = Column(String(100), nullable=False)
    signature_valid = Column(Boolean, nullable=False)
    status = Column(String(20), nullable=False)  # 'processed', 'rejected', 'error'
    error_message = Column(Text)
    received_at = Column(DateTime(timezone=True), server_default=func.now())


class TiendanubeWebhookLog(Base):
    __tablename__ = "tiendanube_webhooks_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    store_id = Column(UUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"))
    topic = Column(String(100), nullable=False)
    signature_valid = Column(Boolean, nullable=False)
    status = Column(String(20), nullable=False)  # 'processed', 'rejected', 'error'
    error_message = Column(Text)
    received_at = Column(DateTime(timezone=True), server_default=func.now())


class TokenRefreshAudit(Base):
    __tablename__ = "token_refresh_audit"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    store_id = Column(UUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"))
    provider = Column(String(50), nullable=False)
    success = Column(Boolean, nullable=False)
    error_message = Column(Text)
    refreshed_at = Column(DateTime(timezone=True), server_default=func.now())


class ConnectorStatus(Base):
    __tablename__ = "connector_status"
    __table_args__ = (UniqueConstraint("store_id", "provider", name="uq_connector_status_store_provider"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    store_id = Column(UUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"))
    provider = Column(String(50), nullable=False)
    last_synced_at = Column(DateTime(timezone=True))
    last_success_at = Column(DateTime(timezone=True))
    last_error = Column(Text)


class AlertLog(Base):
    """Dedupe record for proactive CAC/ROAS alerts — see
    app/services/alerts.py. dedupe_key's shape depends on alert_type: for
    'cac' it's "{channel}:{cohort_month}" (once per channel per month); for
    'roas' it's the constant "roas" and dedupe is a cooldown on sent_at
    instead (see check_roas_alert)."""

    __tablename__ = "alert_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    store_id = Column(UUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False)
    alert_type = Column(String(20), nullable=False)
    dedupe_key = Column(String(100), nullable=False)
    sent_at = Column(DateTime(timezone=True), server_default=func.now())


class CustomerDataAccessLog(Base):
    """Who fetched customer-linked data, and when. The only customer-linked
    value exposed by any route today is the opaque customer_id UUID via
    GET /stores/{id}/orders — see app/routes/orders.py::list_orders, the
    sole place this gets written."""

    __tablename__ = "customer_data_access_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    store_id = Column(UUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    endpoint = Column(String(100), nullable=False)
    accessed_at = Column(DateTime(timezone=True), server_default=func.now())


class AccountActivityLog(Base):
    """A narrow, personal "what did I just do" feed for the Perfil page's
    "Actividad reciente" — same deliberately-narrow philosophy as
    CustomerDataAccessLog above: only a handful of high-signal actions get
    logged (see app/services/activity_log.py for the full list), not every
    mutating route. Always scoped to the acting user, not the whole
    account, matching how it's surfaced (a personal feed, not a shared
    admin audit trail)."""

    __tablename__ = "account_activity_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id = Column(UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    action = Column(String(50), nullable=False)
    detail = Column(String(255), nullable=False)
    # clock_timestamp(), not now() — now() is fixed for the whole
    # transaction, so several activities logged in one request/session
    # (or, as this table's own tests found, several requests sharing one
    # wrapping transaction) would otherwise tie on created_at and sort
    # arbitrarily instead of newest-first.
    created_at = Column(DateTime(timezone=True), server_default=func.clock_timestamp())


class OAuthState(Base):
    """CSRF state tokens for the OAuth handshake — issued by */auth-url,
    burned by the matching */callback. See db/init/013_oauth_states.sql."""

    __tablename__ = "oauth_states"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    store_id = Column(UUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False)
    provider = Column(String(50), nullable=False)
    token_hash = Column(String(64), unique=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
    consumed_at = Column(DateTime(timezone=True))  # NULL = still valid/unused
