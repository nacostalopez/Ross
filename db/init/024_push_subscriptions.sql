-- Web Push subscriptions — one row per (user, browser/device) that opted
-- in to push notifications from the frontend's notification toggle. This
-- only tracks *who can receive* a push; *what* triggers one is still the
-- existing per-store alert/report opt-in (store_alert_preferences /
-- store_report_preferences) — see app/services/push.py and
-- app/services/notifications.py::send_to_store.
CREATE TABLE push_subscriptions (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    endpoint TEXT NOT NULL,
    p256dh_key TEXT NOT NULL,
    auth_key TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, endpoint)
);
