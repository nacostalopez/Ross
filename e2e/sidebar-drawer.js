#!/usr/bin/env node
/**
 * Real-browser check of the hamburger button and the sidebar drawer it opens (below 860 px
 * the sidebar is an off-canvas drawer; see frontend/style.css and the "Mobile sidebar
 * drawer" block of frontend/app.js).
 *
 * Proves, in a real Chromium:
 *   - there is no hamburger on the login screen (there is no sidebar to open there);
 *   - once logged in, the button opens the drawer, aria-expanded follows it, and focus moves
 *     into the drawer;
 *   - Escape, a click on the backdrop and choosing a view all close it, and Escape/backdrop
 *     give focus back to the button;
 *   - while the drawer is closed nothing inside it can be reached with Tab;
 *   - widening the window past 860 px with the drawer open leaves the permanent sidebar and a
 *     closed state behind, and shrinking again does not bring the drawer back on its own;
 *   - on desktop there is no hamburger and the sidebar is always visible.
 *
 * Prerequisites (local/manual, NOT wired into CI): the docker-compose stack running, frontend
 * on FRONTEND_URL (default http://localhost:3000) and the API on the same host, port 8000.
 *
 * Run: npm install && npm run test:sidebar   (from this e2e/ directory)
 *   FRONTEND_URL=http://172.x.x.x:3000 npm run test:sidebar   # e.g. the WSL2 address
 *
 * Each run registers a throwaway account ("Menu QA"), so it leaves that test data in the dev
 * database.
 */
const { chromium } = require("playwright");

const FRONTEND_URL = process.env.FRONTEND_URL || "http://localhost:3000";
const API_URL = process.env.API_URL || `${new URL(FRONTEND_URL).protocol}//${new URL(FRONTEND_URL).hostname}:8000`;
const APP = `${FRONTEND_URL.replace(/\/$/, "")}/index.html`;

const PHONE = { width: 390, height: 780 };
const TABLET = { width: 820, height: 900 };
const DESKTOP = { width: 1280, height: 800 };

const problems = [];
let total = 0;
function check(name, ok, detail = "") {
  total += 1;
  if (!ok) problems.push(`${name} ${detail}`);
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`);
}

async function register() {
  const res = await fetch(`${API_URL}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ account_name: "Menu QA", email: `menu-qa-${Date.now()}@example.com`, password: "Aramal-QA-2026!xQ7" }),
  });
  if (!res.ok) throw new Error(`register -> ${res.status} ${await res.text()}`);
  return res.json();
}

async function open(browser, viewport, tokens) {
  const context = await browser.newContext({ viewport, serviceWorkers: "block" });
  if (tokens) {
    await context.addInitScript((t) => {
      localStorage.setItem("escal_token", t.access_token);
      localStorage.setItem("escal_refresh_token", t.refresh_token);
    }, tokens);
  }
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  await page.goto(APP);
  return { context, page, errors };
}

const state = (page) => page.evaluate(() => ({
  expanded: document.getElementById("sidebar-toggle").getAttribute("aria-expanded"),
  open: document.getElementById("dashboard-view").classList.contains("sidebar-open"),
  focusInDrawer: !!document.activeElement && !!document.activeElement.closest("#dashboard-sidebar"),
  focusOnToggle: document.activeElement === document.getElementById("sidebar-toggle"),
}));

// Wait out the 0.2 s slide.
const settle = (page) => page.waitForTimeout(350);

