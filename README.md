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

Like CAC by channel, this only counts orders that resolved to a
`customers` row (see "Customer identity") — a guest checkout with no email
or phone never gets a `customer_id`, so it's invisible to this endpoint
even though it's counted in `/metrics/summary`'s revenue. For a store with
real guest-checkout volume, this table's total revenue will be lower than
the dashboard's headline revenue for the same range — that gap is exactly
the guest-order total, not a bug.

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

### PWA (mobile install + offline shell)

`frontend/` is now an installable Progressive Web App — "Agregar a
pantalla de inicio" on iOS/Android puts ARAMAL on the home screen with its
own icon, no app-store listing required. `frontend/manifest.json` declares
name/colors/icons (`frontend/icons/`, generated at brand primary `#2263A2`
— `icon-192.png`/`icon-512.png` for `purpose: any`, plus a padded
`icon-maskable-512.png` for Android's adaptive-icon safe zone) and is
linked from both `index.html` and `landing.html`, alongside the
`apple-touch-icon`/`apple-mobile-web-app-*` tags Safari needs since it
ignores the manifest for its own icon.

`frontend/sw.js` (registered from the bottom of `app.js`) gives the app
shell offline resilience: navigations are network-first (so an online user
always gets the latest deploy) falling back to cache when offline; other
static assets (`style.css`, `app.js`, icons) are stale-while-revalidate.
It only ever intercepts same-origin `GET`s — API calls go to a different
origin/port (`API_BASE` in `app.js`) and are never touched, so live data
is never served stale from the service-worker cache. Verified with
Playwright against the static frontend: manifest parses and is
installable, the service worker registers with no console errors, all
icons resolve, and a reload with the network fully cut still renders the
full app shell from cache.

### Push notifications

Opt-in per device (not per store/account — a user can have several
subscribed devices, e.g. phone + laptop). Proactive CAC/ROAS alerts and
weekly reports (see "Proactive alerts", "Weekly reports") now fan out to
push as well as email: `app/services/notifications.py::send_to_store` calls
both `send_email` and the new `send_push_to_user` for every account owner,
independently — one failing doesn't block the other.

Same colocated-settings/no-op-when-unconfigured pattern as SMTP
(`app/email.py`): `VAPID_PRIVATE_KEY`/`VAPID_PUBLIC_KEY` unset means
`send_push_to_user` just logs instead of sending, and `GET
/push/vapid-public-key` returns an empty string, so the frontend hides the
notification toggle entirely rather than offering a subscribe flow that
could never send. Generate a real keypair with
`python scripts/generate_vapid_keys.py` and paste the output into the root
`.env`.

The frontend's bell icon (topbar, next to the theme toggle — only shown
once logged in and once the backend confirms push is configured) drives
the whole flow: click subscribes (`Notification.requestPermission()` then
`pushManager.subscribe()`, posted to `POST /push/subscribe`) or
unsubscribes (`DELETE /push/subscribe?endpoint=...`) a device. `sw.js`
handles the `push` event (shows a notification from the JSON payload) and
`notificationclick` (focuses an existing tab or opens one). Backend storage
is a plain `push_subscriptions` table (`user_id`, `endpoint`, the two Web
Push keys), upserted on `(user_id, endpoint)` so a browser that rotates its
subscription doesn't accumulate dead rows; a 404/410 from the push service
on send (subscription revoked/expired) prunes the row automatically.

Verified for real, not just mocked, by `e2e/push-notifications.js`
(`npm install && npm run test:push` from `e2e/`, see that file's own doc
comment for full prerequisites — not wired into CI, since it needs a real
Chrome binary, real outbound internet to Google's FCM, and real VAPID
secrets in the runner): a live Chrome instance (Playwright's own bundled
Chromium has no Google API key, so `pushManager.subscribe()` fails
outright with "push service not available" — this needed `channel:
"chrome"` against a real, separately-installed browser) registers a fresh
account, clicks the real bell icon, creates a store, and clicks the
existing "Enviar ahora" button — and the backend's `pywebpush` call
actually signs and posts to Google's live FCM endpoint (`201` back from
Google), with the real weekly-summary notification arriving and rendering
in that browser's service worker end to end.

