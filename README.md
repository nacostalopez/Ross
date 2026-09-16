# ARAMAL

E-commerce analytics backend (Triple Whale / Scalify style) — True ROAS, net profit,
and ad spend consolidation across stores. Standalone project, independent of any
other repo on this machine.

## Stack

- **FastAPI** (Python) — REST API, plus Shopify/Meta/Google Ads connectors
  (`backend/app/connectors/`)
- **PostgreSQL + TimescaleDB** — relational config tables + hypertables for orders,
  pixel events, and ad spend, with a continuous aggregate for fast daily rollups
- **Plain HTML/CSS/JS frontend** (`frontend/`) — no build step, calls the API
  directly; served by nginx in Docker Compose
- **Docker Compose** — one command to run the whole stack locally

Schema lives in [`db/init/`](db/init) and matches the design: `accounts`, `users`,
`stores`, `store_credentials`, `products` as regular tables; `orders`, `pixel_events`,
`ad_spend` as hypertables; `daily_financial_summary` as a continuous aggregate.

## Quickstart

```bash
docker compose up --build
```

This starts Postgres/Timescale on `localhost:5432`, the API on
`http://localhost:8000` (docs at `http://localhost:8000/docs`), and the
frontend on `http://localhost:3000`. The schema is created automatically
from `db/init/*.sql` on first boot.

Open `http://localhost:3000`, register an account, create a store, and click
"Seed demo data" to see a real True ROAS number without touching the
terminal — or do the same thing from the command line:

```bash
pip install requests
python scripts/seed_demo.py
```

## Auth

Every endpoint except `POST /auth/register`, `POST /auth/login`, and
`POST /accounts/invites/accept` requires a JWT bearer token. Accounts support
multiple users, each with one of three roles:

- **owner** — full access, plus inviting/removing members and changing roles
- **admin** — can read and write stores/products/orders/connectors, but can't manage members
- **viewer** — read-only

Registering creates a brand-new account with the registering user as its
`owner`. Additional members join via an invite, never via `/auth/register`
again (an email can only belong to one account):

```bash
curl -X POST localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"account_name": "My Store", "email": "me@example.com", "password": "at-least-8-chars"}'
# => {"access_token": "...", "refresh_token": "...", "token_type": "bearer"}

curl localhost:8000/stores -H "Authorization: Bearer <access_token>"

# Owner invites a teammate — this also emails them an accept link (see
# "Email" below; with no SMTP configured it just logs instead of sending):
curl -X POST localhost:8000/accounts/invites \
  -H "Authorization: Bearer <owner_access_token>" \
  -H "Content-Type: application/json" \
  -d '{"email": "teammate@example.com", "role": "viewer"}'
# => {"id": "...", "token": "...", ...}  — token is only ever shown here (fallback if email delivery fails)

# Invitee accepts (no auth required — they have no account yet):
curl -X POST localhost:8000/accounts/invites/accept \
  -H "Content-Type: application/json" \
  -d '{"token": "<token from above>", "password": "at-least-8-chars"}'
# => {"access_token": "...", "refresh_token": "...", "token_type": "bearer"}
```

### Password reset

```bash
# Always returns the same generic message, whether or not the email is
# registered, so the response can't be used to enumerate accounts. With no
# SMTP configured, the reset link/token is logged instead of emailed.
curl -X POST localhost:8000/auth/forgot-password \
  -H "Content-Type: application/json" \
  -d '{"email": "me@example.com"}'
# => {"message": "If that email is registered, we've sent a password reset link."}

curl -X POST localhost:8000/auth/reset-password \
  -H "Content-Type: application/json" \
  -d '{"token": "<token from the email>", "password": "at-least-8-chars"}'
# => {"access_token": "...", "refresh_token": "...", "token_type": "bearer"}
# The token is single-use and expires after 60 minutes; on success every
# existing refresh token for the account is revoked (other devices stay
# logged in only until their access token naturally expires) and the caller
# is logged back in with a fresh token pair.
```

Role is read fresh from the database on every request (not baked into the
JWT), so a role change or member removal takes effect on the very next
request rather than waiting out the access token's lifetime.

### Refresh tokens

Access tokens are short-lived (`ACCESS_TOKEN_EXPIRE_MINUTES`, default 15
min); a separate opaque refresh token (`REFRESH_TOKEN_EXPIRE_DAYS`, default
30 days) is issued alongside it by every endpoint above and persisted
(hashed, like invite tokens) in `refresh_tokens`:

```bash
curl -X POST localhost:8000/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh_token": "<refresh_token>"}'
# => new {"access_token": "...", "refresh_token": "..."} — the old refresh
#    token is revoked in the same request (rotation), so reusing it 401s

curl -X POST localhost:8000/auth/logout \
  -H "Authorization: Bearer <access_token>" -H "Content-Type: application/json" \
  -d '{"refresh_token": "<refresh_token>"}'
# revokes just that one refresh token (this device/session)
```

The frontend (`frontend/`) stores both tokens and transparently refreshes:
any `401` from the API triggers one `/auth/refresh` call (concurrent 401s
share a single in-flight refresh so the token isn't rotated twice), then
retries the original request — so a session survives past the 15-minute
access token lifetime without re-prompting for login, until the refresh
token itself expires or is revoked.

Every `/stores/{id}/...` route checks that the store belongs to the caller's
account (404, not 403, on mismatch — no confirming another account's store
exists). `store_credentials.access_token`/`refresh_token` are encrypted at
rest with Fernet (`CREDENTIALS_ENCRYPTION_KEY`) before they hit the database.

Set `JWT_SECRET` and `CREDENTIALS_ENCRYPTION_KEY` in `.env` for anything
beyond local dev — see `.env.example`.

### Dashboard layout

The summary board's widgets — which ones show, their order, and which stat
is the 2x2 hero tile — are a per-user preference (`dashboard_layouts`, one
row per user), not per-account or per-store: each teammate arranges their
own view of the same underlying data. A user with no saved layout gets a
sensible default (every widget, True ROAS as hero) rather than an empty
board:

```bash
curl localhost:8000/dashboard/layout -H "Authorization: Bearer <access_token>"
# => {"widgets": [{"type": "stat_roas", "hero": true}, {"type": "stat_revenue", "hero": false}, ...]}

curl -X PUT localhost:8000/dashboard/layout \
  -H "Authorization: Bearer <access_token>" -H "Content-Type: application/json" \
  -d '{"widgets": [{"type": "stat_roas", "hero": true}, {"type": "chart_daily", "hero": false}]}'
# replaces the whole layout — the frontend always sends the full widget
# list after any add/remove/reorder/hero change, so this is a full
# overwrite, not a patch
```

Any role (including viewer) can save their own layout — it's a display
preference, not a data-access permission. Widget types are validated
server-side (`WidgetType` in `app/schemas/dashboard.py`); an unknown type or
a duplicate type in the same layout is rejected with `422`.

### Creative analytics

`ad_spend` tracks spend at campaign/adset level; `creative_performance` is a
separate hypertable for the ad (creative) level — what a media buyer
actually scans to decide what to scale or kill. Meta and Google only
(Tiendanube/MercadoPago aren't creative-based ad platforms). `MetaConnector`
and `GoogleAdsConnector` each get a `fetch_creative_performance()` alongside
their existing `fetch_ad_spend()`, and the connector routes get a matching
`.../sync-creative-performance` next to `.../sync-ad-spend`. Thumbnail
images aren't fetched yet — both platforms need a separate per-creative API
call to get them, deliberately left for later rather than adding N+1
requests to every sync.

`GET /stores/{id}/metrics/creatives?start=&end=` aggregates
`creative_performance` by ad, sums spend/impressions/clicks over the range,
computes CTR/CPC/CPM server-side, and ranks by spend descending — this is
what the dashboard's "Performance por creativo" widget (add it via
"Personalizar" — it's not in the default layout) renders as a table.

### Customer identity

`orders` had no concept of "customer" at all — every row was anonymous, no
email/phone, no stable id linking two orders from the same buyer. That
blocks any LTV, cohort, repeat-purchase, or CAC-payback feature, since none
of those can be built without first knowing "these two orders are the same
person." `app/services/customers.py::resolve_customer_id()` is the one
place that gets solved: every order-ingestion path (bulk
`POST /stores/{id}/orders`, Shopify webhook, Tiendanube webhook) calls it
with whatever email/phone it has, and it finds-or-creates the matching
`customers` row and returns its id to store on the order.

**Hash-only, no plaintext PII at rest** — deliberately extending the same
convention `pixel_events.user_email_hash` already established, rather than
storing anything decryptable:

- Email is normalized (trim + lowercase) and phone (digits only, no leading
  zeros) the same way Meta/Google Conversions APIs expect before SHA-256
  hashing, so `customers.email_hash`/`phone_hash` would already be
  CAPI-ready if that gets built later, with no separate re-hash needed.
- None of LTV-by-cohort, CAC payback, or product journeys need to *display*
  an actual email anywhere — a stable hash fully covers dedup.
- Storing zero reversible PII means no "right to erasure" complexity for a
  product whose merchants' end-customers never consented to Escal
  specifically.

`orders.customer_id` intentionally has **no FK constraint** to
`customers.id` (same as `orders.store_id` having none either) — `Customer`
is an ORM-managed table (gets created/dropped fresh every test session),
while `orders` is a hypertable that persists across test runs untouched;
a real FK there would make `Base.metadata.drop_all()` fail at every test
teardown once a single row existed referencing it.

This ships only the identity foundation — no LTV/cohort/CAC endpoints or
UI yet; those are a natural follow-up now that this exists (see
"Status / next steps").

### LTV by cohort + CAC payback

`GET /stores/{id}/metrics/ltv-cohorts?start=&end=&months=` groups customers
by the calendar month of `first_order_at` (a "cohort") and, for each,
returns a cumulative-LTV curve (avg `net_profit` per customer, month 0 =
acquisition month through `months - 1`, default 6) alongside a **blended**
CAC (total `ad_spend` in the cohort's acquisition month / new customers
that month) and a `payback_month` — the first month-offset where cumulative
LTV crosses CAC, or `null` if it hasn't (yet, or ever, if there's no spend
data for that month). The dashboard's "LTV por cohorte y CAC payback"
widget (add it via "Personalizar") renders this as a grid.

One thing that's genuinely different from every other `/metrics/*`
endpoint here: `start`/`end` filter which **cohorts** to include (by
`first_order_at`), not which orders — each cohort's curve looks forward
from its own acquisition month regardless of `end`. A cohort acquired last
week can only ever show one populated month, and that's correct
cohort-analysis behavior, not a bug (the widget shows a one-line reminder
of this under its title).

Deliberately simplified for this first pass, same spirit as Creative
Analytics scoping out ad-level attribution:

- CAC here is blended, not per-channel — see "CAC by channel" below for the
  per-channel breakdown, which is a separate endpoint/widget rather than a
  parameter on this one (splitting *this* grid by channel would multiply
  its cohort × month-offset shape by channel too, a different enough view
  to warrant its own).
- CAC by channel is deliberately still first-touch — see "Multi-touch
  attribution (revenue by channel)" below for the purchase-sequence view
  that credits every order, not just the first.
- LTV is `net_profit` (contribution margin), not gross revenue.
- No product-journey or repeat-purchase-interval data yet, just the
  cohort/CAC-payback pair.

### CAC by channel

`GET /stores/{id}/metrics/cac-by-channel?start=&end=` splits the same
blended CAC above by acquisition channel: for each cohort month, one row
per channel (`meta`, `google`, or `other`) with that channel's own new
customers, `ad_spend`, and CAC. A customer's channel is whichever one their
*first* order's `attribution_utm_source` normalizes to (aliases like
`facebook`/`fb`/`instagram` → `meta`, `adwords`/`google ads` → `google`);
anything else lands in `other`, which correctly has no spend/CAC since
`ad_spend` only ever has `meta`/`google`/`mercadopago` rows to divide by.
Needs `db/init/019_order_attribution.sql` and reliable
`attribution_utm_source` on orders (see "Status / next steps") to be
meaningful — before that migration, everything falls into `other`. The
dashboard's "CAC por canal" widget (add it via "Personalizar") renders
this as a grid, same cohort semantics as "LTV by cohort" above (`start`/
`end` filter cohorts by `first_order_at`, not orders).

