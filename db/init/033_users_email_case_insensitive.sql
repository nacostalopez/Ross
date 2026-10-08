-- One account per email address, however it's capitalised. users.email was
-- only unique byte for byte, so "Ana@gmail.com" and "ana@gmail.com" could
-- register two accounts (a phone capitalising the first letter was enough).
-- The app now stores addresses trimmed and lowercased and compares them
-- lowercased (app/services/users.py); this index stops a duplicate even when
-- two sign-ups for the same address arrive at the same time.

-- Existing rows to the same form. Fails on purpose if two rows already
-- collide, so a real duplicate gets looked at instead of silently merged.
UPDATE users SET email = lower(btrim(email)) WHERE email <> lower(btrim(email));
UPDATE account_invites SET email = lower(btrim(email)) WHERE email <> lower(btrim(email));

CREATE UNIQUE INDEX ux_users_email_lower ON users (lower(email));
