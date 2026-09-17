import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class Account(Base):
    __tablename__ = "accounts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    stores = relationship("Store", back_populates="account", cascade="all, delete-orphan")
    users = relationship("User", back_populates="account", cascade="all, delete-orphan")
    invites = relationship("AccountInvite", back_populates="account", cascade="all, delete-orphan")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('owner', 'admin', 'viewer')", name="ck_users_role"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id = Column(UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"))
    email = Column(String(255), unique=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    account = relationship("Account", back_populates="users")


class AccountInvite(Base):
    __tablename__ = "account_invites"
    __table_args__ = (
        CheckConstraint("role IN ('owner', 'admin', 'viewer')", name="ck_account_invites_role"),
        CheckConstraint("status IN ('pending', 'accepted', 'revoked')", name="ck_account_invites_status"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id = Column(UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    email = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False)
    invited_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    token_hash = Column(String(64), unique=True, nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
    accepted_at = Column(DateTime(timezone=True))

    account = relationship("Account", back_populates="invites")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(String(64), unique=True, nullable=False)
    issued_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True))  # NULL = active


class DashboardLayout(Base):
    __tablename__ = "dashboard_layouts"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    widgets = Column(JSONB, nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class StoreAlertPreference(Base):
    """Opt-in CAC/ROAS alert config, one row per store. See
    app/services/alerts.py for how these thresholds are checked and
    scripts/run_alert_checks.py for how the check actually gets run
    (there's no in-process scheduler — see that script's docstring)."""

    __tablename__ = "store_alert_preferences"

    store_id = Column(UUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True)
    enabled = Column(Boolean, nullable=False, default=False)
    # NULL = no CAC alert configured — no dollar default makes sense across businesses.
    cac_threshold = Column(Numeric(12, 4))
    roas_threshold = Column(Numeric(6, 2), nullable=False, default=1.0)
    roas_days_n = Column(SmallInteger, nullable=False, default=3)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class StoreReportPreference(Base):
    """Opt-in weekly email summary, one row per store. See
    app/services/reports.py for what the email contains and
    scripts/send_weekly_reports.py for how it's actually scheduled (no
    in-process scheduler — same reasoning as StoreAlertPreference above)."""

    __tablename__ = "store_report_preferences"

    store_id = Column(UUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True)
    enabled = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(String(64), unique=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True))  # NULL = unused


class Store(Base):
    __tablename__ = "stores"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id = Column(UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"))
    name = Column(String(255), nullable=False)
    platform = Column(String(50), nullable=False)
    currency = Column(String(3), default="USD")
    timezone = Column(String(50), default="UTC")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    account = relationship("Account", back_populates="stores")
    credentials = relationship("StoreCredential", back_populates="store", cascade="all, delete-orphan")
    products = relationship("Product", back_populates="store", cascade="all, delete-orphan")


class StoreCredential(Base):
    __tablename__ = "store_credentials"
    __table_args__ = (UniqueConstraint("store_id", "provider", name="uq_store_provider"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    store_id = Column(UUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"))
    provider = Column(String(50), nullable=False)
    access_token = Column(String, nullable=False)
    refresh_token = Column(String)
    expires_at = Column(DateTime(timezone=True))
    # Provider-side account/store identifier captured at OAuth time (e.g.
    # Tiendanube's own store id, MercadoPago's collector id), so later API
    # calls don't require the caller to keep re-supplying it. Nullable —
    # Shopify/Meta/Google don't use it.
    provider_account_id = Column(String(255))
    # CAPI feedback loop (Meta Conversions API / Google Enhanced Conversions)
    # — opt-in per store+provider. capi_destination_id is the Meta pixel id
    # or Google conversionAction resource name, depending on `provider`.
    # See app/services/capi.py.
    capi_enabled = Column(Boolean, nullable=False, default=False)
    capi_destination_id = Column(String(255))

    store = relationship("Store", back_populates="credentials")


class StoreMembership(Base):
    """Per-store role override on top of User.role (account-wide) — a row
    here overrides the effective role for that one store; no row means the
    account role applies. See app/dependencies.py::require_store_role."""

    __tablename__ = "store_memberships"
    __table_args__ = (CheckConstraint("role IN ('owner', 'admin', 'viewer')", name="ck_store_memberships_role"),)

    store_id = Column(UUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role = Column(String(20), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("store_id", "external_id", name="uq_store_external_id"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    store_id = Column(UUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"))
    external_id = Column(String(255), nullable=False)
    sku = Column(String(100))
    title = Column(String(255), nullable=False)
    cogs = Column(Numeric(12, 4), default=0.0)
    shipping_cost = Column(Numeric(12, 4), default=0.0)

    store = relationship("Store", back_populates="products")


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("store_id", "email_hash", name="uq_customers_store_email"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    store_id = Column(UUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False)
    # SHA-256 hex, normalized the same way Meta/Google Conversions APIs
    # expect (see app/services/customers.py) — no plaintext email/phone is
    # ever stored, matching the pixel_events.user_email_hash convention.
    email_hash = Column(String(64))
    phone_hash = Column(String(64))
    external_customer_id = Column(String(255))
    first_order_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PushSubscription(Base):
    """A Web Push subscription for one (user, browser) pair — a user can
    have several (phone + laptop). This only tracks *who can receive* a
    push; *what* triggers one is still the existing per-store alert/report
    opt-in (StoreAlertPreference/StoreReportPreference) — see
    app/services/push.py and app/services/notifications.py::send_to_store,
    which fans out to both email and push for the same event."""

    __tablename__ = "push_subscriptions"
    __table_args__ = (UniqueConstraint("user_id", "endpoint", name="uq_push_subscriptions_user_endpoint"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    endpoint = Column(Text, nullable=False)
    p256dh_key = Column(Text, nullable=False)
    auth_key = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CapiEvent(Base):
    """One row per (store, order, provider) CAPI send attempt — both the
    idempotency check (never re-send a purchase already marked "sent") and
    the audit trail (why a send failed). See app/services/capi.py."""

    __tablename__ = "capi_events"
    __table_args__ = (UniqueConstraint("store_id", "order_id", "provider", name="uq_capi_events_store_order_provider"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    store_id = Column(UUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False)
    order_id = Column(String(255), nullable=False)
    provider = Column(String(50), nullable=False)
    status = Column(String(20), nullable=False)  # "sent" | "failed"
    error = Column(Text)
    sent_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
