from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_owned_store
from app.models import Store
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

router = APIRouter(prefix="/stores/{store_id}/metrics", tags=["metrics"])

# Summary reads straight from the `orders` hypertable so it's accurate the
# instant an order is ingested (the continuous aggregate can lag up to an
# hour behind, see /daily below).
SUMMARY_SQL = text(
    """
    WITH metrics_orders AS (
        SELECT
            COALESCE(SUM(gross_amount), 0) AS revenue,
            COALESCE(SUM(net_profit), 0) AS net_profit
        FROM orders
        WHERE store_id = :store_id
          AND time BETWEEN :start AND :end
    ),
    metrics_ads AS (
        SELECT COALESCE(SUM(spend), 0) AS total_ad_spend
        FROM ad_spend
        WHERE store_id = :store_id
          AND time BETWEEN :start AND :end
    )
    SELECT
        o.revenue,
        o.net_profit,
        a.total_ad_spend,
        (o.net_profit - a.total_ad_spend) AS real_profit_after_ads,
        -- Unlike plain ROAS (revenue / spend), this is net of discounts,
        -- shipping, payment-gateway fees and COGS (net_profit is a
        -- GENERATED column on orders, see db/init/003_hypertables.sql) —
        -- it answers "how much profit per ad dollar", not "how much
        -- revenue per ad dollar".
        ROUND((o.net_profit / NULLIF(a.total_ad_spend, 0))::numeric, 2) AS true_roas
    FROM metrics_orders o, metrics_ads a
    """
)

# Full P&L, unlike SUMMARY_SQL above which only exposes the net figures —
# every line item here is already summed into orders.net_profit's generated
# column (db/init/003_hypertables.sql), just never surfaced individually
# until now.
PNL_SQL = text(
    """
    WITH metrics_orders AS (
        SELECT
            COALESCE(SUM(gross_amount), 0) AS revenue,
            COALESCE(SUM(discounts), 0) AS discounts,
            COALESCE(SUM(shipping_fee), 0) AS shipping_fee,
            COALESCE(SUM(payment_gateway_fee), 0) AS payment_gateway_fee,
            COALESCE(SUM(cogs_total), 0) AS cogs_total,
            COALESCE(SUM(net_profit), 0) AS net_profit
        FROM orders
        WHERE store_id = :store_id
          AND time BETWEEN :start AND :end
    ),
    metrics_ads AS (
        SELECT COALESCE(SUM(spend), 0) AS total_ad_spend
        FROM ad_spend
        WHERE store_id = :store_id
          AND time BETWEEN :start AND :end
    )
    SELECT
        o.revenue,
        o.discounts,
        o.shipping_fee,
        o.payment_gateway_fee,
        o.cogs_total,
        o.net_profit,
        a.total_ad_spend,
        (o.net_profit - a.total_ad_spend) AS real_profit_after_ads
    FROM metrics_orders o, metrics_ads a
    """
)

# Daily breakdown reads from the daily_financial_summary continuous
# aggregate (fast, but refreshed on an hourly schedule) joined with daily
# ad spend.
DAILY_SQL = text(
    """
    WITH ads_by_day AS (
        SELECT time_bucket('1 day', time) AS day, SUM(spend) AS spend
        FROM ad_spend
        WHERE store_id = :store_id
          AND time BETWEEN :start AND :end
        GROUP BY day
    )
    SELECT
        f.day,
        f.total_orders,
        f.total_revenue,
        f.total_cogs,
        f.total_gateway_fees,
        f.total_net_profit,
        COALESCE(a.spend, 0) AS ad_spend
    FROM daily_financial_summary f
    LEFT JOIN ads_by_day a ON a.day = f.day
    WHERE f.store_id = :store_id
      AND f.day BETWEEN :start AND :end
    ORDER BY f.day
    """
)


# One row per ad (creative), summed across the range and ranked by spend —
# what a media buyer scans first to decide what to scale or kill. MAX() on
# the text columns is safe here since they're functionally dependent on
# ad_id (a given ad doesn't change name/campaign mid-range in practice).
CREATIVES_SQL = text(
    """
    SELECT
        platform,
        ad_id,
        MAX(ad_name) AS ad_name,
        MAX(campaign_name) AS campaign_name,
        MAX(thumbnail_url) AS thumbnail_url,
        SUM(spend) AS spend,
        SUM(impressions) AS impressions,
        SUM(clicks) AS clicks,
        ROUND((SUM(clicks)::numeric / NULLIF(SUM(impressions), 0)) * 100, 2) AS ctr,
        ROUND((SUM(spend) / NULLIF(SUM(clicks), 0))::numeric, 4) AS cpc,
        ROUND((SUM(spend) / NULLIF(SUM(impressions), 0) * 1000)::numeric, 4) AS cpm
    FROM creative_performance
    WHERE store_id = :store_id
      AND time BETWEEN :start AND :end
    GROUP BY platform, ad_id
    ORDER BY spend DESC
    """
)


