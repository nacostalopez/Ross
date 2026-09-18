from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_owned_store, require_store_role
from app.models import Store, StoreAlertPreference, User
from app.schemas.alerts import AlertCheckResult, AlertPreferencesIn, AlertPreferencesOut
from app.services.activity_log import log_activity
from app.services.alerts import run_check_for_store

router = APIRouter(prefix="/stores/{store_id}/alert-preferences", tags=["alerts"])


@router.get("", response_model=AlertPreferencesOut)
def get_alert_preferences(store: Store = Depends(get_owned_store), db: Session = Depends(get_db)):
    """No saved row yet means alerts are off with the schema's own
    defaults (roas_threshold=1.0, roas_days_n=3, no CAC threshold)."""
    row = db.get(StoreAlertPreference, store.id)
    if not row:
        return AlertPreferencesOut()
    return AlertPreferencesOut(
        enabled=row.enabled,
        cac_threshold=float(row.cac_threshold) if row.cac_threshold is not None else None,
        roas_threshold=float(row.roas_threshold),
        roas_days_n=row.roas_days_n,
    )


@router.put("", response_model=AlertPreferencesOut)
def set_alert_preferences(
    payload: AlertPreferencesIn,
    store: Store = Depends(get_owned_store),
    current_user: User = Depends(require_store_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    row = db.get(StoreAlertPreference, store.id)
    if row:
        row.enabled = payload.enabled
        row.cac_threshold = payload.cac_threshold
        row.roas_threshold = payload.roas_threshold
        row.roas_days_n = payload.roas_days_n
    else:
        row = StoreAlertPreference(store_id=store.id, **payload.model_dump())
        db.add(row)
    db.commit()
    if payload.enabled:
        log_activity(
            db, current_user.account_id, current_user.id, "alert_preferences_updated", f"Activaste alertas en {store.name}"
        )
    return payload


@router.post("/check-now", response_model=AlertCheckResult)
def check_alerts_now(
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_store_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Runs the same check scripts/run_alert_checks.py runs on a schedule,
    synchronously against the request's own session — lets the frontend's
    "Probar ahora" button (and a merchant tuning thresholds) see the result
    immediately instead of waiting for the next cron tick."""
    return run_check_for_store(db, store)
