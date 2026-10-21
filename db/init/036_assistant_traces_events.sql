-- The assistant's memory (docs/ia/fase-4-2-diseno.md): one trace per tool run and, apart, what the person
-- did with the result. A trace keeps the structure of a call, never a figure, free text or a parameter
-- value outside the bounded list (backend/app/ai/traces.py). Both tables go with the account and expire
-- after 180 days.
CREATE TABLE assistant_traces (
    id UUID PRIMARY KEY,
    account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    store_id UUID REFERENCES stores(id) ON DELETE CASCADE,
    tool VARCHAR(60) NOT NULL,
    tier VARCHAR(2) NOT NULL DEFAULT 'L0',
    provider VARCHAR(40) NOT NULL DEFAULT 'template',
    -- 'ok' or a DenialReason.
    outcome VARCHAR(40) NOT NULL,
    validator_rejected BOOLEAN NOT NULL DEFAULT FALSE,
    -- {"days": 14}: only the bounded parameters, by name.
    param_shape JSONB NOT NULL DEFAULT '{}',
    latency_ms INTEGER NOT NULL,
    role VARCHAR(20) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_assistant_traces_account_tool ON assistant_traces (account_id, tool, created_at DESC);
CREATE INDEX ix_assistant_traces_created ON assistant_traces (created_at);

CREATE TABLE assistant_events (
    id UUID PRIMARY KEY,
    trace_id UUID NOT NULL REFERENCES assistant_traces(id) ON DELETE CASCADE,
    account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    -- shown, useful, not_useful, dismissed, acted (reserved until the assistant screen exists, stage 4.3).
    kind VARCHAR(20) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (trace_id, user_id, kind)
);
CREATE INDEX ix_assistant_events_account ON assistant_events (account_id, created_at);
