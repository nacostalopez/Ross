-- Keep the latest data-sync result separate from OAuth and other connector errors.
ALTER TABLE IF EXISTS connector_status
    -- Fresh installs create connector_status in migration 008; IF EXISTS also
    -- keeps partially initialized local/test databases from failing startup.
    ADD COLUMN IF NOT EXISTS last_sync_status VARCHAR(20),
    ADD COLUMN IF NOT EXISTS last_sync_error TEXT;