# Unlike every other endpoint here, :start/:end filter *cohorts* (by
# first_order_at), not orders — the LTV curve for a cohort looks forward
# from its acquisition month regardless of :end, capped by :months. A
# cohort acquired last month can only ever have one populated month-offset;
# that's correct cohort-analysis behavior, not a bug. CAC is blended
# (total ad_spend in the cohort's acquisition month / new_customers) —
# no per-channel attribution yet.
LTV_COHORTS_SQL = text(
    """
    WITH cohorts AS (
        SELECT id, date_trunc('month', first_order_at) AS cohort_month
        FROM customers
        WHERE store_id = :store_id
          AND first_order_at BETWEEN :start AND :end
    ),
    cohort_sizes AS (
        SELECT cohort_month, COUNT(*) AS new_customers
        FROM cohorts
        GROUP BY cohort_month
    ),
    order_months AS (
        SELECT
            c.cohort_month,
            (
                (EXTRACT(YEAR FROM o.time) - EXTRACT(YEAR FROM c.cohort_month)) * 12
                + (EXTRACT(MONTH FROM o.time) - EXTRACT(MONTH FROM c.cohort_month))
            )::int AS month_offset,
            o.net_profit
        FROM orders o
        JOIN cohorts c ON o.customer_id = c.id
    ),
    monthly_profit AS (
        SELECT cohort_month, month_offset, SUM(net_profit) AS profit
        FROM order_months
        WHERE month_offset BETWEEN 0 AND :months - 1
        GROUP BY cohort_month, month_offset
    ),
    cumulative AS (
        SELECT
            cohort_month,
            month_offset,
            SUM(profit) OVER (PARTITION BY cohort_month ORDER BY month_offset) AS cumulative_profit
        FROM monthly_profit
    ),
    spend AS (
        SELECT date_trunc('month', time) AS cohort_month, SUM(spend) AS spend
        FROM ad_spend
        WHERE store_id = :store_id
        GROUP BY cohort_month
    )
    SELECT
        cs.cohort_month,
        cs.new_customers,
        sp.spend / NULLIF(cs.new_customers, 0) AS cac,
        cu.month_offset,
        cu.cumulative_profit / NULLIF(cs.new_customers, 0) AS avg_cumulative_ltv
    FROM cohort_sizes cs
    LEFT JOIN spend sp ON sp.cohort_month = cs.cohort_month
    LEFT JOIN cumulative cu ON cu.cohort_month = cs.cohort_month
    ORDER BY cs.cohort_month, cu.month_offset
    """
)


# CAC per acquisition channel, unlike LTV_COHORTS_SQL's blended CAC above.
# A customer's channel is whichever normalized value their *first* order's
# attribution_utm_source maps to — same acquisition-month boundary as the
# blended cohorts query (customers.first_order_at), just split by channel
# instead of summed across all of them. utm_source is freeform text set by
# whoever built the ad, so it's normalized against the small set of aliases
# real campaigns actually use; anything else falls into 'other', which
# correctly gets no spend/CAC since ad_spend only has 'meta'/'google'/
# 'mercadopago' rows (see connectors' fetch_ad_spend) — no data to divide by.
CAC_BY_CHANNEL_SQL = text(
    """
    WITH channel_orders AS (
        SELECT
            o.customer_id,
            o.time,
            CASE lower(coalesce(o.attribution_utm_source, ''))
                WHEN 'meta' THEN 'meta'
                WHEN 'facebook' THEN 'meta'
                WHEN 'fb' THEN 'meta'
                WHEN 'instagram' THEN 'meta'
                WHEN 'google' THEN 'google'
                WHEN 'adwords' THEN 'google'
                WHEN 'google ads' THEN 'google'
                ELSE 'other'
            END AS channel
        FROM orders o
        WHERE o.store_id = :store_id
          AND o.customer_id IS NOT NULL
    ),
    first_order_channel AS (
        -- One row per customer: the channel of their earliest order.
        SELECT DISTINCT ON (customer_id) customer_id, channel
        FROM channel_orders
        ORDER BY customer_id, time ASC
    ),
    cohorts AS (
        SELECT c.id, foc.channel, date_trunc('month', c.first_order_at) AS cohort_month
        FROM customers c
        JOIN first_order_channel foc ON foc.customer_id = c.id
        WHERE c.store_id = :store_id
          AND c.first_order_at BETWEEN :start AND :end
    ),
    cohort_sizes AS (
        SELECT cohort_month, channel, COUNT(*) AS new_customers
        FROM cohorts
        GROUP BY cohort_month, channel
    ),
    spend AS (
        SELECT date_trunc('month', time) AS cohort_month, platform AS channel, SUM(spend) AS spend
        FROM ad_spend
        WHERE store_id = :store_id
        GROUP BY cohort_month, platform
    )
    SELECT
        cs.cohort_month,
        cs.channel,
        cs.new_customers,
        sp.spend,
        sp.spend / NULLIF(cs.new_customers, 0) AS cac
    FROM cohort_sizes cs
    LEFT JOIN spend sp ON sp.cohort_month = cs.cohort_month AND sp.channel = cs.channel
    ORDER BY cs.cohort_month, cs.channel
    """
)