That test's retry loop around the bell-icon click isn't defensive
boilerplate — it's the fix for a real finding from building it: clicking
the icon via Playwright's synthetic click sometimes leaves
`pushManager.subscribe()` hanging forever (never resolves or rejects). A
`setTimeout`-triggered subscribe with no user gesture at all hangs every
time; a real dispatched click succeeds most but not all of the time (the
click handler in `app.js` was also trimmed to the minimum awaits before
calling `subscribe()` as part of chasing this down — cached
`ServiceWorkerRegistration`/subscription state instead of re-awaiting them
on every click — which measurably helped but didn't eliminate the
raciness). Reads as a Chrome/CDP race around synthetic-click user
activation for this specific API, not an app bug: a real person clicking
with a real mouse isn't expected to hit it. The test retries the click (up
to 6 times, it has never needed more than 3 across repeated local runs)
rather than asserting the first click always works.

**Mobile UX pass:** verified end-to-end with Playwright (mobile viewport,
against the live docker-composed stack, seeded demo data, real login) —
this caught a real bug (see below), not just a static CSS review.

- The dashboard sidebar (`.sidebar`, previously a fixed 220px column with
  no media query at all) is now an off-canvas drawer below 860px: a
  hamburger button (`#sidebar-toggle`, topbar) slides it in over a dimmed
  backdrop (`#sidebar-backdrop`); it closes on backdrop tap or on
  navigating (`setSidebarOpen(false)` in `switchDashboardView`, `app.js`).
- Found and fixed: the topbar itself broke below ~430px — brand, theme
  toggle, account name, and "Cerrar sesión" all fought for one row and
  the account name/button text wrapped mid-word into a garbled two-line
  mess. Now wraps cleanly to a second row, and `#account-name` truncates
  with an ellipsis instead of wrapping.
- `.store-panel-actions` (6 buttons + a range select) now wraps instead of
  overflowing off-screen.
- Fixed-width `.auth-card`/`.modal-card` now cap at `calc(100vw - 32px)`;
  `.modal-card` also caps its height and scrolls internally so a tall
  modal (e.g. "Miembros de la tienda") can't get clipped off a short
  mobile viewport.
- Form inputs bump to 16px on mobile (`.auth-form`, `.modal-card`,
  `.store-panel-actions select`) — below that, iOS Safari auto-zooms the
  whole page on focus, which is jarring and easy to miss without testing
  on an actual small viewport.
- `.widget-ctrl`/`.theme-toggle` and the drawer's nav rows got slightly
  bigger tap targets on mobile; desktop sizing is untouched.
- Tables (creative performance, LTV cohorts, CAC-by-channel, attribution,
  audit log) were already `overflow-x: auto`-wrapped — left as-is, that's
  an acceptable mobile pattern and nothing broke there.
- Not covered: this pass was the dashboard shell and its modals: no
  device-farm/cross-browser sweep (only Chromium/iPhone-12-sized
  viewport), and no tablet-specific pass between 860px and desktop.

### P&L completo (full profit-and-loss breakdown)

`orders` already sums `discounts`/`shipping_fee`/`payment_gateway_fee`/
`cogs_total` into its `net_profit` generated column (see
`db/init/003_hypertables.sql`) — `GET /stores/{id}/metrics/pnl` just
surfaces each line item individually instead of collapsing them into one
number the way `/metrics/summary` does, and the "P&L completo" dashboard
widget renders it as a simple line-item table (revenue down to real
profit after ads). No schema changes; this was already computable from
data every connector was already writing.

Deliberately not built: margin by SKU. Unlike the P&L above, `orders` has
no per-line-item detail — the Shopify connector already iterates
`line_items` to compute `cogs_total` (`app/connectors/shopify.py`) but
discards the per-product breakdown once it's summed. A real per-SKU
margin ranking would need a new `order_items` table (product_id,
quantity, unit cogs) and matching changes to both the Shopify and
Tiendanube connectors — feasible (no new external API calls, just
persisting data the connectors already touch), but a real feature, not a
quick win.

### Perfil (notification channels, devices, quick alerts)

A new sidebar view, personal to the logged-in user (not store- or
account-scoped), holding three things:

- **Per-channel notification preferences** — a new
  `notification_channel_preferences` table (`user_id`, `event_type` one of
  `cac_alert`/`roas_alert`/`weekly_report`, `email_enabled`,
  `push_enabled`). `app/services/notifications.py::send_to_store` checks
  this per recipient before sending each channel; no saved row means both
  channels stay on, so nobody's notifications change unless they actually
  open Perfil and flip something. This closes the gap flagged during this
  session's UX review: turning on a store's alerts never told a user they
  also needed to enable push on their own device — now that's one matrix
  in one place, covering both.
