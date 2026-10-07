-- Proactive CAC/ROAS alerts — opt-in per store, checked by
-- app/cli/run_alert_checks.py (no scheduler in-process, see that script).
CREATE TABLE store_alert_preferences (
    store_id UUID PRIMARY KEY REFERENCES stores(id) ON DELETE CASCADE,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    -- NULL = no CAC alert configured; there's no dollar default that makes
    -- sense across businesses.
    cac_threshold NUMERIC(12, 4),
    roas_threshold NUMERIC(6, 2) NOT NULL DEFAULT 1.0,
    roas_days_n SMALLINT NOT NULL DEFAULT 3,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Dedupe log so the same alert isn't resent every single check — see
-- app/services/alerts.py for how dedupe_key is built per alert_type.
CREATE TABLE alert_log (
    id UUID PRIMARY KEY,
    store_id UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    alert_type VARCHAR(20) NOT NULL,
    dedupe_key VARCHAR(100) NOT NULL,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_alert_log_dedupe ON alert_log (store_id, alert_type, dedupe_key, sent_at DESC);
