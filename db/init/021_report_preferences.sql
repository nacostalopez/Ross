-- Weekly email summary — opt-in per store, checked by
-- app/cli/send_weekly_reports.py. Separate from store_alert_preferences:
-- alerts are anomaly-triggered, this is calendar-triggered, and each has
-- its own script/cron schedule (daily vs weekly).
CREATE TABLE store_report_preferences (
    store_id UUID PRIMARY KEY REFERENCES stores(id) ON DELETE CASCADE,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
