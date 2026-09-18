-- Billing scaffolding — see the "Estrategia de Billing" proposal doc.
-- monthly_price is NULL on every plan on purpose: real pricing is a
-- business decision, not a schema decision. Every account gets pinned to
-- 'scale' (below) so this goes live with zero behavior change until
-- someone deliberately assigns a lower plan.
CREATE TABLE plans (
    id VARCHAR(20) PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    monthly_price NUMERIC(10, 2),
    max_stores INTEGER,
    max_orders_per_month INTEGER,
    max_users INTEGER,
    features JSONB NOT NULL DEFAULT '[]'
);

INSERT INTO plans (id, name, max_stores, max_orders_per_month, max_users, features) VALUES
    ('starter', 'Starter', 1, 500, 2, '[]'),
    ('growth', 'Growth', 5, 5000, 5,
        '["weekly_report", "ltv_cohorts", "cac_by_channel", "attribution_by_channel", "forecast"]'),
    ('scale', 'Scale', NULL, NULL, NULL,
        '["weekly_report", "ltv_cohorts", "cac_by_channel", "attribution_by_channel", "forecast",
          "creative_performance", "store_role_overrides", "audit_log"]');

CREATE TABLE subscriptions (
    id UUID PRIMARY KEY,
    account_id UUID NOT NULL UNIQUE REFERENCES accounts(id) ON DELETE CASCADE,
    plan_id VARCHAR(20) NOT NULL REFERENCES plans(id),
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    trial_ends_at TIMESTAMPTZ,
    current_period_end TIMESTAMPTZ,
    payment_provider VARCHAR(20),
    provider_subscription_id VARCHAR(255),
    provider_customer_id VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE invoices (
    id UUID PRIMARY KEY,
    account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    provider_invoice_id VARCHAR(255),
    amount NUMERIC(12, 2) NOT NULL,
    currency VARCHAR(3) NOT NULL DEFAULT 'USD',
    status VARCHAR(20) NOT NULL,
    issued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    paid_at TIMESTAMPTZ
);

-- Grandfather every account that exists before billing did.
INSERT INTO subscriptions (id, account_id, plan_id, status)
SELECT gen_random_uuid(), id, 'scale', 'active' FROM accounts;
