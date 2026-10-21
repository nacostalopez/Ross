"""The analytics tools: the figures the dashboard shows, read through the metrics service.

Every one is read-only. Each returns what the matching API route returns, validated against the same
output type, so the assistant and the screen can't disagree about a number.
"""

from datetime import timedelta

from pydantic import Field

from app.ai.tools.base import NoParams, Output, PeriodParams, Tool, ToolContext, ToolParams
from app.ai.tools.registry import register
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
from app.schemas.narrative import NarrativeOut
from app.services.metrics import service as metrics_service
from app.services.weekly_narrative import get_or_build_with_outcome

MODULE = "analytics"


class LtvParams(PeriodParams):
    months: int = Field(6, ge=1, le=12, description="Cuántos meses de vida del cliente mostrar")


class ForecastParams(ToolParams):
    history_days: int = Field(60, ge=metrics_service.MIN_FORECAST_HISTORY_DAYS, le=365)
    forecast_days: int = Field(30, ge=1, le=90)


def _over_period(read):
    """A tool that reads one metric over the last `days` days."""

    def run(ctx: ToolContext, params: PeriodParams) -> Output:
        start, end = ctx.rolling_window(params.days)
        return Output(read(ctx.db, str(ctx.store.id), start, end), (start, end))

    return run


def _ltv_cohorts(ctx: ToolContext, params: LtvParams) -> Output:
    start, end = ctx.rolling_window(params.days)
    return Output(metrics_service.ltv_cohorts(ctx.db, str(ctx.store.id), start, end, params.months), (start, end))


def _forecast(ctx: ToolContext, params: ForecastParams) -> Output:
    data = metrics_service.forecast(ctx.db, str(ctx.store.id), params.history_days, params.forecast_days, now=ctx.now)
    return Output(data, (ctx.now - timedelta(days=params.history_days), ctx.now))


def _weekly_narrative(ctx: ToolContext, params: NoParams) -> Output:
    narrative, rejected = get_or_build_with_outcome(ctx.db, ctx.store, now=ctx.now)
    data = {
        "text": narrative.text,
        "mood": narrative.mood,
        "source": "ia" if narrative.source == "ia" else "plantilla",
        "period_end": narrative.period_end,
        "facts": narrative.facts,
    }
    return Output(data, validator_rejected=rejected)


register(Tool(
    name="summary",
    module=MODULE,
    description="Ventas, ganancia neta, inversión publicitaria, ganancia real después de publicidad y True ROAS del período.",
    params=PeriodParams,
    result=MetricsSummaryOut,
    run=_over_period(metrics_service.summary),
))
register(Tool(
    name="pnl",
    module=MODULE,
    description=(
        "Estado de resultados del período: ingresos, descuentos, envíos, comisiones de pago, costo de mercadería, "
        "ganancia neta, inversión publicitaria y ganancia real."
    ),
    params=PeriodParams,
    result=PnlOut,
    run=_over_period(metrics_service.pnl),
))
register(Tool(
    name="daily",
    module=MODULE,
    description="Pedidos, ventas, ganancia e inversión publicitaria día por día.",
    params=PeriodParams,
    result=list[DailyMetricOut],
    run=_over_period(metrics_service.daily),
))
register(Tool(
    name="creatives",
    module=MODULE,
    description="Rendimiento por anuncio: gasto, impresiones, clics, CTR, CPC y CPM.",
    params=PeriodParams,
    result=list[CreativeMetricOut],
    plan_feature="creative_performance",
    run=_over_period(metrics_service.creatives),
))
register(Tool(
    name="ltv_cohorts",
    module=MODULE,
    description="Valor de vida del cliente por cohorte de adquisición y mes en que se recupera el costo de adquirirlo.",
    params=LtvParams,
    result=list[CohortLtvOut],
    plan_feature="ltv_cohorts",
    run=_ltv_cohorts,
))
register(Tool(
    name="cac_by_channel",
    module=MODULE,
    description="Costo de adquisición de cliente por canal, según el canal del primer pedido de cada cliente.",
    params=PeriodParams,
    result=list[ChannelCacOut],
    plan_feature="cac_by_channel",
    run=_over_period(metrics_service.cac_by_channel),
))
register(Tool(
    name="attribution_by_channel",
    module=MODULE,
    description="Pedidos, pedidos repetidos e ingresos por canal: cada pedido cuenta para su propio canal.",
    params=PeriodParams,
    result=list[ChannelAttributionOut],
    plan_feature="attribution_by_channel",
    run=_over_period(metrics_service.attribution_by_channel),
))
register(Tool(
    name="forecast",
    module=MODULE,
    description="Proyección de ventas, ganancia e inversión para los próximos días, a partir de la tendencia reciente.",
    params=ForecastParams,
    result=ForecastOut,
    plan_feature="forecast",
    run=_forecast,
))
register(Tool(
    name="weekly_narrative",
    module=MODULE,
    description="La semana contada en lenguaje simple: los últimos 7 días frente a los 7 anteriores.",
    params=NoParams,
    result=NarrativeOut,
    run=_weekly_narrative,
))
