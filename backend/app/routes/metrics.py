from datetime import datetime, timedelta, timezone

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
    ForecastDayOut,
    ForecastOut,
    MetricsSummaryOut,
    PnlOut,
)
from app.services.forecasting import linear_forecast
from app.services.metrics.queries import (
    ATTRIBUTION_BY_CHANNEL_SQL,
    CAC_BY_CHANNEL_SQL,
    CREATIVES_SQL,
    DAILY_SQL,
    FORECAST_HISTORY_SQL,
    LTV_COHORTS_SQL,
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
    rows = (
        db.execute(LTV_COHORTS_SQL, {"store_id": str(store.id), "start": start, "end": end, "months": months})
        .mappings()
        .all()
    )

    cohorts: dict = {}
    for row in rows:
        cohort = cohorts.setdefault(
            row["cohort_month"],
            {
                "cohort_month": row["cohort_month"],
                "new_customers": row["new_customers"],
                "cac": row["cac"],
                "by_month": {},
            },
        )
        if row["month_offset"] is not None:
            cohort["by_month"][row["month_offset"]] = float(row["avg_cumulative_ltv"] or 0)

    result = []
    for cohort in cohorts.values():
        ltv_by_month = []
        last = 0.0
        for offset in range(months):
            if offset in cohort["by_month"]:
                last = cohort["by_month"][offset]
            ltv_by_month.append(last)

        cac = cohort["cac"]
        payback_month = None
        if cac is not None:
            cac = float(cac)
            for offset, cumulative in enumerate(ltv_by_month):
                if cumulative >= cac:
                    payback_month = offset
                    break

        result.append(
            {
                "cohort_month": cohort["cohort_month"],
                "new_customers": cohort["new_customers"],
                "cac": cac,
                "ltv_by_month": ltv_by_month,
                "payback_month": payback_month,
            }
        )

    result.sort(key=lambda c: c["cohort_month"])
    return result


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


# Below this many days of history, a linear fit is more noise than signal —
# an empty forecast (rather than a wild extrapolation from 2-3 data points)
# is the honest answer.
MIN_FORECAST_HISTORY_DAYS = 7


@router.get("/forecast", response_model=ForecastOut)
def metrics_forecast(
    history_days: int = Query(60, ge=MIN_FORECAST_HISTORY_DAYS, le=365),
    forecast_days: int = Query(30, ge=1, le=90),
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_plan_feature("forecast")),
    db: Session = Depends(get_db),
):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=history_days)
    rows = (
        db.execute(FORECAST_HISTORY_SQL, {"store_id": str(store.id), "start": start, "end": end}).mappings().all()
    )

    days_with_data = sum(1 for r in rows if float(r["revenue"]) or float(r["ad_spend"]))
    if days_with_data < MIN_FORECAST_HISTORY_DAYS:
        return ForecastOut(days=[], total_revenue=0.0, total_net_profit=0.0, total_ad_spend=0.0, true_roas=None)

    revenue_history = [float(r["revenue"]) for r in rows]
    net_profit_history = [float(r["net_profit"]) for r in rows]
    ad_spend_history = [float(r["ad_spend"]) for r in rows]

    revenue_forecast = [max(0.0, v) for v in linear_forecast(revenue_history, forecast_days)]
    net_profit_forecast = linear_forecast(net_profit_history, forecast_days)
    ad_spend_forecast = [max(0.0, v) for v in linear_forecast(ad_spend_history, forecast_days)]

    last_day = rows[-1]["day"]
    days_out = []
    for i in range(forecast_days):
        day = last_day + timedelta(days=i + 1)
        revenue = revenue_forecast[i]
        net_profit = net_profit_forecast[i]
        ad_spend = ad_spend_forecast[i]
        true_roas = round(net_profit / ad_spend, 2) if ad_spend else None
        days_out.append(
            ForecastDayOut(day=day, revenue=revenue, net_profit=net_profit, ad_spend=ad_spend, true_roas=true_roas)
        )

    total_revenue = sum(d.revenue for d in days_out)
    total_net_profit = sum(d.net_profit for d in days_out)
    total_ad_spend = sum(d.ad_spend for d in days_out)
    total_true_roas = round(total_net_profit / total_ad_spend, 2) if total_ad_spend else None

    return ForecastOut(
        days=days_out,
        total_revenue=total_revenue,
        total_net_profit=total_net_profit,
        total_ad_spend=total_ad_spend,
        true_roas=total_true_roas,
    )
