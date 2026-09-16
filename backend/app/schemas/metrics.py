from datetime import date

from pydantic import BaseModel


class MetricsSummaryOut(BaseModel):
    revenue: float
    net_profit: float
    total_ad_spend: float
    real_profit_after_ads: float
    true_roas: float | None


class DailyMetricOut(BaseModel):
    day: date
    total_orders: int
    total_revenue: float
    total_cogs: float
    total_gateway_fees: float
    total_net_profit: float
    ad_spend: float


class CreativeMetricOut(BaseModel):
    platform: str
    ad_id: str
    ad_name: str | None
    campaign_name: str | None
    thumbnail_url: str | None
    spend: float
    impressions: int
    clicks: int
    # Percent (e.g. 2.35 = 2.35%), null with zero impressions.
    ctr: float | None
    cpc: float | None
    cpm: float | None


class CohortLtvOut(BaseModel):
    cohort_month: date
    new_customers: int
    # Blended spend / new_customers for the cohort's acquisition month; null
    # when there's no ad_spend data for that month.
    cac: float | None
    # Cumulative avg net profit per customer; index 0 = acquisition month.
    ltv_by_month: list[float]
    # First index where ltv_by_month >= cac; null if never (within the
    # window) or if cac itself is null.
    payback_month: int | None


class ChannelCacOut(BaseModel):
    cohort_month: date
    # Normalized acquisition channel ("meta", "google", or "other" for
    # anything that didn't map to a known ad platform alias).
    channel: str
    new_customers: int
    # Null when there's no ad_spend for this channel/month.
    spend: float | None
    cac: float | None


class ChannelAttributionOut(BaseModel):
    # Normalized channel ("meta", "google", or "other"), same mapping as
    # ChannelCacOut — but here every order in the period counts toward its
    # own channel, not just each customer's first ever order.
    channel: str
    orders: int
    # Orders that aren't the customer's first order ever (regardless of
    # whether that first order falls in this date range) — the revenue
    # CAC-by-channel's first-touch-only credit doesn't attribute to this
    # channel today.
    repeat_orders: int
    revenue: float
    net_profit: float
    # Null when there's no ad_spend for this channel in the period.
    spend: float | None
    roas: float | None


class ForecastDayOut(BaseModel):
    day: date
    # revenue/ad_spend are clamped to >= 0 (can't be negative); net_profit
    # is not — a linear fit can legitimately project a loss.
    revenue: float
    net_profit: float
    ad_spend: float
    true_roas: float | None


class ForecastOut(BaseModel):
    # Empty when there's under MIN_FORECAST_HISTORY_DAYS of history — a
    # trend line from a handful of points would be noise, not signal.
    days: list[ForecastDayOut]
    total_revenue: float
    total_net_profit: float
    total_ad_spend: float
    true_roas: float | None
