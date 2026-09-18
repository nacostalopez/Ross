from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, get_owned_store, require_plan_feature, require_store_role
from app.models import CustomerDataAccessLog, Store, User
from app.models import orders as orders_table
from app.schemas.orders import CustomerDataAccessLogOut, OrderCreate, OrderOut
from app.services.capi import send_google_purchase_conversion, send_meta_purchase_event
from app.services.customers import resolve_customer_id

router = APIRouter(prefix="/stores/{store_id}/orders", tags=["orders"])


@router.post("", status_code=201)
def ingest_orders(
    payload: list[OrderCreate],
    background_tasks: BackgroundTasks,
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_store_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    if not payload:
        return {"inserted": 0}

    rows = []
    for item in payload:
        data = item.model_dump()
        email = data.pop("customer_email")
        phone = data.pop("customer_phone")
        data["customer_id"] = resolve_customer_id(db, store.id, email, phone, order_time=data["time"])
        rows.append({"store_id": store.id, **data})

    stmt = pg_insert(orders_table).values(rows)
    update_cols = {
        c.name: stmt.excluded[c.name]
        for c in orders_table.c
        if c.name not in ("time", "store_id", "order_id", "net_profit")
    }
    stmt = stmt.on_conflict_do_update(
        index_elements=["store_id", "order_id", "time"],
        set_=update_cols,
    )
    db.execute(stmt)
    db.commit()
    for row in rows:
        background_tasks.add_task(send_meta_purchase_event, store.id, row["order_id"], row)
        background_tasks.add_task(send_google_purchase_conversion, store.id, row["order_id"], row)
    return {"inserted": len(rows)}


@router.get("", response_model=list[OrderOut])
def list_orders(
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    store: Store = Depends(get_owned_store),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(orders_table).where(orders_table.c.store_id == store.id)
    if start:
        stmt = stmt.where(orders_table.c.time >= start)
    if end:
        stmt = stmt.where(orders_table.c.time <= end)
    stmt = stmt.order_by(orders_table.c.time.desc()).limit(500)
    rows = db.execute(stmt).mappings().all()

    # The only customer-linked value any route returns is customer_id
    # (Customer itself is hash-only, never returned directly) — log who
    # fetched it and when. See app/models/audit.py::CustomerDataAccessLog.
    db.add(
        CustomerDataAccessLog(
            id=uuid4(), store_id=store.id, user_id=current_user.id, endpoint="GET /orders"
        )
    )
    db.commit()

    return rows


@router.get("/audit-log", response_model=list[CustomerDataAccessLogOut])
def list_audit_log(
    limit: int = Query(default=50, ge=1, le=500),
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_store_role("owner", "admin")),
    __: User = Depends(require_plan_feature("audit_log")),
    db: Session = Depends(get_db),
):
    """Who fetched customer-linked order data, most recent first — a
    viewer (even a store-level one) can't see who looked at what."""
    rows = (
        db.query(CustomerDataAccessLog)
        .filter(CustomerDataAccessLog.store_id == store.id)
        .order_by(CustomerDataAccessLog.accessed_at.desc())
        .limit(limit)
        .all()
    )
    users_by_id = {
        u.id: u
        for u in db.query(User).filter(User.id.in_({r.user_id for r in rows})).all()
    }
    return [
        CustomerDataAccessLogOut(
            user_email=users_by_id[r.user_id].email if r.user_id in users_by_id else "(usuario eliminado)",
            endpoint=r.endpoint,
            accessed_at=r.accessed_at,
        )
        for r in rows
    ]
