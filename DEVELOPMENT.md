# Development Guide - Ross Backend

This document describes how to develop, test, and deploy the Ross backend.

## Getting Started

### Prerequisites
- Python 3.10+
- Docker and Docker Compose
- PostgreSQL client tools (optional, for direct DB access)

### Initial Setup

1. **Clone and navigate to backend:**
   ```bash
   cd backend
   ```

2. **Create virtual environment:**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Copy environment template:**
   ```bash
   cp .env.example .env
   ```

5. **Configure environment variables** (especially API keys for connectors):
   - Edit `.env` with your Shopify, Meta, and Google API credentials

6. **Install the pre-commit hook** (from the repo root, not `backend/`):
   ```bash
   pip install pre-commit
   pre-commit install
   ```
   This runs `ruff` (lint + format, auto-fixing) and the tests that don't need
   a live database (`pytest -m "not db"`) before every commit. It won't catch
   DB-dependent test failures — run the full suite yourself before pushing
   (see Testing below).

### Running the Application

1. **Start Docker services:**
   ```bash
   docker-compose up -d
   ```

2. **Run migrations** (if needed):
   ```bash
   # Already run via docker-entrypoint-initdb.d in docker-compose
   ```

3. **Start the server:**
   ```bash
   uvicorn app.main:app --reload
   ```

The API will be available at `http://localhost:8100`.

## Testing

### Test Infrastructure

We use `pytest` with `pytest-asyncio` for async endpoint testing. Tests run against a separate test database (`test-db` service in docker-compose).

### Running Tests

1. **Start test database (once per session):**
   ```bash
   docker-compose up test-db -d
   ```

2. **Run all tests:**
   ```bash
   pytest
   ```

3. **Run specific test file:**
   ```bash
   pytest tests/test_auth.py -v
   ```

4. **Run specific test:**
   ```bash
   pytest tests/test_auth.py::TestRegister::test_register_success -v
   ```

5. **Run tests with coverage:**
   ```bash
   pytest --cov=app --cov-report=html
   ```

### Test Organization

- **tests/conftest.py** - Fixtures for database, client, users, stores
- **tests/test_auth.py** - Authentication (register/login)
- **tests/test_ownership.py** - Ownership scoping (prevent cross-account access)
- **tests/test_encryption.py** - Credential encryption
- **tests/test_connectors_interface.py** - Every connector implements `BaseConnector` fully
- **tests/test_connectors_schema.py** - Meta/Google ad-spend and Shopify order output shape (mocked HTTP)
- **tests/test_webhooks_e2e.py** - Shopify webhook end-to-end against a real DB: signature validation, order upsert, audit logging, connector_status

### Example Test Output

```
tests/test_auth.py::TestRegister::test_register_success PASSED
tests/test_auth.py::TestLogin::test_login_success PASSED
tests/test_ownership.py::TestOwnershipScoping::test_cannot_access_other_account_store PASSED
tests/test_encryption.py::TestEncryption::test_encrypt_decrypt_roundtrip PASSED
```

### Test Markers

Tests are marked `auth`, `ownership`, `encryption`, `connector`, `webhook`,
`integration`, `slow`, and — importantly — `db` for anything needing a live
database connection (`docker compose up test-db -d` first). Run just the
offline ones with `pytest -m "not db"` (this is what the pre-commit hook
runs); run everything with a plain `pytest`.

The rate limiter (see below) is process-global, in-memory storage — the
`_reset_rate_limiter` autouse fixture in `conftest.py` clears it before every
test so unrelated login/register calls earlier in the run don't trip a 429
in a later, unrelated test.

### Postman Collection

`postman/ross.postman_collection.json` covers the full request lifecycle
against a running stack (`docker compose up`): register/login, store
CRUD, order/ad-spend ingestion, metrics math, and the Shopify webhook
(valid + invalid signature, computed in a pre-request script via
`CryptoJS.HmacSHA256`). Import it into Postman directly, or run headless:

```bash
npm install -g newman
newman run postman/ross.postman_collection.json
```

## Continuous Integration

