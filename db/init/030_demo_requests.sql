-- Demo requests from the public landing's form (POST /demo-requests).
-- Replaces the mailto: link the landing used to have: the request is kept
-- here even if the notification email to the team fails or SMTP isn't
-- configured, and it records which platform the prospect sells on.
CREATE TABLE demo_requests (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    platform TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_demo_requests_created_at ON demo_requests (created_at DESC);
