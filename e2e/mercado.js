#!/usr/bin/env node
/**
 * Real-browser check of the Mercado Libre / Mercado Pago connectors and the
 * "Mi plan" (Mercado Pago Suscripciones) panel.
 *
 * Proves, in a real Chromium against the running stack:
 *   - the connector grid lists Mercado Libre and Mercado Pago by name;
 *   - "Conectar" on each opens the modal with no extra field and sends the
 *     browser to that provider's consent screen with our client_id/state
 *     (the provider page itself is stubbed — no real network call);
 *   - coming back with a code the provider won't accept (no real app is
 *     registered here) fails gracefully with the error toast, not a crash;
 *   - "Mi plan" shows the grandfathered Scale plan, "Disponible pronto" for
 *     unpriced plans, and — once a plan is priced — "Suscribirme", which
 *     reports "El cobro no está configurado" without a billing token;
 *   - returning from Mercado Pago's checkout (?billing=return) explains the
 *     plan updates on confirmation and cleans the URL.
 *
 * Prerequisites (local/manual, NOT wired into CI): the docker-compose stack
 * running. PRICE_GROWTH_CMD is how the script temporarily prices Growth
 * (and restores it to NULL afterwards) — by default through the dev DB
 * container in WSL.
 *
 * Run: npm run test:mercado   (from this e2e/ directory)
 *   FRONTEND_URL=http://172.x.x.x:3100 npm run test:mercado   # e.g. the WSL2 address
 *
 * Each run registers a throwaway account ("Mercado QA").
 */
const { execSync } = require("child_process");
const { chromium } = require("playwright");

const FRONTEND_URL = process.env.FRONTEND_URL || "http://localhost:3100";
const API_URL = process.env.API_URL || `${new URL(FRONTEND_URL).protocol}//${new URL(FRONTEND_URL).hostname}:8100`;
const APP = `${FRONTEND_URL.replace(/\/$/, "")}/index.html`;
const PSQL = process.env.PSQL_CMD || 'wsl -e docker exec escal-db-1 psql -U escal -d escal -c';

