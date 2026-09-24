"""Customer identity resolution — the one place email/phone hashing lives.

Every order-ingestion path (bulk API, Shopify/Tiendanube webhooks, Mercado
Libre/Mercado Pago syncs and notifications) calls resolve_customer_id() instead of hashing/upserting customers itself.
"""

import hashlib
import re
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import Customer


def _hash_email(email: str) -> str:
    """Meta/Google Conversions API recipe: trim + lowercase, then SHA-256.
    Using their exact normalization means this hash is already CAPI-ready
    if that feature gets built later — no separate re-hash needed."""
    normalized = email.strip().lower()
    return hashlib.sha256(normalized.encode()).hexdigest()


def _hash_phone(phone: str) -> str:
    """Meta/Google recipe: digits only, country code included, no leading
    zeros. Best-effort here — we don't have a phone-parsing library to
    reliably infer a missing country code from a bare local number, so this
    just strips non-digits and leading zeros from whatever the platform
    sends (Shopify/Tiendanube both send phone with a leading + and country
    code when they have it)."""
    digits = re.sub(r"\D", "", phone).lstrip("0")
    return hashlib.sha256(digits.encode()).hexdigest()


def resolve_customer_id(
    db: Session,
    store_id: UUID,
    email: str | None,
    phone: str | None,
    external_customer_id: str | None = None,
    order_time: datetime | None = None,
) -> UUID | None:
    """Find-or-create the Customer for this store matching the given
    email/phone (or, failing both, a namespaced marketplace buyer id), and
    return its id. Returns None if none is given — the order simply has no
    linked customer, same as before this existed.

    Email is the primary dedup key; phone is only used to match when no
    email is present (a customer could plausibly share a phone with someone
    else in edge cases, but not an email).

    first_order_at tracks the order's own timestamp (order_time), not
    ingestion time — connectors and bulk imports routinely backfill past
    orders, and LTV-by-cohort needs the real acquisition date. If an
    earlier order for an already-known customer arrives later (backfill
    landing out of order), first_order_at moves back to match.
    """
    email_hash = _hash_email(email) if email else None
    phone_hash = _hash_phone(phone) if phone else None

    # Marketplaces (Mercado Libre) mask buyer contact data, so their stable
    # buyer id is the only identity there is. Only used as a key when
    # there's no email/phone at all, and only for ids the connector
    # namespaced ("ml:123", "mp:456") — a bare Shopify/Tiendanube customer
    # id stays reference-only, as before.
    external_key = external_customer_id if external_customer_id and ":" in external_customer_id else None

    if not email_hash and not phone_hash and not external_key:
        return None

    order_time = order_time or datetime.now(timezone.utc)

    existing = None
    if email_hash:
        existing = db.query(Customer).filter_by(store_id=store_id, email_hash=email_hash).first()
    elif phone_hash:
        existing = db.query(Customer).filter_by(store_id=store_id, phone_hash=phone_hash).first()
    else:
        existing = db.query(Customer).filter_by(store_id=store_id, external_customer_id=external_key).first()

    if existing:
        # Enrich with anything new we learned about this customer, but
        # never overwrite an already-known value.
        if phone_hash and not existing.phone_hash:
            existing.phone_hash = phone_hash
        if external_customer_id and not existing.external_customer_id:
            existing.external_customer_id = external_customer_id
        if not existing.first_order_at or order_time < existing.first_order_at:
            existing.first_order_at = order_time
        return existing.id

    customer = Customer(
        store_id=store_id,
        email_hash=email_hash,
        phone_hash=phone_hash,
        external_customer_id=external_customer_id,
        first_order_at=order_time,
    )
    db.add(customer)
    db.flush()  # populate customer.id without requiring a separate commit
    return customer.id