### Multi-touch attribution (revenue by channel)

`GET /stores/{id}/metrics/attribution-by-channel?start=&end=` is the
multi-touch counterpart to "CAC by channel" above: instead of crediting a
customer's *entire* history to whichever channel drove their first order,
it credits *every order* in the date range to that order's own
`attribution_utm_source` channel. One row per channel with its order count,
`repeat_orders` (orders that weren't that customer's first order ever,
regardless of whether the first one falls in this date range), revenue,
net profit, `ad_spend`, and ROAS. This is deliberately period-based (order
`time` in `start`/`end`), not cohort-based like CAC-by-channel/LTV-cohorts
— revenue happens continuously, it isn't tied to an acquisition month.
It answers a different question than CAC by channel: not "which channel
should get credit for acquiring this customer" but "how much revenue did
each channel actually drive, including customers it won back after some
other channel (or itself) made the first sale." The dashboard's "Atribución
multi-touch por canal" widget (add it via "Personalizar") renders this as
a grid.

This is "purchase-sequence" multi-touch, not weighted pre-purchase
touchpoint attribution (ad click → landing page → purchase, split with a
linear/time-decay/position-based model) — that would need `pixel_events`
(currently an orphaned table: no `click_id`, no link from `anonymous_id` to
a `customers` row) built out into a real touchpoint pipeline, which is a
much bigger project blocked on the same kind of real-traffic verification
as the Connect flow below. This endpoint only needed `orders`' existing
per-order attribution snapshot, already captured since
`db/init/019_order_attribution.sql`.

