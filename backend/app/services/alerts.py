"""Proactive CAC/ROAS alerts — opt-in per store (see StoreAlertPreference).

There's no in-process scheduler here on purpose: run_all_alert_checks() is
meant to be invoked by a real cron (or Windows Task Scheduler) via
scripts/run_alert_checks.py, the same way app/services/capi.py's send_*
functions are invoked by BackgroundTasks rather than owning their own
timing. Each function that opens its own session follows that same
capi.py pattern (a background/offline caller has no request-scoped
Depends(get_db) session to reuse).
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import AlertLog, Store, StoreAlertPreference
from app.routes.metrics import CAC_BY_CHANNEL_SQL, DAILY_SQL
from app.schemas.alerts import AlertCheckResult
from app.services.notifications import send_to_store

logger = logging.getLogger("ross.alerts")


def _already_sent(
    db: Session, store_id: UUID, alert_type: str, dedupe_key: str, cooldown: Optional[timedelta], now: datetime
) -> bool:
    query = db.query(AlertLog).filter_by(store_id=store_id, alert_type=alert_type, dedupe_key=dedupe_key)
    if cooldown is None:
        return query.first() is not None
    last = query.order_by(AlertLog.sent_at.desc()).first()
    if last is None:
        return False
    last_sent_at = last.sent_at if last.sent_at.tzinfo else last.sent_at.replace(tzinfo=timezone.utc)
    return last_sent_at > now - cooldown


def _log_alert(db: Session, store_id: UUID, alert_type: str, dedupe_key: str, now: datetime) -> None:
    db.add(AlertLog(id=uuid4(), store_id=store_id, alert_type=alert_type, dedupe_key=dedupe_key, sent_at=now))
    db.commit()


def check_cac_alerts(db: Session, store: Store, prefs: StoreAlertPreference, now: datetime) -> list[str]:
    """One alert per channel per cohort month — CAC is a monthly-cohort
    metric (see CAC_BY_CHANNEL_SQL), so re-checking mid-month before the
    month's picture is final would be noise, not signal."""
    if prefs.cac_threshold is None:
        return []

    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    rows = (
        db.execute(CAC_BY_CHANNEL_SQL, {"store_id": str(store.id), "start": month_start, "end": now})
        .mappings()
        .all()
    )

    fired = []
    threshold = float(prefs.cac_threshold)
    for row in rows:
        if row["cac"] is None or float(row["cac"]) <= threshold:
            continue
        dedupe_key = f"{row['channel']}:{row['cohort_month'].isoformat()}"
        if _already_sent(db, store.id, "cac", dedupe_key, cooldown=None, now=now):
            continue

        message = f"{row['channel']}: CAC ${float(row['cac']):.2f} > ${threshold:.2f}"
        send_to_store(
            db,
            store,
            subject=f"[ROSS] CAC alto en {store.name} — canal {row['channel']}",
            body=(
                f"El CAC del canal '{row['channel']}' en {store.name} este mes es "
                f"${float(row['cac']):.2f}, por encima del umbral configurado "
                f"(${threshold:.2f}). Nuevos clientes este mes: {row['new_customers']}."
            ),
            event_type="cac_alert",
        )
        _log_alert(db, store.id, "cac", dedupe_key, now=now)
        fired.append(message)

    return fired


def _roas_streak_is_bad(daily_rows: list, threshold: float, days_n: int) -> bool:
    """daily_rows must be ordered oldest -> newest (as DAILY_SQL returns
    them). A day with no ad_spend is skipped entirely rather than counted
    as good or bad — true ROAS (net_profit / ad_spend) is undefined with
    nothing to divide by. Split out from check_roas_alert as a pure
    function so it's testable without a live TimescaleDB continuous
    aggregate (daily_financial_summary only refreshes on its own policy
    schedule, not synchronously on insert — see db/init/004_*.sql)."""
    spend_days = [r for r in daily_rows if r["ad_spend"] and float(r["ad_spend"]) > 0]
    if len(spend_days) < days_n:
        return False
    recent = spend_days[-days_n:]
    return all(float(r["total_net_profit"]) / float(r["ad_spend"]) < threshold for r in recent)


def check_roas_alert(db: Session, store: Store, prefs: StoreAlertPreference, now: datetime) -> bool:
    days_n = prefs.roas_days_n
    start = now - timedelta(days=days_n + 7)  # buffer for the continuous aggregate's own lag
    rows = db.execute(DAILY_SQL, {"store_id": str(store.id), "start": start, "end": now}).mappings().all()

    if not _roas_streak_is_bad(rows, float(prefs.roas_threshold), days_n):
        return False

    if _already_sent(db, store.id, "roas", "roas", cooldown=timedelta(days=days_n), now=now):
        return False

    send_to_store(
        db,
        store,
        subject=f"[ROSS] True ROAS bajo en {store.name}",
        body=(
            f"{store.name} lleva {days_n} días con true ROAS por debajo de "
            f"{float(prefs.roas_threshold):.2f}x. Revisá el desglose de gasto por canal en el dashboard."
        ),
        event_type="roas_alert",
    )
    _log_alert(db, store.id, "roas", "roas", now=now)
    return True


def run_check_for_store(db: Session, store: Store, now: Optional[datetime] = None) -> AlertCheckResult:
    now = now or datetime.now(timezone.utc)
    prefs = db.get(StoreAlertPreference, store.id)
    if not prefs or not prefs.enabled:
        return AlertCheckResult(cac_alerts_sent=[], roas_alert_sent=False)

    cac_alerts = check_cac_alerts(db, store, prefs, now)
    roas_alert = check_roas_alert(db, store, prefs, now)
    return AlertCheckResult(cac_alerts_sent=cac_alerts, roas_alert_sent=roas_alert)


def run_all_alert_checks() -> None:
    """Entry point for scripts/run_alert_checks.py — opens its own session
    since an offline/cron caller has no request-scoped one to reuse."""
    db = SessionLocal()
    try:
        stores = (
            db.query(Store)
            .join(StoreAlertPreference, StoreAlertPreference.store_id == Store.id)
            .filter(StoreAlertPreference.enabled.is_(True))
            .all()
        )
        for store in stores:
            try:
                run_check_for_store(db, store)
            except Exception:
                logger.exception("alert_check_failed_for_store", extra={"store_id": str(store.id)})
    finally:
        db.close()
