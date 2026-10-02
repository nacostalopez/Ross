#!/usr/bin/env node
/**
 * Regenerates the dashboard captures the landing page shows
 * (frontend/img/dashboard-demo-light.png and -dark.png).
 *
 * Registers a throwaway account ("Demo ROSS"), creates one USD store (the
 * demo data is in USD), presses the dashboard's own "Cargar datos de demo"
 * button, and captures the top of the store panel in both themes. The
 * landing labels them as demo data, so nothing here is a real merchant's.
 *
 * Prerequisites (local/manual, NOT wired into CI): the docker-compose stack
 * running, frontend on FRONTEND_URL (default http://localhost:3100) and the
 * API on the same host, port 8100.
 *
 * Run: npm install && npm run shots:landing   (from this e2e/ directory)
 */
const { chromium } = require("playwright");
const path = require("path");

const FRONTEND_URL = process.env.FRONTEND_URL || "http://localhost:3100";
const API_URL = process.env.API_URL || `${new URL(FRONTEND_URL).protocol}//${new URL(FRONTEND_URL).hostname}:8100`;
const APP = `${FRONTEND_URL.replace(/\/$/, "")}/index.html`;
const OUT_DIR = path.join(__dirname, "..", "frontend", "img");

async function post(url, body, token) {
  const res = await fetch(API_URL + url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`POST ${url} -> ${res.status} ${await res.text()}`);
  return res.json();
}

async function capture(browser, tokens, theme, seed) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 1000 }, reducedMotion: "reduce", serviceWorkers: "block" });
  await context.addInitScript(({ theme, tokens }) => {
    localStorage.setItem("ross_theme", theme);
    localStorage.setItem("ross_token", tokens.access_token);
    localStorage.setItem("ross_refresh_token", tokens.refresh_token);
  }, { theme, tokens });
  const page = await context.newPage();
  await page.goto(APP, { waitUntil: "networkidle" });
  await page.waitForSelector("#store-panel:not([hidden])");
  if (seed) {
    await page.click("#seed-btn");
    await page.waitForFunction(() => !document.getElementById("seed-btn").disabled, null, { timeout: 120000 });
  }
  await page.selectOption("#range-select", "30");
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(800);
  // From the store header down to the end of the daily chart; the widgets below it are cut off.
  const box = await page.locator("#store-panel").boundingBox();
  const chart = await page.locator("#chart-container").boundingBox();
  const file = path.join(OUT_DIR, `dashboard-demo-${theme}.png`);
  await page.screenshot({ path: file, fullPage: true, clip: { x: box.x, y: box.y, width: box.width, height: chart.y + chart.height + 16 - box.y } });
  console.log(`Saved ${file}`);
  await context.close();
}

(async () => {
  const browser = await chromium.launch();
  const tokens = await post("/auth/register", {
    account_name: "Demo ROSS",
    email: `demo-ross-${Date.now()}@example.com`,
    password: "Ross-Demo-2026!xQ7",
  });
  await post("/stores", { name: "Tienda de demostración", platform: "shopify", currency: "USD" }, tokens.access_token);
  await capture(browser, tokens, "light", true);
  await capture(browser, tokens, "dark", false);
  await browser.close();
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