# Multi-touch counterpart to CAC_BY_CHANNEL_SQL above: that query credits a
# customer's *entire* acquisition to whichever channel drove their first
# order, and stops there. This one credits *every* order to its own order's
# channel, so a channel that wins a customer back for a second or third
# purchase gets counted for that revenue too, instead of it silently
# disappearing into whichever channel happened to land the first sale.
# Consequently this is period-based (order time within :start/:end), not
# cohort-based (customer's first_order_at) like CAC-by-channel/LTV-cohorts
# above — revenue happens continuously, it isn't tied to an acquisition
# month. repeat_orders (orders that aren't a customer's first order ever,
# regardless of whether that first order falls inside this date range) is
# what actually makes this "multi-touch": it's the count CAC-by-channel's
# first-touch-only credit silently drops for each channel.
ATTRIBUTION_BY_CHANNEL_SQL = text(
    """
    WITH first_orders AS (
        SELECT customer_id, MIN(time) AS first_order_time
        FROM orders
        WHERE store_id = :store_id
          AND customer_id IS NOT NULL
        GROUP BY customer_id
    ),
    attributed_orders AS (
        SELECT
            o.gross_amount,
            o.net_profit,
            (o.time > fo.first_order_time) AS is_repeat_order,
            CASE lower(coalesce(o.attribution_utm_source, ''))
                WHEN 'meta' THEN 'meta'
                WHEN 'facebook' THEN 'meta'
                WHEN 'fb' THEN 'meta'
                WHEN 'instagram' THEN 'meta'
                WHEN 'google' THEN 'google'
                WHEN 'adwords' THEN 'google'
                WHEN 'google ads' THEN 'google'
                ELSE 'other'
            END AS channel
        FROM orders o
        JOIN first_orders fo ON fo.customer_id = o.customer_id
        WHERE o.store_id = :store_id
          AND o.customer_id IS NOT NULL
          AND o.time BETWEEN :start AND :end
    ),
    by_channel AS (
        SELECT
            channel,
            COUNT(*) AS orders,
            COUNT(*) FILTER (WHERE is_repeat_order) AS repeat_orders,
            SUM(gross_amount) AS revenue,
            SUM(net_profit) AS net_profit
        FROM attributed_orders
        GROUP BY channel
    ),
    spend AS (
        SELECT platform AS channel, SUM(spend) AS spend
        FROM ad_spend
        WHERE store_id = :store_id
          AND time BETWEEN :start AND :end
        GROUP BY platform
    )
    SELECT
        bc.channel,
        bc.orders,
        bc.repeat_orders,
        bc.revenue,
        bc.net_profit,
        sp.spend,
        CASE WHEN sp.spend > 0 THEN bc.revenue / sp.spend ELSE NULL END AS roas
    FROM by_channel bc
    LEFT JOIN spend sp ON sp.channel = bc.channel
    ORDER BY bc.channel
    """
)


# Backing query for /forecast. Deliberately reads straight from
# orders/ad_spend (like SUMMARY_SQL above) rather than the
# daily_financial_summary continuous aggregate, which only refreshes on an
# hourly policy — a forecast built on stale/incomplete recent days would be
# wrong in a way that's hard to notice. generate_series fills in days with
# no activity as 0 so the day-index used by linear_forecast lines up with
# real calendar days (a gap would silently compress the timeline).
FORECAST_HISTORY_SQL = text(
    """
    WITH days AS (
        SELECT generate_series(date_trunc('day', :start), date_trunc('day', :end), interval '1 day') AS day
    ),
    orders_by_day AS (
        SELECT date_trunc('day', time) AS day, SUM(gross_amount) AS revenue, SUM(net_profit) AS net_profit
        FROM orders
        WHERE store_id = :store_id
          AND time BETWEEN :start AND :end
        GROUP BY day
    ),
    spend_by_day AS (
        SELECT date_trunc('day', time) AS day, SUM(spend) AS ad_spend
        FROM ad_spend
        WHERE store_id = :store_id
          AND time BETWEEN :start AND :end
        GROUP BY day
    )
    SELECT
        d.day,
        COALESCE(o.revenue, 0) AS revenue,
        COALESCE(o.net_profit, 0) AS net_profit,
        COALESCE(s.ad_spend, 0) AS ad_spend
    FROM days d
    LEFT JOIN orders_by_day o ON o.day = d.day
    LEFT JOIN spend_by_day s ON s.day = d.day
    ORDER BY d.day
    """
)


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
