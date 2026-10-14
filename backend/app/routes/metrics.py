from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_owned_store, require_plan_feature
from app.models import Store, User
from app.schemas.metrics import (
    ChannelAttributionOut,
    ChannelCacOut,
    CohortLtvOut,
    CreativeMetricOut,
    DailyMetricOut,
    ForecastOut,
    MetricsSummaryOut,
    PnlOut,
)
from app.services.metrics import service as metrics_service
from app.services.metrics.queries import (
    ATTRIBUTION_BY_CHANNEL_SQL,
    CAC_BY_CHANNEL_SQL,
    CREATIVES_SQL,
    DAILY_SQL,
    PNL_SQL,
    SUMMARY_SQL,
)

router = APIRouter(prefix="/stores/{store_id}/metrics", tags=["metrics"])


@router.get("/summary", response_model=MetricsSummaryOut)
def metrics_summary(
    start: datetime = Query(...),
    end: datetime = Query(...),
    store: Store = Depends(get_owned_store),
    db: Session = Depends(get_db),
):
    row = db.execute(SUMMARY_SQL, {"store_id": str(store.id), "start": start, "end": end}).mappings().one()
    return row


@router.get("/pnl", response_model=PnlOut)
def metrics_pnl(
    start: datetime = Query(...),
    end: datetime = Query(...),
    store: Store = Depends(get_owned_store),
    db: Session = Depends(get_db),
):
    row = db.execute(PNL_SQL, {"store_id": str(store.id), "start": start, "end": end}).mappings().one()
    return row


@router.get("/daily", response_model=list[DailyMetricOut])
def metrics_daily(
    start: datetime = Query(...),
    end: datetime = Query(...),
    store: Store = Depends(get_owned_store),
    db: Session = Depends(get_db),
):
    rows = db.execute(DAILY_SQL, {"store_id": str(store.id), "start": start, "end": end}).mappings().all()
    return rows


@router.get("/creatives", response_model=list[CreativeMetricOut])
def metrics_creatives(
    start: datetime = Query(...),
    end: datetime = Query(...),
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_plan_feature("creative_performance")),
    db: Session = Depends(get_db),
):
    rows = db.execute(CREATIVES_SQL, {"store_id": str(store.id), "start": start, "end": end}).mappings().all()
    return rows


@router.get("/ltv-cohorts", response_model=list[CohortLtvOut])
def metrics_ltv_cohorts(
    start: datetime = Query(...),
    end: datetime = Query(...),
    months: int = Query(6, ge=1, le=12),
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_plan_feature("ltv_cohorts")),
    db: Session = Depends(get_db),
):
    return metrics_service.ltv_cohorts(db, str(store.id), start, end, months)


@router.get("/cac-by-channel", response_model=list[ChannelCacOut])
def metrics_cac_by_channel(
    start: datetime = Query(...),
    end: datetime = Query(...),
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_plan_feature("cac_by_channel")),
    db: Session = Depends(get_db),
):
    rows = (
        db.execute(CAC_BY_CHANNEL_SQL, {"store_id": str(store.id), "start": start, "end": end}).mappings().all()
    )
    return rows


@router.get("/attribution-by-channel", response_model=list[ChannelAttributionOut])
def metrics_attribution_by_channel(
    start: datetime = Query(...),
    end: datetime = Query(...),
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_plan_feature("attribution_by_channel")),
    db: Session = Depends(get_db),
):
    rows = (
        db.execute(ATTRIBUTION_BY_CHANNEL_SQL, {"store_id": str(store.id), "start": start, "end": end})
        .mappings()
        .all()
    )
    return rows


@router.get("/forecast", response_model=ForecastOut)
def metrics_forecast(
    history_days: int = Query(60, ge=metrics_service.MIN_FORECAST_HISTORY_DAYS, le=365),
    forecast_days: int = Query(30, ge=1, le=90),
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_plan_feature("forecast")),
    db: Session = Depends(get_db),
):
    return ForecastOut(**metrics_service.forecast(db, str(store.id), history_days, forecast_days))