`.github/workflows/ci.yml` runs on every push/PR: spins up a `timescaledb`
service container, applies `db/init/*.sql` to it directly with `psql`
(mirroring what `docker-entrypoint-initdb.d` does locally), then runs `ruff
check`, a check that no real `.env` is committed, and the full `pytest`
suite. See `.github/pull_request_template.md` for the PR checklist.

## Connector Architecture

### Base Connector Interface

All connectors inherit from `BaseConnector` in `app/connectors/__init__.py`:

```python
class BaseConnector(ABC):
    def get_oauth_url(self, state: str) -> str: ...
    def exchange_auth_code(self, code: str, redirect_uri: str) -> OAuthToken: ...
    def validate_webhook_signature(self, body: str, signature: str) -> bool: ...
    def process_webhook(self, event_type: str, data: dict) -> dict: ...
    def fetch_historical_data(self, start_date: datetime, end_date: datetime) -> dict: ...
```

### Implemented Connectors

#### 1. Shopify Connector (`app/connectors/shopify.py`)

**OAuth Flow:**
```
User → /connectors/shopify/auth-url?store_id=xxx
  ↓
/connectors/shopify/callback (with Shopify's auth code)
  ↓
Token stored encrypted in store_credentials table
```

**Webhook Support:**
- Event: `orders/create` and `orders/updated`
- Signature validation: HMAC-SHA256 via `X-Shopify-Hmac-SHA256` header
- Data normalized and ingested into `orders` table

**Usage:**
```bash
# Get OAuth URL
curl -X POST http://localhost:8100/connectors/shopify/auth-url \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{"store_id": "xxx", "shop_domain": "mystore.myshopify.com"}'

# Shopify webhook (received from Shopify)
curl -X POST http://localhost:8100/connectors/shopify/webhook/{store_id} \
  -H "X-Shopify-Hmac-SHA256: ..." \
  -H "X-Shopify-Topic: orders/create" \
  -d '{...order data...}'
```

#### 2. Meta (Facebook) Ads Connector (`app/connectors/meta.py`)

**OAuth Flow:**
```
User → /connectors/meta/auth-url?store_id=xxx
  ↓
/connectors/meta/callback (with Meta's auth code)
  ↓
Token stored encrypted in store_credentials table
```

**Ad Spend Sync:**
```bash
# Sync ad spend for date range
curl -X POST http://localhost:8100/connectors/meta/sync-ad-spend \
  -H "Authorization: Bearer {token}" \
  -d '{"store_id": "xxx", "start_date": "2026-01-01", "end_date": "2026-01-31"}'
```

**Data Fetched:**
- Campaign spend by day
- Impressions and clicks
- Stored in `ad_spend` table with `platform="meta"`

#### 3. Google Ads Connector (`app/connectors/google.py`)

**OAuth Flow:**
```
User → /connectors/google/auth-url?store_id=xxx
  ↓
/connectors/google/callback (with Google's auth code)
  ↓
Token stored encrypted + refresh token (6-month rotation)
```

**Ad Spend Sync:**
```bash
# Sync ad spend (auto-refreshes token if expired)
curl -X POST http://localhost:8100/connectors/google/sync-ad-spend \
  -H "Authorization: Bearer {token}" \
  -d '{"store_id": "xxx", "start_date": "2026-01-01", "end_date": "2026-01-31"}'
```

**Data Fetched:**
- Campaign performance via GAQL queries
- Spend in micros (converted to currency)
- Stored in `ad_spend` table with `platform="google"`

#### 4. Tiendanube Connector (`app/connectors/tiendanube.py`)

**OAuth Flow:**
```
User → /connectors/tiendanube/auth-url?store_id=xxx
  ↓
/connectors/tiendanube/callback (with Tiendanube's auth code)
  ↓
Token + Tiendanube's own store id (provider_account_id) stored in store_credentials
```

**Webhook Support:**
- Event: `order/created` and `order/updated`
- Signature validation: HMAC-SHA256 (base64), same shape as Shopify's
- Data normalized and ingested into `orders` table

**Usage:**
```bash
# Get OAuth URL
curl -X POST http://localhost:8100/connectors/tiendanube/auth-url \
  -H "Authorization: Bearer {token}" \
  -d '{"store_id": "xxx"}'

# Tiendanube webhook (received from Tiendanube)
curl -X POST http://localhost:8100/connectors/tiendanube/webhook/{store_id} \
  -H "X-Linkedstore-Hmac-Sha256: ..." \
  -H "X-Linkedstore-Topic: order/created" \
  -d '{...order data...}'
```

