from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_owned_store, require_plan_feature, require_store_role
from app.models import Store, StoreReportPreference, User
from app.schemas.reports import ReportPreferencesIn, ReportPreferencesOut, SendReportNowOut
from app.services.activity_log import log_activity
from app.services.reports import send_weekly_report

router = APIRouter(prefix="/stores/{store_id}/report-preferences", tags=["reports"])


@router.get("", response_model=ReportPreferencesOut)
def get_report_preferences(store: Store = Depends(get_owned_store), db: Session = Depends(get_db)):
    row = db.get(StoreReportPreference, store.id)
    return ReportPreferencesOut(enabled=row.enabled if row else False)


@router.put("", response_model=ReportPreferencesOut)
def set_report_preferences(
    payload: ReportPreferencesIn,
    store: Store = Depends(get_owned_store),
    current_user: User = Depends(require_store_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    # Only gated when *enabling* — a plan downgrade must never strand
    # someone unable to turn OFF a feature they already had on.
    if payload.enabled:
        require_plan_feature("weekly_report")(current_user, db)

    row = db.get(StoreReportPreference, store.id)
    if row:
        row.enabled = payload.enabled
    else:
        row = StoreReportPreference(store_id=store.id, enabled=payload.enabled)
        db.add(row)
    db.commit()
    if payload.enabled:
        log_activity(
            db,
            current_user.account_id,
            current_user.id,
            "report_preferences_updated",
            f"Activaste el reporte semanal en {store.name}",
        )
    return payload


@router.post("/send-now", response_model=SendReportNowOut)
def send_report_now(
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_store_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Sends (and returns) the weekly summary right now, regardless of the
    saved enabled toggle — see SendReportNowOut for why "preview/test send"
    deliberately isn't gated the same way alerts' check-now is."""
    body = send_weekly_report(db, store, datetime.now(timezone.utc))
    return SendReportNowOut(body=body)
