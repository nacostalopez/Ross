"""Weekly email summary — opt-in per store (see StoreReportPreference).

Same no-in-process-scheduler reasoning as app/services/alerts.py:
run_all_weekly_reports() is meant to be invoked by a real cron via
scripts/send_weekly_reports.py, not scheduled from inside the FastAPI app.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Store, StoreReportPreference
from app.routes.metrics import CAC_BY_CHANNEL_SQL, SUMMARY_SQL
from app.services.notifications import send_to_store

logger = logging.getLogger("ross.reports")


def build_weekly_summary(db: Session, store: Store, now: datetime) -> str:
    week_start = now - timedelta(days=7)
    summary = db.execute(SUMMARY_SQL, {"store_id": str(store.id), "start": week_start, "end": now}).mappings().one()

    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    cac_rows = (
        db.execute(CAC_BY_CHANNEL_SQL, {"store_id": str(store.id), "start": month_start, "end": now})
        .mappings()
        .all()
    )

    lines = [
        f"Resumen semanal de {store.name} — últimos 7 días",
        "",
        f"Revenue: ${float(summary['revenue']):.2f}",
        f"Profit neto: ${float(summary['net_profit']):.2f}",
        f"Gasto en ads: ${float(summary['total_ad_spend']):.2f}",
        f"Profit real (post-ads): ${float(summary['real_profit_after_ads']):.2f}",
        f"True ROAS: {summary['true_roas']}x" if summary["true_roas"] is not None else "True ROAS: —",
    ]

    cac_lines = [
        f"  {row['channel']}: ${float(row['cac']):.2f}" for row in cac_rows if row["cac"] is not None
    ]
    if cac_lines:
        lines += ["", "CAC por canal este mes:", *cac_lines]

    return "\n".join(lines)


def send_weekly_report(db: Session, store: Store, now: datetime) -> str:
    body = build_weekly_summary(db, store, now)
    send_to_store(
        db, store, subject=f"[ROSS] Resumen semanal — {store.name}", body=body, event_type="weekly_report"
    )
    return body


def run_report_for_store(db: Session, store: Store, now: Optional[datetime] = None) -> bool:
    """Returns whether a report was sent (False if reporting isn't enabled
    for this store)."""
    now = now or datetime.now(timezone.utc)
    prefs = db.get(StoreReportPreference, store.id)
    if not prefs or not prefs.enabled:
        return False
    send_weekly_report(db, store, now)
    return True


def run_all_weekly_reports() -> None:
    """Entry point for scripts/send_weekly_reports.py — opens its own
    session, same pattern as app/services/alerts.py::run_all_alert_checks."""
    db = SessionLocal()
    try:
        stores = (
            db.query(Store)
            .join(StoreReportPreference, StoreReportPreference.store_id == Store.id)
            .filter(StoreReportPreference.enabled.is_(True))
            .all()
        )
        for store in stores:
            try:
                run_report_for_store(db, store)
            except Exception:
                logger.exception("weekly_report_failed_for_store", extra={"store_id": str(store.id)})
    finally:
        db.close()