#### 5. MercadoPago Connector (`app/connectors/mercadopago.py`)

**OAuth Flow:**
```
User → /connectors/mercadopago/auth-url?store_id=xxx
  ↓
/connectors/mercadopago/callback (with MercadoPago's auth code)
  ↓
Token stored encrypted + refresh token (~180-day rotation)
```

Modeled pull-based only, like Meta/Google — MercadoPago also supports
payment webhooks, but ad_spend metrics are already daily-bucketed, so no
webhook route is wired up (see the connector's module docstring).

**Ad Spend Sync:**
```bash
curl -X POST http://localhost:8100/connectors/mercadopago/sync-ad-spend \
  -H "Authorization: Bearer {token}" \
  -d '{"store_id": "xxx", "start_date": "2026-01-01", "end_date": "2026-01-31"}'
```

**Data Fetched:**
- Campaign spend by day
- Impressions and clicks
- Stored in `ad_spend` table with `platform="mercadopago"`

### Sync Status / Health

`GET /stores/{store_id}/connectors/health` (ownership-scoped, like every
other `/stores/{id}/...` route) returns per-provider status from the
`connector_status` table:

```json
{
  "shopify": {"last_synced_at": "...", "last_success_at": "...", "last_error": null},
  "meta": {"last_synced_at": "...", "last_success_at": "...", "last_error": "..."}
}
```

Every OAuth callback, ad-spend sync, and webhook call updates this — see
`_upsert_connector_status()` in `app/routes/connectors.py`. Related audit
tables exist for lower-level detail: `shopify_webhooks_log` /
`tiendanube_webhooks_log` (every webhook received, valid or not) and
`token_refresh_audit` (every Google/MercadoPago token-refresh attempt) — see
`db/init/006_*.sql` through `009_*.sql`.

`store_credentials.provider_account_id` (added in `db/init/009_*.sql`) holds
a provider-side account/store identifier captured at OAuth time — Tiendanube's
own store id, MercadoPago's collector id — for connectors whose API calls
need more than just the access token. Shopify/Meta/Google leave it null.

### Adding a New Connector

Every write route above (`auth-url`, `callback`, `sync-ad-spend`) is
ownership-scoped via the `get_owned_store` dependency, exactly like every
other `/stores/{id}/...` route — a new connector's routes should do the same,
not re-check `store.account_id` manually.

Checklist (also enforced via `.github/pull_request_template.md`):

- [ ] Create `app/connectors/newconnector.py` implementing every
      `BaseConnector` abstract method:
      ```python
      from app.connectors import BaseConnector

      class NewConnector(BaseConnector):
          def get_oauth_url(self, state: str) -> str: ...
          def exchange_auth_code(self, code: str, redirect_uri: str) -> OAuthToken: ...
          # ... implement other methods
      ```
      (Instantiating a subclass missing a method raises `TypeError` —
      `tests/test_connectors_interface.py` catches this in CI.)
- [ ] Add a `BREAKING_CHANGES` list next to `API_VERSION`, like the other
      three connectors, so future API-version bumps have somewhere to log
      what changed.
- [ ] Add settings to **both** `.env.example` (root) and `backend/.env.example`
- [ ] Add OAuth endpoints in `app/routes/connectors.py`:
      ```python
      @router.post("/newconnector/auth-url")
      def get_newconnector_auth_url(...): ...

      @router.post("/newconnector/callback")
      def newconnector_oauth_callback(...): ...
      ```
- [ ] Encrypt tokens with `encrypt_secret` before storing in `store_credentials`
- [ ] Call `_upsert_connector_status(...)` on every OAuth callback and sync
      attempt (success and failure) so `GET /stores/{id}/connectors/health`
      reflects it — see the existing connectors for the pattern
- [ ] If ad-spend data: match the exact field set in
      `tests/test_connectors_schema.py::AD_SPEND_REQUIRED_FIELDS` — Meta and
      Google must stay identical since they feed the same table
- [ ] Write tests in `tests/test_connectors_{provider}.py`
- [ ] Update this doc's "Implemented Connectors" section

## Security Notes

See `SECURITY.md` (repo root) for the credential rotation schedule.

### Structured Logging

`app/logging_config.py` configures the root logger to emit one JSON object
per line (timestamp, level, logger, message, plus anything passed via
`extra={...}`), so logs are directly ingestible by log aggregators with no
regex parsing. `main.py`'s `log_requests` middleware logs every request
(method, path, status, duration, client IP, a generated `X-Request-ID`), and
the Shopify webhook route logs on rejection/success/failure. Set the level
with `LOG_LEVEL` (defaults to `INFO`).

### Refresh Tokens

`POST /auth/register`, `POST /auth/login`, and `POST /accounts/invites/accept`
all return `{access_token, refresh_token}`. Access tokens are JWTs
(`ACCESS_TOKEN_EXPIRE_MINUTES`, default 15 min); refresh tokens are opaque
random values (`secrets.token_urlsafe(32)`) — only their SHA-256 hash is
persisted (`refresh_tokens.token_hash`, via `app/security.py::hash_token`,
the same helper `account_invites` uses), so a raw refresh token is only ever
visible in the response that issued it.

- `POST /auth/refresh` **rotates**: the presented token is looked up, checked
  for `revoked_at IS NULL` and not expired, then immediately revoked and
  replaced with a new access/refresh pair. Reusing an already-rotated token
  401s. There's no reuse-detection beyond that single check (e.g. no
  "revoke the whole token family if a rotated-out token is replayed") — a
  reasonable next hardening step if this ever needs to detect token theft,
  not something the current scale needs.