const problems = [];
let total = 0;
function check(name, ok, detail = "") {
  total += 1;
  if (!ok) problems.push(`${name} ${detail}`);
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`);
}

async function apiCall(path, { method = "GET", body, token } = {}) {
  const res = await fetch(`${API_URL}${path}`, {
    method,
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`${method} ${path} -> ${res.status} ${await res.text()}`);
  return res.json();
}

function setGrowthPrice(value) {
  execSync(`${PSQL} "UPDATE plans SET monthly_price = ${value} WHERE id = 'growth'"`, { stdio: "pipe" });
}

async function open(browser, tokens, path = "") {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, serviceWorkers: "block" });
  await context.addInitScript((t) => {
    localStorage.setItem("escal_token", t.access_token);
    localStorage.setItem("escal_refresh_token", t.refresh_token);
  }, tokens);
  const page = await context.newPage();
  const errors = [];
  const dialogs = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("dialog", async (d) => {
    dialogs.push(d.message());
    await d.accept();
  });
  await page.goto(`${APP}${path}`);
  return { context, page, errors, dialogs };
}

(async () => {
  const browser = await chromium.launch();
  const tokens = await apiCall("/auth/register", {
    method: "POST",
    body: { account_name: "Mercado QA", email: `mercado-qa-${Date.now()}@example.com`, password: "Aramal-QA-2026!xQ7" },
  });
  const store = await apiCall("/stores", {
    method: "POST",
    token: tokens.access_token,
    body: { name: "QA Links de pago", platform: "mercadopago", currency: "ARS" },
  });

  // 1) Connector grid + connect flow.
  for (const [provider, label, host] of [
    ["mercadolibre", "Mercado Libre", "auth.mercadolibre.com.ar"],
    ["mercadopago", "Mercado Pago", "auth.mercadopago.com"],
  ]) {
    const { context, page, errors } = await open(browser, tokens);
    await page.waitForSelector("#connector-status .connector-card");
    const card = page.locator(".connector-card", { hasText: label });
    check(`[${provider}] card in the connector grid`, (await card.count()) === 1);

    let consentUrl = null;
    await page.route(`https://${host}/**`, async (route) => {
      consentUrl = route.request().url();
      await route.fulfill({ status: 200, contentType: "text/html", body: "<h1>consent stub</h1>" });
    });
    await card.locator("[data-connect-provider]").click();
    check(`[${provider}] modal has no extra field`, !(await page.isVisible("#connect-provider-field")));
    check(
      `[${provider}] modal title`,
      (await page.textContent("#connect-provider-title")) === `Conectar ${label}`,
    );
    await Promise.all([page.waitForURL(`https://${host}/**`), page.click("#connect-provider-form button[type=submit]")]);
    const url = new URL(consentUrl);
    check(`[${provider}] redirected to ${host}`, url.host === host, consentUrl);
    check(`[${provider}] consent URL carries state`, !!url.searchParams.get("state"));
    check(
      `[${provider}] redirect_uri comes back to index.html`,
      (url.searchParams.get("redirect_uri") || "").endsWith(`index.html?connector=${provider}`),
      url.searchParams.get("redirect_uri"),
    );

    // Provider sends the user back with a code our (unregistered) app can't redeem.
    await page.goto(`${APP}?connector=${provider}&code=fake-code&state=${url.searchParams.get("state")}`);
    await page.waitForSelector("#dashboard-view:not([hidden])");
    await page.waitForTimeout(800);
    const pageText = await page.textContent("body");
    check(
      `[${provider}] failed exchange shows the error toast`,
      pageText.includes(`No se pudo completar la conexión con ${label}`),
    );
    check(`[${provider}] URL cleaned after callback`, !page.url().includes("code="), page.url());
    check(`[${provider}] no page errors`, errors.length === 0, errors.join("; "));
    await context.close();
  }

  // 2) Mi plan, nothing priced yet.
  {
    const { context, page, errors } = await open(browser, tokens);
    await page.waitForSelector("#dashboard-view:not([hidden])");
    await page.click("#nav-profile");
    await page.waitForSelector("#plan-options .plan-option");
    const summary = await page.textContent("#plan-summary");
    check("[plan] grandfathered on Scale, no charge", summary.includes("Scale") && summary.includes("Sin cobro activo"), summary);
    check("[plan] three plan options", (await page.locator("#plan-options .plan-option").count()) === 3);
    check("[plan] unpriced plans can't be bought", (await page.locator("[data-checkout-plan]").count()) === 0);
    check("[plan] no page errors", errors.length === 0, errors.join("; "));
    await context.close();
  }

  // 3) Mi plan with Growth priced: the button appears and fails gracefully without a billing token.
  setGrowthPrice(49000);
  try {
    const { context, page, errors } = await open(browser, tokens);
    await page.waitForSelector("#dashboard-view:not([hidden])");
    await page.click("#nav-profile");
    await page.waitForSelector("[data-checkout-plan='growth']");
    const priceText = await page.textContent("#plan-options");
    check("[plan] Growth shows its ARS price", /49\.000/.test(priceText), priceText.replace(/\s+/g, " "));
    await page.click("[data-checkout-plan='growth']");
    await page.waitForTimeout(800);
    check("[plan] missing billing token reported", (await page.textContent("body")).includes("El cobro no está configurado"));
    check("[plan] stays on the app", page.url().startsWith(APP), page.url());
    check("[plan] button re-enabled", await page.isEnabled("[data-checkout-plan='growth']"));
    check("[plan/priced] no page errors", errors.length === 0, errors.join("; "));
    await context.close();
  } finally {
    setGrowthPrice("NULL");
  }

  // 4) Back from Mercado Pago's checkout.
  {
    const { context, page, dialogs, errors } = await open(browser, tokens, "?billing=return");
    await page.waitForSelector("#dashboard-view:not([hidden])");
    check(
      "[billing return] explains the plan updates on confirmation",
      dialogs.some((m) => m.includes("apenas Mercado Pago confirme")),
      dialogs.join(" | "),
    );
    check("[billing return] URL cleaned", !page.url().includes("billing="), page.url());
    check("[billing return] no page errors", errors.length === 0, errors.join("; "));
    await context.close();
  }

  await browser.close();
  console.log(`\n${total - problems.length}/${total} checks passed (store ${store.id})`);
  if (problems.length) process.exit(1);
})().catch((err) => {
  console.error(err);
  try {
    setGrowthPrice("NULL");
  } catch (_) {}
  process.exit(1);
});