### Forecast (simple linear projection)

`GET /stores/{id}/metrics/forecast?history_days=60&forecast_days=30`
fits an ordinary-least-squares line to the trailing `history_days` of
daily revenue/net_profit/ad_spend (reading straight from `orders`/
`ad_spend`, not the `daily_financial_summary` continuous aggregate — same
freshness reasoning as `SUMMARY_SQL`) and extrapolates `forecast_days`
forward. No numpy/pandas/Prophet — just the closed-form OLS formulas in
`app/services/forecasting.py::linear_forecast`, a plain function with no
DB dependency. Revenue/ad_spend are clamped to `>= 0`; net_profit isn't,
since a trend can legitimately project a loss. `true_roas` per forecasted
day is `net_profit / ad_spend` computed from the two independently-fitted
series, not its own regression. Returns `days: []` (not an error) with
under 7 days of real history — a line fit through a handful of points
would be noise dressed up as a forecast. CAC isn't forecasted here: it's
a monthly-cohort metric with at most a few data points per channel, too
few for a trend line to mean anything. The dashboard's "Proyección a 30
días" widget shows the totals over the forecast window, not a chart per
day.

### Proactive alerts

Opt-in per store (`GET`/`PUT /stores/{id}/alert-preferences`, off by
default): email the account's owner(s) when a channel's CAC crosses a
configured threshold, or when true ROAS stays below a configured minimum
(default 1.0x) for N days in a row (default 3). Two independent checks:

- **CAC** — re-evaluates the current calendar month's "CAC by channel"
  above once per channel; fires at most once per channel per month (no
  point re-warning mid-month before the picture is final). Off entirely
  when no `cac_threshold` is set — there's no dollar default that makes
  sense across businesses.