- `POST /auth/logout` revokes one refresh token (its owner must match the
  caller's `current_user.id`, else 404 — same no-leak pattern as
  `get_owned_store`). There's no "logout everywhere" endpoint yet, but
  `routes/accounts.py::remove_member` and `routes/auth.py::reset_password`
  both bulk-revoke every active refresh token for the affected user (member
  removal, and a successful password reset, respectively) before returning.
- Role changes and member removal don't need any refresh-token-specific
  handling to take effect immediately: `require_role()` already re-reads
  `role` from the DB on every request rather than trusting a JWT claim, so a
  refresh token only ever mints a *new* access token honoring whatever the
  role is *right now*.

### Email

`app/email.py::send_email()` sends plain-text mail over SMTP
(`smtplib`/`email.message`, stdlib only — no new dependency). Configured via
`SMTP_HOST`/`PORT`/`USERNAME`/`PASSWORD`/`FROM_EMAIL`/`USE_TLS` — works with
Gmail, SendGrid/Mailgun/SES's SMTP endpoints, or any other SMTP server. With
`SMTP_HOST` unset (the default, and always true in tests/CI) it logs the
email at `INFO` instead of connecting anywhere, so nothing here needs real
credentials for local dev. Two callers today:

- `routes/accounts.py::create_invite` (and `resend_invite`, sharing the
  `_send_invite_email` helper) emails the invitee an accept link
  (`{FRONTEND_URL}/index.html?invite_token=...`) and the raw token, and
  still returns the raw token in the API response too as a fallback. Resend
  mints a fresh token and a fresh `INVITE_EXPIRY_DAYS` expiry on the same
  `AccountInvite` row (not a new row) — the old token stops working the
  moment a resend happens, since only the row's current `token_hash` is
  ever looked up. Only a `status == "pending"` invite can be resent; an
  expired-but-unaccepted invite is still `pending` (nothing transitions it
  to an "expired" status), so it's still resendable. The frontend's auth
  view reads `invite_token` off the URL on load and shows a dedicated
  "accept invite" form (password + confirm) that posts to
  `/accounts/invites/accept`; the Equipo screen's pending-invites list has a
  "Reenviar" action next to "Revocar".
- `routes/auth.py::forgot_password` emails a reset link
  (`{FRONTEND_URL}/index.html?reset_token=...`) and the raw token — but,
  unlike invites, never echoes the token in the API response (the endpoint's
  response is identical whether or not the email is registered, to avoid
  leaking account existence). The frontend reads `reset_token` off the URL
  the same way and shows a "new password" form that posts to
  `/auth/reset-password`.