(async () => {
  const browser = await chromium.launch();
  const tokens = await register();

  // 1) Login screen: no button that opens nothing.
  for (const [name, viewport] of [["phone", PHONE], ["tablet", TABLET], ["desktop", DESKTOP]]) {
    const { context, page } = await open(browser, viewport, null);
    await page.waitForSelector("#auth-view:not([hidden])");
    check(`[login/${name}] no hamburger button`, !(await page.isVisible("#sidebar-toggle")));
    await context.close();
  }

  // 2) Phone: the drawer. Every scenario starts from a fresh page, so one failure cannot leave
  // the drawer open and break the steps after it.
  const phone = async () => {
    const opened = await open(browser, PHONE, tokens);
    await opened.page.waitForSelector("#dashboard-view:not([hidden])");
    return opened;
  };
  const openDrawer = async (page) => {
    await page.click("#sidebar-toggle", { timeout: 5000 });
    await settle(page);
  };

  {
    const { context, page } = await phone();
    check("[phone] hamburger visible once logged in", await page.isVisible("#sidebar-toggle"));
    const s = await state(page);
    check("[phone] starts closed", s.expanded === "false" && !s.open && !(await page.isVisible("#nav-dashboard")), JSON.stringify(s));

    // Tab through the whole page: focus must never land inside the closed drawer.
    let hitDrawer = 0;
    await page.evaluate(() => document.body.focus());
    for (let i = 0; i < 40; i++) {
      await page.keyboard.press("Tab");
      if ((await state(page)).focusInDrawer) hitDrawer += 1;
    }
    check("[phone] Tab never reaches the closed drawer", hitDrawer === 0, `${hitDrawer} of 40 stops inside it`);
    await context.close();
  }

  {
    const { context, page, errors } = await phone();
    await openDrawer(page);
    let s = await state(page);
    check("[phone] click opens it", s.open && s.expanded === "true" && (await page.isVisible("#nav-dashboard")), JSON.stringify(s));
    check("[phone] focus moves into the open drawer", s.focusInDrawer, JSON.stringify(s));
    await page.keyboard.press("Escape");
    await settle(page);
    s = await state(page);
    check("[phone] Escape closes it", !s.open && s.expanded === "false" && !(await page.isVisible("#nav-dashboard")), JSON.stringify(s));
    check("[phone] Escape gives focus back to the button", s.focusOnToggle, JSON.stringify(s));
    check("[phone] no page errors", errors.length === 0, errors.join(" | "));
    await context.close();
  }

  {
    const { context, page } = await phone();
    await openDrawer(page);
    await page.mouse.click(PHONE.width - 12, PHONE.height / 2);
    await settle(page);
    const s = await state(page);
    check("[phone] a click on the backdrop closes it", !s.open && s.expanded === "false", JSON.stringify(s));
    check("[phone] the backdrop gives focus back to the button", s.focusOnToggle, JSON.stringify(s));
    await context.close();
  }

  {
    const { context, page } = await phone();
    await openDrawer(page);
    await page.click("#nav-profile", { timeout: 5000 });
    await settle(page);
    const s = await state(page);
    check("[phone] choosing a view closes it and shows the view", !s.open && s.expanded === "false" && (await page.isVisible("#profile-panel")), JSON.stringify(s));
    await context.close();
  }

  {
    // Open, then widen the window: the permanent sidebar takes over and nothing stays "open".
    const { context, page } = await phone();
    await openDrawer(page);
    await page.setViewportSize(DESKTOP);
    await settle(page);
    let s = await state(page);
    check("[phone->desktop] resizing resets the open state", s.expanded === "false" && !s.open, JSON.stringify(s));
    check("[phone->desktop] the sidebar shows as a permanent column", await page.isVisible("#nav-dashboard"));
    await page.setViewportSize(PHONE);
    await settle(page);
    s = await state(page);
    check("[desktop->phone] shrinking back leaves the drawer closed", !s.open && !(await page.isVisible("#nav-dashboard")), JSON.stringify(s));
    await context.close();
  }

  // 3) Tablet width (still below 860 px).
  {
    const { context, page } = await open(browser, TABLET, tokens);
    await page.waitForSelector("#dashboard-view:not([hidden])");
    check("[tablet] hamburger visible once logged in", await page.isVisible("#sidebar-toggle"));
    await page.click("#sidebar-toggle", { timeout: 5000 });
    await settle(page);
    check("[tablet] the drawer opens", (await state(page)).open);
    await context.close();
  }

  // 4) Desktop: permanent sidebar, no button, nav reachable by keyboard.
  {
    const { context, page } = await open(browser, DESKTOP, tokens);
    await page.waitForSelector("#dashboard-view:not([hidden])");
    check("[desktop] no hamburger", !(await page.isVisible("#sidebar-toggle")));
    check("[desktop] the sidebar is always visible", await page.isVisible("#nav-dashboard"));
    let reachedNav = false;
    await page.evaluate(() => document.body.focus());
    for (let i = 0; i < 20 && !reachedNav; i++) {
      await page.keyboard.press("Tab");
      reachedNav = (await state(page)).focusInDrawer;
    }
    check("[desktop] Tab reaches the sidebar", reachedNav);
    await context.close();
  }

  await browser.close();
  console.log(`\n${total - problems.length}/${total} checks passed`);
  if (problems.length) {
    console.log(`PROBLEMS:\n- ${problems.join("\n- ")}`);
    process.exit(1);
  }
})().catch((err) => {
  console.error(err);
  process.exit(2);
});