- **Dispositivos y sesiones** — `GET /push/subscriptions` lists every
  device a user has subscribed, with a friendly label
  (`app/routes/push.py::_label_from_user_agent`) derived from the
  User-Agent captured at subscribe time (`push_subscriptions.user_agent`,
  new column) — the Push API itself exposes no device name. `DELETE
  /push/subscriptions/{id}` revokes a *different* device than the one
  making the request; the existing `DELETE /push/subscribe?endpoint=`
  only ever unsubscribed the calling browser's own subscription, so
  there was previously no way to revoke a lost or old device remotely.
- **Constructor de alertas rápido** — an inline threshold editor
  directly on the True ROAS stat card (fetches/saves through the
  existing `PUT /stores/{id}/alert-preferences`, no new endpoint) so
  setting a threshold doesn't require opening the full "Alertas" modal.

Verified end-to-end against the live stack: toggling a channel off in the
matrix and reloading confirms it persisted and that `send_to_store` skips
exactly that channel for that event type (not others); a real subscribed
device shows up with its derived label and disappears on revoke; the
quick-alert save round-trips through the same preferences a "Alertas"
modal open reflects. No regressions on the mobile layout (checked at
390px: topbar, the notification matrix, and the quick-alert row all still
fit).

### Actividad reciente + Tu equipo (Perfil)

Two more Perfil panels, rounding it out:

- **Actividad reciente** — a new `account_activity_log` table logs a
  deliberately narrow set of high-signal actions (store created, invite
  sent, alerts/weekly-report turned on — see
  `app/services/activity_log.py` for the exact list), same scoping
  philosophy as `customer_data_access_log`: not a generic audit
  middleware, just the handful of call sites that matter. `GET
  /accounts/activity` is personal (filtered to the calling user), not a
  shared account-wide audit trail.
- **Tu equipo** — a one-line summary (avatars, role counts, a link into
  the full "Equipo" screen) reusing the existing `GET /accounts/members`
  — hidden for viewers since that endpoint already requires owner/admin.

Found and fixed while building this: `created_at` on the new table
defaulted to `now()`, which Postgres fixes for the whole transaction —
several activities logged within one request/session (or, as this
feature's own ordering test caught, several requests sharing one
wrapping test transaction) tied on timestamp and sorted arbitrarily
instead of newest-first. Switched to `clock_timestamp()`, which reflects
real wall-clock time per statement.

### Billing scaffolding (no real prices yet)