Both send calls are wrapped the same way — a failure is caught and logged,
never raised, since email delivery is best-effort and (for invites) the
token in the response is still a usable fallback.

**Running this for real under `docker compose`:** `docker-compose.yml`'s
`backend` service only injects the env vars it explicitly lists in its
`environment:` block — it does not pass through arbitrary host env vars, so
setting `SMTP_HOST` etc. in `backend/.env` does nothing for the
containerized backend (that file isn't even mounted into the container).
`FRONTEND_URL` and `SMTP_*` need to be forwarded there explicitly (as
`JWT_SECRET` and `CREDENTIALS_ENCRYPTION_KEY` already were) and set in a
**root-level** `.env`, which is what Compose itself reads for `${VAR}`
substitution in `docker-compose.yml`. After changing them, `docker compose
up -d backend` (not just `restart`) is needed to recreate the container
with the new environment.

### Rate Limiting

`app/rate_limit.py` defines a shared `slowapi` `Limiter` keyed by client IP,
with a `200/minute` default applied to every route via `SlowAPIMiddleware`.
Sensitive endpoints override it tighter: `/auth/register` (5/minute),
`/auth/login` (10/minute), the Shopify webhook receiver (120/minute — higher
because legitimate traffic can burst there). Exceeding a limit returns `429`.

### Environment Variable Validation

Two layers:
- `python scripts/check_env.py` — compares the real environment (and
  `backend/.env` / `.env`) against `backend/.env.example`, the canonical
  list of every variable the app knows about. Missing `DATABASE_URL`,
  `JWT_SECRET`, or `CREDENTIALS_ENCRYPTION_KEY` fails the check (exit 1);
  missing connector credentials just warn, since the app boots fine without
  them (that connector just won't work). Wired into CI as a step.
- `Settings.validate_production_ready()` (`app/config.py`), called at
  backend startup — refuses to boot when `ENVIRONMENT=production` and
  `jwt_secret`/`credentials_encryption_key` are still equal to their
  dev-only defaults. A no-op in local dev (`ENVIRONMENT` defaults to
  `development`).

### Ownership Audit

`scripts/audit_ownership.py` checks that every `store_credentials` row still
decrypts with the current `CREDENTIALS_ENCRYPTION_KEY`, and that no
`store_credentials`/`orders`/`pixel_events`/`ad_spend` row references a
store that no longer exists (the time-series hypertables have no FK
constraint enforcing this at the database level). Run it periodically:

```bash
cd backend && python ../scripts/audit_ownership.py
```

Exits non-zero on any finding, so it can be wired into a cron/CI job later.

### Credential Encryption

All OAuth tokens are encrypted at rest using Fernet (symmetric encryption):

```python
# In app/security.py
encrypted_token = encrypt_secret(raw_token)
decrypted_token = decrypt_secret(encrypted_token)
```

- Encryption key: `CREDENTIALS_ENCRYPTION_KEY` from `.env`
- Generate new key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`

### Webhook Signature Validation

- **Shopify**: HMAC-SHA256 of body, header `X-Shopify-Hmac-SHA256`
- **Meta**: SHA1 HMAC, header `X-Hub-Signature`
- **Google**: Does not use webhooks (pull-based only)

### Ownership Isolation

All connector endpoints use `get_owned_store()` dependency:

```python
store = db.get(Store, store_id)
if not store or store.account_id != current_user.account_id:
    raise HTTPException(status_code=404, detail="Store not found")
```

This prevents users from accessing stores in other accounts. Error messages don't reveal store existence (no data leak).

## Database Schema

### Key Tables

- **store_credentials** - Encrypted OAuth tokens
  - `id` (UUID): Primary key
  - `store_id` (UUID): Foreign key to stores
  - `provider` (string): "shopify", "meta", "google", etc.
  - `access_token` (string): Encrypted token
  - `refresh_token` (string): Encrypted (if applicable)
  - `expires_at` (datetime): Token expiration (if applicable)

- **orders** (hypertable) - Order data from Shopify
  - `time` (datetime): Order timestamp
  - `store_id` (UUID): Which store
  - `order_id` (string): Shopify order ID
  - `gross_amount`, `net_profit`, etc.

- **ad_spend** (hypertable) - Ad spend from Meta/Google
  - `time` (datetime): Date of spend
  - `store_id` (UUID): Which store
  - `platform` (string): "meta" or "google"
  - `campaign_id`, `campaign_name`, `spend`, `impressions`, `clicks`

## Continuous Aggregates (Metrics)

SQL views in `db/init/004_continuous_aggregates.sql` provide:

- `daily_financial_summary` - Refreshed hourly with orders + COGS data
- `metrics/summary` endpoint joins this with real ad spend:
  ```
  true_roas = net_profit / ad_spend  # net of discounts, shipping, gateway fees, COGS — not plain revenue/spend
  real_profit_after_ads = net_profit - ad_spend
  ```

## Troubleshooting

### Test Database Connection Issues

1. Verify test-db is running:
   ```bash
   docker ps | grep test-db
   ```

2. Test connection:
   ```bash
   psql -h localhost -p 5433 -U test -d escal_test
   ```

3. Recreate test database:
   ```bash
   docker-compose down test-db
   docker-compose up test-db -d
   ```

### Webhook Signature Validation Failures

1. Verify API secret matches in `.env`
2. Ensure webhook body isn't modified before validation
3. Check webhook headers are passed correctly

### Token Expiration Issues

- **Shopify**: Access tokens don't expire
- **Meta**: Access tokens don't expire (for business accounts)
- **Google**: Access tokens expire in 1 hour, refresh tokens in 6 months

The `sync_google_ad_spend` endpoint automatically refreshes expired tokens.

## Performance Notes

- **Continuous aggregates** refresh every hour (configurable)
- **Webhook processing** is synchronous (queue if needed for scale)
- **Historical syncs** paginate API results (rate limiting varies by provider)

## Next Steps

1. ✅ Test infrastructure (pytest + test database)
2. ✅ Shopify OAuth + order webhook
3. ✅ Meta Ads connector
4. ✅ Google Ads connector
5. ✅ CI (GitHub Actions), pre-commit hook, PR template
6. ✅ Connector sync-status tracking + `/connectors/health` + ownership audit script
7. ✅ Secrets rotation policy (`SECURITY.md`)
8. ✅ First frontend (`frontend/` — plain HTML/CSS/JS, no build step)
9. ✅ Structured (JSON) logging, API rate limiting, env-var validation, Shopify webhook e2e tests
10. ✅ Tiendanube connector (webhook-based, same pattern as Shopify)
11. ✅ MercadoPago connector (pull-based, same pattern as Meta/Google)
12. ✅ Multi-user accounts / Owner-Admin-Viewer role-based permissions
13. ✅ Real frontend design pass (ROSS brand system, light/dark mode,
    Spanish localization, bento dashboard, team management screen)
14. ✅ Invite-link landing flow + forgot/reset-password flow on the frontend
15. ✅ Hover tooltips on the daily revenue-vs-spend chart
16. ✅ "Resend invite" action (fresh token + expiry, Equipo screen)
17. ✅ Configurable summary board (per-user widget add/remove/reorder/hero)
18. ✅ Creative-level (ad-level) performance for Meta/Google, ranked by spend
19. ✅ Customer identity foundation (hash-only, deduplicated `customers`
    table linked from every order-ingestion path)
20. ✅ LTV by cohort + blended CAC payback (`/metrics/ltv-cohorts` +
    dashboard cohort-grid widget)
21. ✅ CAPI feedback loop (Meta Conversions API + Google Enhanced
    Conversions for Leads, via BackgroundTasks; Google side unverified
    against a real Ads account — see README "CAPI feedback loop")
22. ✅ Connect flow for Shopify/Meta/Google ("Conectar" button + OAuth
    round trip) + fixed the ad_account_id/customer_id persistence gap
    (`StoreCredential.provider_account_id`) — see README "Connect flow";
    needs real registered apps to complete a live consent screen
23. ⏳ Password strength meter
24. ⏳ Per-channel CAC, product journeys (now unblocked by #19/#20)
25. ⏳ Creative-level revenue/ROAS attribution (needs ad_id/creative_id on
    `orders`, not just utm_source/utm_campaign)
26. ⏳ Creative thumbnails (needs a per-creative API call on both platforms)
