from typing import Literal

from pydantic import BaseModel

EventType = Literal["cac_alert", "roas_alert", "weekly_report"]


class NotificationChannelPreferenceItem(BaseModel):
    event_type: EventType
    email_enabled: bool
    push_enabled: bool


class NotificationPreferencesOut(BaseModel):
    preferences: list[NotificationChannelPreferenceItem]


class NotificationPreferencesIn(BaseModel):
    preferences: list[NotificationChannelPreferenceItem]
