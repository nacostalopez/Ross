"""The metrics that need more than running a query: LTV cohorts and the forecast.

They return plain dicts, the same shape the routes returned before, so the routes, the report and the
assistant's tools all read the same figures. The caller resolves and checks the store first; these
functions only ever see a store id that was already authorized.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.services.forecasting import linear_forecast
from app.services.metrics.queries import FORECAST_HISTORY_SQL, LTV_COHORTS_SQL

# Below this many days of history, a linear fit is more noise than signal —
# an empty forecast (rather than a wild extrapolation from 2-3 data points)
# is the honest answer.
MIN_FORECAST_HISTORY_DAYS = 7


def ltv_cohorts(db: Session, store_id: str, start: datetime, end: datetime, months: int) -> list[dict]:
    rows = db.execute(LTV_COHORTS_SQL, {"store_id": store_id, "start": start, "end": end, "months": months}).mappings().all()

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


def forecast(
    db: Session,
    store_id: str,
    history_days: int,
    forecast_days: int,
    now: Optional[datetime] = None,
) -> dict:
    end = now or datetime.now(timezone.utc)
    start = end - timedelta(days=history_days)
    rows = db.execute(FORECAST_HISTORY_SQL, {"store_id": store_id, "start": start, "end": end}).mappings().all()

    days_with_data = sum(1 for r in rows if float(r["revenue"]) or float(r["ad_spend"]))
    if days_with_data < MIN_FORECAST_HISTORY_DAYS:
        return {"days": [], "total_revenue": 0.0, "total_net_profit": 0.0, "total_ad_spend": 0.0, "true_roas": None}

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
            {"day": day, "revenue": revenue, "net_profit": net_profit, "ad_spend": ad_spend, "true_roas": true_roas}
        )

    total_revenue = sum(d["revenue"] for d in days_out)
    total_net_profit = sum(d["net_profit"] for d in days_out)
    total_ad_spend = sum(d["ad_spend"] for d in days_out)
    total_true_roas = round(total_net_profit / total_ad_spend, 2) if total_ad_spend else None

    return {
        "days": days_out,
        "total_revenue": total_revenue,
        "total_net_profit": total_net_profit,
        "total_ad_spend": total_ad_spend,
        "true_roas": total_true_roas,
    }
