-- Captured at subscribe time so "Dispositivos y sesiones" (see
-- GET /push/subscriptions) can show something more useful than a raw
-- endpoint URL — a friendly label is derived from this server-side.
ALTER TABLE push_subscriptions ADD COLUMN user_agent TEXT;
