-- Which version of the terms of service and privacy policy a person accepted, and when
-- (backend/app/legal.py). NULL for accounts and requests created before the texts existed.
ALTER TABLE users ADD COLUMN IF NOT EXISTS terms_accepted_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS terms_version VARCHAR(40);

ALTER TABLE demo_requests ADD COLUMN IF NOT EXISTS privacy_accepted_at TIMESTAMPTZ;
ALTER TABLE demo_requests ADD COLUMN IF NOT EXISTS privacy_version VARCHAR(40);