`Plan`/`Subscription`/`Invoice` models exist (see the "Estrategia de
Billing" proposal doc for the full design) and `require_plan_feature()`
is wired onto 8 routes (forecast, ltv-cohorts, cac-by-channel,
attribution-by-channel, creative performance, weekly reports, per-store
role overrides, audit log). Every account — the 124 that existed before
this and every new registration — is pinned to the `scale` plan, which
has every gated feature, so **nothing is actually restricted today**;
`Plan.monthly_price` and the volume limits are placeholders. Gating a
feature only ever applies to *enabling* it, never to turning it off — a
future downgrade must not strand someone unable to disable something
they already had on. This is Phase 1 only: no checkout, no Stripe
integration, no way for a real account to end up on a plan other than
Scale yet.

### Pricing page (`frontend/pricing.html`)

A public, unauthenticated marketing page — linked from the landing
page nav — that helps a visitor self-select a plan by monthly order
volume instead of reading a static feature table. A 4-stop slider
(under 500 / 500–2.000 / 2.000–5.000 / more than 5.000 orders per
month) drives which of the three plan cards is highlighted as
recommended, the explanation line above the cards, and a capacity bar
showing roughly how much of that plan's headroom the volume uses
(Scale has no bar since it has no order ceiling). All prices show as a
`[Precio]` placeholder — intentional, since real values aren't decided
yet (see "Billing scaffolding" above); the "Solicitar demo" CTA is how
someone gets on the list to hear when pricing lands. Plain HTML/CSS/JS,
no framework, mirroring `landing.html`'s conventions.

### Agents (pixel-art identity)

The frontend is getting a pixel-art identity, carried over from
TERXPERIENCE_APP: every module is an animated agent doing that module's
task, and every person appears as an agent wearing their role's uniform
(Owner / Admin / Viewer, with one of 4 skin/hair variants picked from their
user id). The sprites are generated by a script, not drawn by hand:
`tools/agentes/` (Python standard library only, see its README) exports each
drawing as JSON (per color, a compact list of rectangles) plus a generated
`frontend/agentes/paleta.css`; the browser
animates each scene with a CSS `steps()` strip, with no JavaScript per frame.
The hand-written runtime is `frontend/agentes/agentes.js` + `agentes.css`
(`window.Agents.hydrate()`, driven by `data-agent-*` attributes), and scenes
are fetched one at a time, only where they are used. What the agents *do*
beyond decorating (the login agent and the mascot, below) lives in
`frontend/agentes/mascota.js` (`window.Mascot`), which only reads the DOM that
`index.html` and `app.js` already provide.

Modules drawn so far (Phase 1 was the connectors one, as the test module):

| Module | Where it shows |
| --- | --- |
| Tiendas y conectores | Empty dashboard ("Todavía no conectaste ninguna tienda"), the technician's head beside the "Estado de conectores" widget |
| Alertas | Beside the title of the "Alertas de CAC y ROAS" modal |
| Reportes | Beside the title of the "Reporte semanal" modal |
| Dashboard | The empty state when the selected range has no orders ("Todavía no hay pedidos en este rango") |
| Perfil | At the right of the Perfil header, next to the person's own bust |
| Auditoría | Beside the title of the "Auditoría" modal |
| Equipo | Beside the title of the Equipo view; each member row and Perfil's "Tu equipo" summary show that person's role chip |

**The login welcome agent** peeks out from behind the login card and reacts to
what the person is doing:

| Situation | What it does |
| --- | --- |
| First visit in this browser (no `aramal_ya_ingreso` in `localStorage`, set by the first successful ingress) | Points at the "Crear cuenta" tab, with a message box ("Ross · ¿Primera vez? Creá tu cuenta") that opens it |
| Login tab at rest | Rests, waves, blinks |
| Password field focused | Covers its eyes |
| A failed login or registration | Shakes its head (until the person edits the form) |
| Registration tab, following the password meter (`data-level`) | Worried when very weak or weak, thumbs up when strong |
| Account created | Celebrates for 1.2 s before the dashboard shows (no wait with reduced motion) |
| Expired session ("Tu sesión expiró…") | Worried |

**The mascot is Ross**, one character (suit and tie, short hair) that is also the
login welcome agent. The name is meant to work for anyone, so any copy that
mentions it stays grammatically neutral ("Ross está trayendo tus números…"); it
is defined once, as `NAME` in `mascota.js`, and the character as `ROSS` in
`tools/agentes/scenes.py`. Ross introduces itself in the first-visit bubble
("Soy Ross.") and is the voice for states: errors that used to use the browser's `alert()` are a
toast with the mascot holding an unplugged cable (`reportError()` in `app.js`;
the "connected correctly" alert is unchanged); the chart panel shows it typing
on a laptop while the numbers take longer than 300 ms (and a message if they
fail); and, when True ROAS is the hero tile, it celebrates or worries on that
card depending on whether the result reaches the minimum the user set in
Alertas (`roas_threshold`, 1.0 by default, read from
`GET /stores/{id}/alert-preferences`), with the same thing said in words next to
it. With no result there is no mascot.

**Ross's message box** is one design in three places: the first-visit bubble of
the login agent, the error toast and the note on the True ROAS card. It is a card
in the app's own colors (`.ross-box` in `agentes.css`: 1 px border, 8 px corners,
"Ross" as a small label over the message) with a thin stripe on the left whose
color says the state at a glance: celeste for the greeting, red for an error,
green for True ROAS at or above the minimum and amber below it (a low result is a
heads-up, not an alarm). The stripe only decorates: every message also says the
same in words (and the ROAS note starts with ▲ or ▼), because in the light theme
even the darker tones (`--ross-neutral`, `--ross-ok`, `--ross-warn`, chosen to
hold 3:1 on white) are not enough to carry a meaning alone; in the dark theme they
are the app's own `--celeste`, `--positive` and `--warning`. Ross stands on the
toast's top edge, sits beside the ROAS note (a small tail points at it) and peeks
from behind the login card; on a phone the login bubble moves out and shrinks so
it stays clear of Ross's face. It was picked over a plain header card and a box
glued to the sprite.

**The first-steps card** ("Primeros pasos · 1 de 3", Ross's bust beside a
three-row list) sits at the top of the Dashboard for the owner or admin of the
active store, and follows what the app already knows: the store exists (always
done when the card shows), the store has had orders (the demo data, or real
ones) and its alerts are on (`enabled` in `GET /stores/{id}/alert-preferences`,
which the True ROAS mood already fetches: one cached read per store). It tracks
progress live (saving Alertas or loading the demo ticks the step without a
reload), goes away when everything is done, and has an "Ocultar" link
(remembered per user in `localStorage`). Viewers of the store never see it, and
with no store the empty state keeps its own "Conectar tu primera tienda" button.
Each step's button goes **straight to its action**, never by pressing another
button behind the person's back: "Cargar demo" loads on the spot (the button
and the header's "Cargar datos de demo" both show the progress) and "Configurar"
opens the alerts form with focus on its first field. `mascota.js` only draws the
card; what the steps are and what they do lives in `app.js`
(`refreshFirstSteps()`).

