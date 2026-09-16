from typing import Literal

from pydantic import BaseModel, field_validator

# Every widget the frontend knows how to render. Adding a new widget type
# means updating this list, the frontend's WIDGET_LABELS, and (for a stat
# widget) STAT_WIDGET_TYPES/STAT_FIELD_MAP in app.js.
WidgetType = Literal[
    "stat_roas",
    "stat_revenue",
    "stat_net_profit",
    "stat_ad_spend",
    "stat_real_profit",
    "chart_daily",
    "connector_status",
    "creative_performance",
    "ltv_cohorts",
    "cac_by_channel",
    "attribution_by_channel",
    "forecast",
]

DEFAULT_WIDGETS = [
    {"type": "stat_roas", "hero": True},
    {"type": "stat_revenue", "hero": False},
    {"type": "stat_net_profit", "hero": False},
    {"type": "stat_ad_spend", "hero": False},
    {"type": "stat_real_profit", "hero": False},
    {"type": "chart_daily", "hero": False},
    {"type": "connector_status", "hero": False},
]


class WidgetConfig(BaseModel):
    type: WidgetType
    # 2x2 hero tile in the bento grid — only meaningful for stat_* widgets,
    # but harmless if set elsewhere (the frontend just ignores it there).
    hero: bool = False


class DashboardLayoutIn(BaseModel):
    widgets: list[WidgetConfig]

    @field_validator("widgets")
    @classmethod
    def no_duplicate_widget_types(cls, widgets: list[WidgetConfig]) -> list[WidgetConfig]:
        types = [w.type for w in widgets]
        if len(types) != len(set(types)):
            raise ValueError("Each widget type can only appear once in a layout")
        return widgets


class DashboardLayoutOut(BaseModel):
    widgets: list[WidgetConfig]
