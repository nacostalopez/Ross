# Deploying ROSS on ross.ar

The site (landing, pricing and the app) is served at `https://ross.ar` and
`https://www.ross.ar`, and the API at `https://api.ross.ar`: the frontend
derives the API address from its own host (`API_BASE` in `frontend/app.js`,
`landing.html`, `pricing.html`). It runs on Railway as three services built
from this repo, with DNS on Cloudflare.

## 1. DNS: delegate ross.ar to Cloudflare

NIC Argentina registers `.ar` domains but doesn't host their DNS records.

1. In Cloudflare (free plan), add the site `ross.ar`. It shows two
   nameservers.
2. In NIC Argentina (nic.ar, "Mis dominios" → `ross.ar` → "Delegar"), replace
   the delegation with those two nameservers. It takes from minutes to a few
   hours; Cloudflare emails when the site is active.

## 2. Railway: one project, three services

Create a project from the GitHub repo, then one service per folder
(Settings → Source → Root Directory). Each builds from that folder's
`Dockerfile`.

| Service | Root directory | Settings |
|---|---|---|
| `db` | `db` | A volume mounted at `/var/lib/postgresql/data`. Variables `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`. No public domain: the backend reaches it over the private network. |
| `backend` | `backend` | The backend variables in `.env.production.example` (`ANTHROPIC_API_KEY` turns on Claude's version of "Ross explica tu semana"). Public domain `api.ross.ar`. Health check path `/health`. |
| `frontend` | `frontend` | Target port 80. Public domains `ross.ar` and `www.ross.ar`. |

On the first start the database runs every `db/init/*.sql` script. After
that, apply new scripts by hand, in order, before deploying code that needs
them (see DEVELOPMENT.md):
`psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/init/0NN_name.sql`.

The scheduled jobs run in one Railway cron service, `cron`. It builds from
`backend` like the backend service, takes the backend's variables as
references (`${{backend.NAME}}`, with the backend service's name), and has a
start command and a schedule instead of a domain. Railway runs cron
schedules in UTC; Argentina is UTC-3.

- Schedule: `0 12 * * *` (09:00 in Argentina).
- Start command: the daily alert check, plus the weekly report on Mondays:
  `sh -c 'python -m app.cli.run_alert_checks; if [ "$(date -u +%u)" = 1 ]; then python -m app.cli.send_weekly_reports; fi'`
- Restart policy: never, so a failed run waits for the next day instead of
  looping.

One service rather than one per job because the trial plan caps the number
of services. Once the live demo is on (`DEMO_VIEWER_EMAIL`), add
`python -m app.cli.refresh_demo_account;` at the start of the command.

## 3. DNS records in Cloudflare

For each custom domain Railway shows a CNAME target. In Cloudflare → DNS,
add one CNAME per domain with proxy status "DNS only" (grey cloud), so
Railway issues and renews the HTTPS certificates itself:

| Type | Name | Target |
|---|---|---|
| CNAME | `@` (ross.ar) | the target Railway shows for ross.ar |
| CNAME | `www` | the target Railway shows for www.ross.ar |
| CNAME | `api` | the target Railway shows for api.ross.ar |

A CNAME on the root (`@`) works because Cloudflare flattens it.

Check: `https://api.ross.ar/health` answers, and `https://ross.ar` loads the
landing with "Probar la demo" and sign-in working (which proves the site
reaches the API and CORS lets it).

## 4. Email from ross.ar

In Resend, add the domain `ross.ar` with **sending** only (leave receiving off:
its MX record would take over the one below) and copy the DNS records it lists
into Cloudflare, plus a `_dmarc` TXT `v=DMARC1; p=none;`. Once verified, create
an API key limited to `ross.ar` and set the backend's `SMTP_*` variables (see
`.env.production.example`); the `cron` service takes them as references.

Use `SMTP_PORT=2587`: Railway blocks outgoing SMTP on 25, 465 and 587, so a
send on 587 hangs until it times out. Resend accepts STARTTLS on 2587.

Mail sent *to* any `@ross.ar` address (replies to `contacto@ross.ar`, for one)
arrives through Cloudflare Email Routing, whose catch-all rule forwards it to
the owner's inbox.

## 5. Provider apps

Register these addresses in each provider's app, exactly as written:

| Provider | Return URL (OAuth) | Notifications / webhooks |
|---|---|---|
| Shopify | `https://ross.ar/index.html?connector=shopify` | `https://api.ross.ar/connectors/shopify/webhook/{store_id}` |
| Tiendanube | `https://ross.ar/index.html?connector=tiendanube` | `https://api.ross.ar/connectors/tiendanube/webhook/{store_id}` |
| Meta | `https://ross.ar/index.html?connector=meta` | — |
| Google Ads | `https://ross.ar/index.html?connector=google` | — |
| TikTok | `https://ross.ar/index.html?connector=tiktok` | — |
| LinkedIn | `https://ross.ar/index.html?connector=linkedin` | — |
| Mercado Libre | `https://ross.ar/index.html?connector=mercadolibre` | `https://api.ross.ar/connectors/mercadolibre/notifications` (topic `orders_v2`) |
| Mercado Pago | `https://ross.ar/index.html?connector=mercadopago` | `https://api.ross.ar/connectors/mercadopago/notifications` ("Pagos") |
| Mercado Pago (ROSS's own billing) | — | `https://api.ross.ar/billing/webhooks/mercadopago` ("Planes y suscripciones") |

Most providers also ask for a privacy policy and terms of service URL on the
same domain.
