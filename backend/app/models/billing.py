"""Billing scaffolding — see the "Estrategia de Billing" proposal doc for
the full design and reasoning. Deliberately built so it can go live with
zero visible behavior change: every account (existing or newly
registered) gets a Subscription pinned to the "scale" plan, which has
every feature — so require_plan_feature() is real and wired onto routes
today, but doesn't restrict anyone until someone deliberately assigns a
lower plan (which nothing in this codebase does yet — that's Phase 2,
the actual paid checkout flow). Prices/limits on Plan are placeholders
(NULL/None) until the business decides real numbers.
"""

import uuid

from sqlalchemy import Column, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from sqlalchemy.types import DateTime

from app.database import Base


class Plan(Base):
    """A billing tier. `id` is a short stable slug ("starter"/"growth"/
    "scale"), not a UUID, since it's referenced by application code
    (require_plan_feature) as well as by Subscription rows."""

    __tablename__ = "plans"

    id = Column(String(20), primary_key=True)
    name = Column(String(50), nullable=False)
    # NULL = not priced yet — the whole point of this scaffolding is that
    # these can stay empty until the business decides real numbers.
    monthly_price = Column(Numeric(10, 2))
    max_stores = Column(Integer)  # NULL = unlimited
    max_orders_per_month = Column(Integer)  # NULL = unlimited
    max_users = Column(Integer)  # NULL = unlimited
    # Feature keys this plan unlocks, checked by require_plan_feature() —
    # e.g. ["forecast", "ltv_cohorts", "cac_by_channel"]. Keys not listed
    # anywhere aren't gated at all (most of the product isn't behind a
    # plan check — see the proposal doc's feature matrix for what's
    # actually gated today vs. always-on).
    features = Column(JSONB, nullable=False, default=list)


class Subscription(Base):
    """One active subscription per account. No row for an account = no
    subscription at all, which app/dependencies.py::_plan_for_account
    treats as "scale" (see this module's docstring for why that's the
    safe default today)."""

    __tablename__ = "subscriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id = Column(UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), unique=True, nullable=False)
    plan_id = Column(String(20), ForeignKey("plans.id"), nullable=False)
    status = Column(String(20), nullable=False, default="active")  # trialing | active | past_due | canceled
    trial_ends_at = Column(DateTime(timezone=True))
    current_period_end = Column(DateTime(timezone=True))
    # Populated once a real payment provider is wired up (Phase 2) — all
    # nullable so a "scale, no payment provider" grandfathered row is valid.
    payment_provider = Column(String(20))
    provider_subscription_id = Column(String(255))
    provider_customer_id = Column(String(255))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    plan = relationship("Plan")


class Invoice(Base):
    """Populated by the payment provider's webhooks once Phase 2 exists —
    no code writes to this table yet, it's here so the schema doesn't need
    a second migration when that lands."""

    __tablename__ = "invoices"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id = Column(UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    provider_invoice_id = Column(String(255))
    amount = Column(Numeric(12, 2), nullable=False)
    currency = Column(String(3), nullable=False, default="USD")
    status = Column(String(20), nullable=False)  # paid | open | void
    issued_at = Column(DateTime(timezone=True), server_default=func.now())
    paid_at = Column(DateTime(timezone=True))
