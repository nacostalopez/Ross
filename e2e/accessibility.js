#!/usr/bin/env node
/**
 * Accessibility and layout gate for the public pages and the dashboard.
 *
 * For the landing, pricing, login and the dashboard (with demo data, light and
 * dark theme), at 1280 and 390 px wide:
 *   - axe-core finds no serious or critical WCAG 2.1 A/AA violation (contrast,
 *     names on controls, ARIA misuse, ...);
 *   - the page has no horizontal scroll;
 *   - no console errors.
 * Each screen is also saved to SHOTS_DIR (default ./shots) so a run can be
 * looked at, and CI uploads them.
 *
 * Runs in CI (.github/workflows/ci.yml, job "accessibility") against a fresh
 * database, and locally against the docker-compose stack:
 *   npm install && npm run test:a11y   (from this e2e/ directory)
 *   FRONTEND_URL=http://localhost:3100 npm run test:a11y
 *
 * Each run registers a throwaway account ("Accesibilidad QA") with one store.
 */
const { chromium } = require("playwright");
const { AxeBuilder } = require("@axe-core/playwright");
const fs = require("fs");
const path = require("path");

const FRONTEND_URL = (process.env.FRONTEND_URL || "http://localhost:3100").replace(/\/$/, "");
const API_URL = process.env.API_URL || `${new URL(FRONTEND_URL).protocol}//${new URL(FRONTEND_URL).hostname}:8100`;
const SHOTS_DIR = process.env.SHOTS_DIR || path.join(__dirname, "shots");
const WIDTHS = [1280, 390];
const BLOCKING = new Set(["serious", "critical"]);

let failures = 0;
function check(name, ok, detail = "") {
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  — ${detail}` : ""}`);
  if (!ok) failures++;
}

async function post(url, body, token) {
  const res = await fetch(API_URL + url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`POST ${url} -> ${res.status} ${await res.text()}`);
  return res.json();
}

async function audit(browser, { name, url, width, theme, tokens, prepare }) {
  const context = await browser.newContext({ viewport: { width, height: 900 }, reducedMotion: "reduce", serviceWorkers: "block" });
  await context.addInitScript(({ theme, tokens }) => {
    if (theme) localStorage.setItem("ross_theme", theme);
    if (tokens) {
      localStorage.setItem("ross_token", tokens.access_token);
      localStorage.setItem("ross_refresh_token", tokens.refresh_token);
    }
  }, { theme, tokens });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });

  await page.goto(`${FRONTEND_URL}/${url}`, { waitUntil: "networkidle" });
  if (prepare) await prepare(page);
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(400);

  const tag = `[${name}/${theme || "light"}/${width}]`;
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  const blocking = result.violations.filter((v) => BLOCKING.has(v.impact));
  check(
    `${tag} no serious or critical accessibility violations`,
    blocking.length === 0,
    blocking.map((v) => `${v.id} (${v.impact}): ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(", ")}`).join(" | "),
  );
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  check(`${tag} no horizontal scroll`, overflow <= 0, overflow > 0 ? `${overflow}px wider than the viewport` : "");
  check(`${tag} no console errors`, errors.length === 0, errors.join(" | "));

  await page.screenshot({ path: path.join(SHOTS_DIR, `${name}-${theme || "light"}-${width}.png`), fullPage: true });
  await context.close();
}

(async () => {
  fs.mkdirSync(SHOTS_DIR, { recursive: true });
  const browser = await chromium.launch();

  for (const width of WIDTHS) {
    await audit(browser, { name: "landing", url: "landing.html", width });
    await audit(browser, { name: "pricing", url: "pricing.html", width });
    await audit(browser, { name: "login", url: "index.html", width });
  }

  const tokens = await post("/auth/register", {
    account_name: "Accesibilidad QA",
    email: `a11y-qa-${Date.now()}@example.com`,
    password: "Ross-QA-2026!xQ7",
  });
  await post("/stores", { name: "Tienda QA", platform: "shopify", currency: "USD" }, tokens.access_token);
  let seeded = false;
  const loadDemoOnce = async (page) => {
    await page.waitForSelector("#store-panel:not([hidden])");
    if (seeded) return;
    await page.click("#more-btn");
    await page.click("#seed-btn");
    await page.waitForFunction(() => !document.getElementById("seed-btn").disabled, null, { timeout: 120000 });
    seeded = true;
  };
  for (const theme of ["light", "dark"]) {
    for (const width of WIDTHS) {
      await audit(browser, { name: "dashboard", url: "index.html", width, theme, tokens, prepare: loadDemoOnce });
    }
  }

  await browser.close();
  console.log(failures ? `\n${failures} check(s) failed` : "\nAll accessibility checks passed");
  process.exit(failures ? 1 : 0);
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