Every person also shows as an agent: the role's chip in the topbar and its bust
in Perfil. A scene inside a hidden modal or view is only downloaded and drawn
when it is shown. Rules that hold for every later scene: no text
is ever drawn inside a sprite (the pixel font can't render accents, `Ñ`, comma
or `$`, which broke 16 of 23 real UI phrases), sprites have no background so
one drawing serves both themes, `prefers-reduced-motion` stops every
animation, a topbar button pauses them (remembered in `localStorage`), and
the whole `frontend/agentes/` folder has a 25 KB gzip budget (4 KB per scene)
that `tools/agentes/export.py` enforces. Today that is 20.9 KB gzip in
total, runtime included (a scene is 0.3–0.7 KB; `mascota.js` is 4.6 KB).
Check a change with `pytest tools/agentes` (output is current and within
budget) and `npm run test:agents` in `e2e/` (real browser: both themes,
360 px, pause, reduced motion, every role, the login agent, the mascot's toast,
loading block and mood, the message boxes' stripe per state and theme, and the
first-steps card; not in CI).

Which role the agents wear: the topbar chip shows the role the person holds
*in the active store* while they are on the Dashboard (a per-store override
can differ from the account role, see "Per-store roles"), and their
account-wide role in Equipo and Perfil; the Perfil bust always uses the
account role. The store role comes from `effective_role`, which `GET /stores`
and `GET /stores/{id}` return for the requesting user (one query for the whole
list, see `app/dependencies.py::effective_roles_for_stores`).

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
- `POST /stores`, `GET /stores`, `GET /stores/{id}` (both `GET`s include the
  caller's `effective_role` on each store — see "Per-store roles"),
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
piece for per-channel CAC (see "CAC by channel"), now shipped. The
frontend is also now an installable PWA (see "PWA (mobile install +
offline shell)") — home-screen install on iOS/Android with an offline
app shell — and has been through a real mobile UX pass (off-canvas
sidebar drawer, a fixed topbar-overflow bug, responsive modals/inputs —
see "Mobile UX pass" below). Proactive alerts and weekly reports now also
send as **push notifications**, opt-in per device (see "Push
notifications"), on top of email, and a full **P&L breakdown** widget and
a personal **Perfil** page (unified notification channels, device
management, an inline quick-alert builder) round out the dashboard and
account settings (see "P&L completo" and "Perfil" below). A pixel-art
**agents** identity is under way (see "Agents"): Phase 1 (a single test
module) and Phase 2 (Alertas, Reportes, Equipo, Auditoría, Perfil and the
Dashboard, one at a time) are done; Phase 3 is partly done: the login welcome
agent (with its registration behavior), the mascot's error toast, loading
block and True ROAS mood, and the first-steps card for new accounts are built,
while the landing/pricing scenes are not. Not yet built:

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
- Push notifications (see "Push notifications" below) only cover the two
  events that already existed as email — proactive alerts and weekly
  reports. Per-channel opt-out now exists (see "Perfil"), but there's no
  push-only notification type — a device can only opt out of push for an
  event that also has email, not receive something push-exclusive.
- No margin-by-SKU (see "P&L completo" below) — `orders` has no
  per-line-item detail today, only the already-summed `cogs_total`. Needs
  a new `order_items` table and matching Shopify/Tiendanube connector
  changes; feasible (no new external API calls), just not built yet.
- "Actividad reciente" (see "Actividad reciente + Tu equipo" below) only
  covers 4 action types — same narrow-by-design scoping as the
  customer-data access log above, not a generic audit trail. A future
  mutating route doesn't get logged automatically; it needs its own
  explicit `log_activity()` call the same way the 4 existing ones do.
- **No billing/monetization of any kind** — there's no `Plan`/
  `Subscription`/`Invoice` model, no usage limits, no trial, no payment
  integration for charging an ARAMAL customer (MercadoPago exists only as
  a connector reading a *merchant's own* sales data). Every account
  created via `POST /auth/register` has full, permanent, unmetered
  access. This is the one gap flagged as blocking self-serve growth,
  not just a missing feature — see the separate billing proposal.

Note for `docker compose` users: `FRONTEND_URL`, `SMTP_*`, and `VAPID_*`
must be set in a root-level `.env` (not `backend/.env`) — `docker-compose.yml`'s `backend`
service only forwards env vars it explicitly lists, and a root `.env` is
what Compose itself reads for `${VAR}` substitution in that file.
