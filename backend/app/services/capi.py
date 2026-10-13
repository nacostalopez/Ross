"""CAPI feedback loop — sends confirmed purchases back to Meta Conversions
API and Google Enhanced Conversions, using the hashed email/phone already
stored on `customers` (see app/services/customers.py). Opt-in per
store+provider via StoreCredential.capi_enabled/capi_destination_id.

Called from BackgroundTasks after order ingestion (app/routes/orders.py,
app/routes/connectors.py webhooks) — each send_* function here opens its
own SessionLocal() rather than reusing the request's session, since FastAPI
closes the Depends(get_db) session before a background task runs.
"""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.connectors import redact
from app.connectors.google import GoogleAdsConnector
from app.connectors.meta import MetaConnector
from app.database import SessionLocal
from app.models import CapiEvent, Customer, StoreCredential
from app.services.connector_status import _upsert_connector_status
from app.services.connector_tokens import fresh_access_token


def _order_time(order_row: dict) -> datetime:
    """order_row["time"] is a real datetime after the bulk-ingest route's
    model_dump(), but a plain ISO string from the Shopify/Tiendanube
    webhook path (see connectors/shopify.py, connectors/tiendanube.py) —
    normalize either shape."""
    value = order_row["time"]
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)


def _load_credential_for_send(db: Session, store_id: UUID, order_id: str, provider: str) -> Optional[StoreCredential]:
    credential = db.query(StoreCredential).filter_by(store_id=store_id, provider=provider).first()
    if not credential or not credential.capi_enabled or not credential.capi_destination_id:
        return None
    already_sent = (
        db.query(CapiEvent).filter_by(store_id=store_id, order_id=order_id, provider=provider, status="sent").first()
    )
    if already_sent:
        return None
    return credential


def _load_customer(db: Session, order_row: dict) -> Optional[Customer]:
    customer_id = order_row.get("customer_id")
    if not customer_id:
        return None
    customer = db.get(Customer, customer_id)
    if not customer or (not customer.email_hash and not customer.phone_hash):
        return None
    return customer


def _record_result(
    db: Session, store_id: UUID, order_id: str, provider: str, status: str, error: Optional[str]
) -> None:
    stmt = pg_insert(CapiEvent.__table__).values(
        id=uuid4(),
        store_id=store_id,
        order_id=order_id,
        provider=provider,
        status=status,
        error=error,
        sent_at=datetime.now(timezone.utc) if status == "sent" else None,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["store_id", "order_id", "provider"],
        set_={"status": stmt.excluded.status, "error": stmt.excluded.error, "sent_at": stmt.excluded.sent_at},
    )
    db.execute(stmt)
    db.commit()
    _upsert_connector_status(db, store_id, f"{provider}_capi", synced=True, success=(status == "sent"), error=error)


def send_meta_purchase_event(store_id: UUID, order_id: str, order_row: dict) -> None:
    db = SessionLocal()
    try:
        credential = _load_credential_for_send(db, store_id, order_id, "meta")
        if not credential:
            return
        customer = _load_customer(db, order_row)
        if not customer:
            return
        try:
            connector = MetaConnector(str(store_id))
            access_token = fresh_access_token(db, credential, connector)
            connector.send_purchase_event(
                access_token,
                credential.capi_destination_id,
                order_id,
                _order_time(order_row),
                float(order_row["gross_amount"]),
                order_row["currency"],
                customer.email_hash,
                customer.phone_hash,
            )
            _record_result(db, store_id, order_id, "meta", "sent", None)
        except Exception as e:
            _record_result(db, store_id, order_id, "meta", "failed", redact(e))
    finally:
        db.close()


def send_google_purchase_conversion(store_id: UUID, order_id: str, order_row: dict) -> None:
    db = SessionLocal()
    try:
        credential = _load_credential_for_send(db, store_id, order_id, "google")
        if not credential:
            return
        customer = _load_customer(db, order_row)
        if not customer:
            return
        try:
            # capi_destination_id for Google is the full conversionAction
            # resource name ("customers/{id}/conversionActions/{id}") — the
            # Ads account id the uploadClickConversions call needs is just
            # its first path segment, so no separate config field is needed.
            google_customer_id = credential.capi_destination_id.split("/")[1]
            connector = GoogleAdsConnector(str(store_id), customer_id=google_customer_id)
            access_token = fresh_access_token(db, credential, connector)
            connector.send_purchase_conversion(
                access_token,
                credential.capi_destination_id,
                order_id,
                _order_time(order_row),
                float(order_row["gross_amount"]),
                order_row["currency"],
                customer.email_hash,
                customer.phone_hash,
            )
            _record_result(db, store_id, order_id, "google", "sent", None)
        except Exception as e:
            _record_result(db, store_id, order_id, "google", "failed", redact(e))
    finally:
        db.close()
