-- Personal "actividad reciente" feed for the Perfil page — deliberately
-- narrow (a handful of high-signal actions, see
-- app/services/activity_log.py), not a generic audit-logging middleware.
-- Always queried by user_id (a personal feed), account_id is kept for
-- context/future account-wide views but isn't the primary access pattern.
CREATE TABLE account_activity_log (
    id UUID PRIMARY KEY,
    account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    action VARCHAR(50) NOT NULL,
    detail VARCHAR(255) NOT NULL,
    -- clock_timestamp(), not now() — see the model's docstring
    -- (app/models/audit.py) for why now() ties within one transaction.
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX idx_account_activity_log_user_time ON account_activity_log (user_id, created_at DESC);
