-- Per-user, per-event-type channel opt-out for the alerts/reports that
-- already exist (see store_alert_preferences/store_report_preferences,
-- app/services/notifications.py). No saved row for a (user, event_type)
-- pair means both channels are on, matching the behavior before this table
-- existed (every owner got both email and push, unconditionally).
CREATE TABLE notification_channel_preferences (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_type VARCHAR(30) NOT NULL CHECK (event_type IN ('cac_alert', 'roas_alert', 'weekly_report')),
    email_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    push_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, event_type)
);