- **ROAS** — looks at the trailing `roas_days_n` days of `/metrics/daily`;
  a day with `$0` ad spend is skipped rather than counted as good or bad
  (nothing to divide by). Fires once, then won't re-fire for
  `roas_days_n` days (a cooldown, not a fixed calendar boundary like CAC's).

There is **no in-process scheduler** — `app/services/alerts.py` is invoked
by `scripts/run_alert_checks.py`, meant to run from a real cron (or Windows
Task Scheduler, since that's this project's dev machine):

```
0 9 * * * cd /path/to/escal && python scripts/run_alert_checks.py
```

The dashboard's "Alertas" button (next to "Personalizar") opens a small
config modal with a "Probar ahora" button that runs the same check
synchronously (`POST /stores/{id}/alert-preferences/check-now`) instead of
waiting for the next cron tick — useful for tuning thresholds and for
verifying the feature works at all without real ad-account data yet.

### Weekly reports

Opt-in per store (`GET`/`PUT /stores/{id}/report-preferences`, off by
default), same no-in-process-scheduler shape as proactive alerts above but
on a weekly cadence: `scripts/send_weekly_reports.py` (a separate script/
cron entry from alerts' — different schedule, different concern) emails
the account's owner(s) a plain-text summary — revenue, net profit, ad
spend, real profit after ads, true ROAS for the trailing 7 days, plus a
CAC-by-channel highlight for the current month, omitted when there's
nothing to show. `app/services/reports.py::build_weekly_summary` reuses
`SUMMARY_SQL`/`CAC_BY_CHANNEL_SQL` from `metrics.py` rather than
recomputing anything.

`POST /stores/{id}/report-preferences/send-now` sends (and returns) that
same summary immediately, regardless of the saved `enabled` toggle — unlike
alerts' "Probar ahora", which only fires when alerts are actually turned
on, "Enviar ahora" here is deliberately a preview/test-send so a merchant
can see what the email looks like *before* deciding to enable it weekly.
The dashboard's "Reportes" button (next to "Alertas") opens this modal.

`app/services/notifications.py` holds the "look up a store's owner(s) and
email them, swallowing individual failures" logic shared by both this and
`app/services/alerts.py`.

### CAPI feedback loop

Sends confirmed purchases back to Meta Conversions API and Google Enhanced
Conversions (for Leads), so both platforms optimize ad delivery against real
revenue instead of just pixel-fired conversions — closes the loop the
customer-identity hashes (`customers.email_hash`/`phone_hash`, already
normalized the way both APIs expect) were built for.

Opt-in per store+provider — no new endpoint, just two new fields on the
existing `PUT /stores/{id}/credentials` upsert:
- `capi_enabled: bool` (default `false`)
- `capi_destination_id: str` — the Meta pixel id, or the full Google
  `conversionAction` resource name (`customers/{id}/conversionActions/{id}`)

When enabled, every new order (bulk `POST /orders`, Shopify webhook,
Tiendanube webhook) schedules a background send for both providers right
after the order commits — `app/services/capi.py` no-ops immediately for
whichever provider isn't configured, checks a new `capi_events` table first
to avoid re-sending an already-sent order (e.g. on `orders/updated`), and
skips entirely if the order has no linked customer (nothing to match on).
Sends never block the ingestion response — first use of FastAPI's
`BackgroundTasks` in this codebase.

There's still no dedicated frontend UI for the two CAPI fields themselves
(`capi_enabled`/`capi_destination_id`) — set them via `PUT /credentials`
directly. Value sent is `gross_amount` (transaction revenue, what ad
platforms mean by conversion value), not `net_profit`. No backfill of
historical orders, no automatic retry on failure — both flagged as
deliberate v1 simplifications; a failed send stays visible via
`capi_events.error` and `GET /stores/{id}/connectors/health` (as
`meta_capi`/`google_capi`) either way.

### Connect flow (Shopify, Meta, Google)

The "Estado de conectores" widget's providers used to be permanently stuck
on "No conectado" — the backend had full OAuth plumbing
(`/connectors/{provider}/auth-url`, `/connectors/{provider}/callback`,
CSRF state tokens) but nothing in the frontend ever called it. Each
disconnected provider's card now has a "Conectar" button that opens a
small modal (Shopify needs a shop domain up front; Meta/Google's ad
account id / Ads customer id are optional and can be filled in on a later
reconnect), then does the standard OAuth round trip: redirect to the
provider, provider redirects back, frontend exchanges the code for a
stored, encrypted credential.

The provider's redirect lands back on `index.html?connector={provider}`
(plain query params, no dedicated route — nginx here serves static files
with no SPA fallback) — the same mechanism already used for
`?invite_token=`/`?reset_token=`. `boot()` in `app.js` picks it up,
completes the callback call, and refreshes the connector widget.

Fixed alongside this: `ad_account_id` (Meta) and `customer_id` (Google)
were accepted as OAuth-callback params but never saved anywhere, so any
sync call after connecting would 400 with "ad_account_id required" — this
was the "pre-existing gap" this README used to flag. Both now persist to
`StoreCredential.provider_account_id` (same field Tiendanube/MercadoPago
already used) and every sync route reads it back.

**You need your own developer app with each platform for this to fully
work** — Shopify Partners, Meta for Developers (Marketing API), Google
Cloud (OAuth client) + Google Ads API Center (developer token) — see
`.env.example`'s comments for exactly what to register and which redirect
URI to use. Without that, the button/modal/redirect mechanics all work
correctly (verified via Playwright, including the graceful-failure path),
but the actual provider consent screen and token exchange can't complete.

### Per-store roles

RBAC was account-wide only (`User.role`: `owner`/`admin`/`viewer`, applies
uniformly to every store in the account) until now. `store_memberships`
adds an **additive override**: a row for `(store_id, user_id)` overrides
the effective role for that one store only — no row means the account
role still applies, so this is fully backward compatible. It doesn't
revoke access to a store (that's still governed by account membership via
`get_owned_store`); it only adjusts *which* role applies there, e.g.
promoting an account-wide viewer to admin for one store, or restricting an
account-wide admin to viewer on a sensitive one.

`app/dependencies.py::require_store_role` is the store-aware counterpart
to `require_role` — every store-scoped mutating route (products, orders,
ad-spend, connectors, alerts, reports, etc.) was switched to it. Account-
level actions (`app/routes/accounts.py` — inviting/removing members,
changing account-wide roles) deliberately stayed on plain `require_role`,
since a store override has no business affecting cuenta-wide
administration.

`GET`/`PUT /stores/{id}/members` manage the overrides — both gated to the
**account-wide owner role specifically** (not `require_store_role`), so a
user who only holds "admin" via a store override can't grant themselves
(or anyone) a stronger one on that same store. The dashboard's "Miembros"
button (add via the store panel) opens this as a per-user role dropdown
("Igual que la cuenta" clears the override).

### Customer data access log

`Customer` is hash-only by design (see "Customer identity") and no route
returns it directly — the only customer-linked value any route exposes is
the opaque `customer_id` UUID via `GET /stores/{id}/orders`. So that's
what gets logged: every call to it records who (which user) and when into
`customer_data_access_log`. `GET /stores/{id}/orders/audit-log?limit=50`
reads it back, most recent first, gated to `require_store_role("owner",
"admin")` — a viewer can't see who looked at what. Dashboard's
"Auditoría" button shows the last 50 entries as a simple table.

This is deliberately narrow in scope: it's an access log for the one
customer-linked value that exists today, not a general-purpose audit
trail. If a future feature exposes real customer data more directly, that
call site would need its own logging call the same way `list_orders` has
one now — there's no shared middleware doing this generically.

## API overview

- `POST /auth/register`, `POST /auth/login`, `GET /auth/me`
- `POST /auth/refresh` (rotates a refresh token), `POST /auth/logout` (revokes one)
- `POST /auth/forgot-password` (always a generic response), `POST /auth/reset-password`
  (single-use token, revokes existing refresh tokens, logs the caller back in)
- `GET /accounts/me`, `GET /accounts/members`
- `POST /accounts/invites`, `GET /accounts/invites`, `DELETE /accounts/invites/{id}`,
  `POST /accounts/invites/{id}/resend` (fresh token + expiry, same email),
  `POST /accounts/invites/accept` — owner-only except accept
- `PATCH /accounts/members/{id}/role`, `DELETE /accounts/members/{id}` — owner-only
- `GET /dashboard/layout`, `PUT /dashboard/layout` — per-user (any role) summary
  board customization: which widgets show, their order, and which stat is the
  2x2 hero tile
- `POST /stores`, `GET /stores`, `GET /stores/{id}`,
  `PUT /stores/{id}/credentials` (OAuth tokens per provider, encrypted at
  rest; also carries `capi_enabled`/`capi_destination_id` — see "CAPI
  feedback loop")
- `PUT /stores/{id}/products` (bulk upsert by `external_id`, carries COGS/shipping cost)
- `POST /stores/{id}/orders` (bulk ingest/upsert, accepts optional
  `customer_email`/`customer_phone` resolved server-side to `customer_id` —
  see "Customer identity"), `GET /stores/{id}/orders?start=&end=`
- `POST /stores/{id}/pixel-events` (bulk ingest), `GET /stores/{id}/pixel-events?...`
- `POST /stores/{id}/ad-spend` (bulk ingest), `GET /stores/{id}/ad-spend?...`
- `POST /stores/{id}/creative-performance` (bulk ingest), `GET /stores/{id}/creative-performance?...`
  — ad-level (Meta/Google only), separate from `ad_spend` since it's per-ad not per-campaign
- `GET /stores/{id}/metrics/summary?start=&end=` — revenue, net profit, ad spend,
  real profit after ads, **true ROAS** (`net_profit / ad_spend` — net of discounts,
  shipping, gateway fees and COGS, not plain revenue/spend; reads live from
  `orders`, always fresh)
- `GET /stores/{id}/metrics/daily?start=&end=` — daily breakdown via the
  `daily_financial_summary` continuous aggregate (fast, but can lag up to ~1h
  behind since it refreshes on an hourly policy — see `db/init/004_continuous_aggregates.sql`)
- `GET /stores/{id}/metrics/creatives?start=&end=` — one row per ad, summed
  over the range and ranked by spend, with CTR/CPC/CPM computed server-side
  (see "Creative analytics" below)
- `GET /stores/{id}/metrics/ltv-cohorts?start=&end=&months=` — cumulative
  LTV per acquisition cohort, blended CAC, and payback month (see "LTV by
  cohort + CAC payback" above)
- `GET /stores/{id}/metrics/cac-by-channel?start=&end=` — the same cohorts,
  CAC split per acquisition channel instead of blended (see "CAC by channel"
  above)
- `GET /stores/{id}/metrics/attribution-by-channel?start=&end=` — revenue/
  profit/ROAS per channel across every order in range, not just each
  customer's first (see "Multi-touch attribution (revenue by channel)"
  above)
- `GET /stores/{id}/metrics/forecast?history_days=&forecast_days=` —
  simple linear projection of revenue/profit/ad-spend/true-ROAS (see
  "Forecast (simple linear projection)" above)
- `GET/PUT /stores/{id}/alert-preferences`, `POST
  /stores/{id}/alert-preferences/check-now` — proactive CAC/ROAS alerts (see
  "Proactive alerts" below)
- `GET/PUT /stores/{id}/report-preferences`, `POST
  /stores/{id}/report-preferences/send-now` — weekly email summary (see
  "Weekly reports" below)
- `GET/POST /connectors/{shopify,meta,google,tiendanube,mercadopago}/...` —
  OAuth handshake, ad-spend sync, and (Shopify/Tiendanube) order webhook per
  provider; Meta/Google also get `.../sync-creative-performance` — see `DEVELOPMENT.md`
- `GET /stores/{id}/connectors/health` — per-provider sync status
- `GET/PUT /stores/{id}/members` — per-store role overrides (see "Per-store
  roles" above)
- `GET /stores/{id}/orders/audit-log?limit=` — who fetched order data and
  when (see "Customer data access log" above)

All requests are logged as structured JSON (see `DEVELOPMENT.md`) and rate
limited (200/min default, tighter on `/auth/*` and the Shopify webhook) —
exceeding a limit returns `429`.

## Status / next steps

Schema, ingestion, profit/ROAS math, auth/credential-encryption,
Shopify/Meta/Google/Tiendanube/MercadoPago connectors, CI, structured
logging, rate limiting, env-var validation, webhook e2e tests, multi-user
accounts with Owner/Admin/Viewer roles, revocable refresh tokens, invite and
password-reset emails (via SMTP, configurable through env vars), transparent
frontend token refresh, hash-only customer identity resolution (every
order-ingestion path links to a deduplicated, PII-free `customers` row —
see "Customer identity"), LTV-by-cohort + blended CAC payback (see
"LTV by cohort + CAC payback"), CAC split by acquisition channel (see
"CAC by channel"), multi-touch/purchase-sequence revenue attribution by
channel (see "Multi-touch attribution (revenue by channel)"), a simple
30-day linear forecast (see "Forecast (simple linear projection)"),
proactive CAC/ROAS email alerts (see "Proactive alerts"), a weekly email
summary report (see "Weekly reports"), per-store
role overrides (see "Per-store roles"), a customer-data access log (see
"Customer data access log"), the Meta/Google CAPI feedback loop (see
"CAPI feedback loop"), and a working Connect flow for Shopify/Meta/Google
(see "Connect flow (Shopify, Meta, Google)") are done.

The frontend (`frontend/`, plain HTML/CSS/JS, no build step) has been carried
well past "just enough to see real numbers": ARAMAL brand system with light/
dark mode, a Spanish (`vos`-register, es-AR-formatted) UI throughout, a
user-configurable summary board (add/remove/reorder widgets, pick which stat
is the 2x2 hero, plus opt-in creative-analytics, LTV-by-cohort,
CAC-by-channel, multi-touch attribution, and 30-day forecast widgets — see
"Dashboard layout", "Creative analytics", "LTV by cohort + CAC payback",
"CAC by channel", "Multi-touch attribution (revenue by channel)", and
"Forecast (simple linear projection)" below) with real
period-over-period deltas, hover tooltips on the daily revenue-vs-spend
chart, a full Equipo (team) screen for the invite/role/remove routes above
(including a "Reenviar" action for a pending invite), an invite-link landing
flow (`index.html?invite_token=...`), and a forgot/reset-password flow
(`index.html?reset_token=...`), plus an inline password-strength meter on
the register/reset/accept-invite forms. `orders` now also captures
`utm_medium`, `utm_content`, a normalized `click_id` (`fb:<fbclid>` /
`g:<gclid>`), and `landing_url` at ingestion time (`db/init/019_*.sql`,
both the Shopify and Tiendanube connectors) — this was also the missing
piece for per-channel CAC (see "CAC by channel"), now shipped. Not yet
built:

- No revenue/ROAS attribution down to the individual ad — creative
  analytics currently shows each platform's own metrics (spend, CTR, CPC,
  CPM), not net_profit or true ROAS per creative. `orders` carries UTM/
  click-id/landing-url data now, but nothing yet joins it to
  `creative_performance.ad_id`. Investigated and deliberately not built
  yet: reading `ad_id` back from the CAPI upload response (the original
  idea) doesn't hold up — neither Meta's Conversions API nor Google's
  `uploadClickConversions` return per-conversion ad attribution in their
  response; both compute it internally and only expose it through their
  own reporting (Ads Manager / GAQL). The real path is the one tools like
  Triple Whale use: Meta/Google both support dynamic URL parameters on an
  ad's destination URL (`{{ad.id}}`, ValueTrack), which would already land
  in `landing_url` if a merchant's campaigns are configured to send them —
  parsing `ad_id`/`adset_id` out of it needs no new API calls, but the
  merchant's own ad setup is outside this codebase's control, so it can't
  be verified in this dev environment (no real ad accounts connected yet).
- No thumbnail images in the creative-performance table (see "Creative
  analytics" below for why).
- Multi-touch attribution is now built at the purchase-sequence level (see
  "Multi-touch attribution (revenue by channel)" above) — every order's own
  channel gets credit, not just each customer's first. What's still not
  built is *weighted pre-purchase* touchpoint attribution (ad click →
  landing page → purchase, split with a linear/time-decay/position-based
  model): `pixel_events` is currently an orphaned table (no `click_id`, no
  code linking `anonymous_id` to a `customers` row), so there's no
  touchpoint sequence to credit yet — a bigger project blocked on the same
  kind of real-traffic verification as the Connect flow below. Also no
  product-journey/repeat-purchase-interval endpoints/UI yet.
- The Google side of the CAPI feedback loop (`GoogleAdsConnector.send_purchase_conversion`)
  is built against Google's documented Enhanced Conversions for Leads
  request shape but has never been exercised against a real Google Ads
  account (no test credentials available) — the Meta side has been verified
  end-to-end against the real Graph API (with an intentionally invalid
  pixel id, confirming the failure path). `send_purchase_conversion` can
  now attach an EEA consent-mode block (`consent.adUserData`/
  `adPersonalization`), but only when a caller passes both values
  explicitly — there is no consent-management source (banner/CMP) in the
  product yet, so `app/services/capi.py` doesn't pass any today and the
  block is simply omitted rather than sending a fabricated default.
- The Shopify/Meta/Google "Conectar" flow (see "Connect flow" above) has
  never completed a real provider consent screen — this dev environment
  has no registered app with any of the three yet, so `SHOPIFY_API_KEY`
  etc. are all still placeholders. The mechanics (button, modal, redirect,
  state-token validation, graceful failure, URL cleanup) are verified via
  Playwright; the actual OAuth handshake needs real credentials to try.
- Per-store roles (see "Per-store roles" above) only *override* the
  effective role for a store — there's no way to fully revoke an account
  member's access to one specific store while keeping them in the account
  (that'd need a "none" sentinel role, not built).
- The customer-data access log only covers `GET /orders` — the one route
  that exposes anything customer-linked today. It isn't a generic
  audit-logging middleware; a future route that exposes real customer data
  would need its own explicit logging call.

Note for `docker compose` users: `FRONTEND_URL` and `SMTP_*` must be set in a
root-level `.env` (not `backend/.env`) — `docker-compose.yml`'s `backend`
service only forwards env vars it explicitly lists, and a root `.env` is
what Compose itself reads for `${VAR}` substitution in that file.
