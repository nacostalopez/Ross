#!/usr/bin/env node
/**
 * Real-browser E2E check for Web Push (see backend/app/services/push.py,
 * frontend's #push-toggle bell icon, frontend/sw.js).
 *
 * What this actually proves, end to end, against real infrastructure:
 *   1. A real user clicking the bell icon gets subscribed via the browser's
 *      real Push API (not mocked).
 *   2. The backend's pywebpush call signs and posts to Google's real FCM
 *      service successfully.
 *   3. The notification is actually delivered and shown by the service
 *      worker in that browser.
 *
 * Prerequisites (all local/manual — this is NOT wired into CI, see below):
 *   - The docker-compose stack running (`docker compose up`) with the
 *     frontend on http://localhost:3100 and backend on :8100.
 *   - VAPID_PRIVATE_KEY / VAPID_PUBLIC_KEY set in the root .env (see
 *     `python scripts/generate_vapid_keys.py`) and the backend container
 *     recreated since (`docker compose up -d backend`) — plain `restart`
 *     doesn't re-read .env.
 *   - Real Google Chrome installed. Playwright's own bundled Chromium has
 *     no Google API key, so PushManager.subscribe() fails outright with
 *     "push service not available" — this script launches with
 *     `channel: "chrome"` specifically to avoid that.
 *
 * Run: npm install && npm run test:push   (from this e2e/ directory)
 *
 * Why this isn't in GitHub Actions: it needs a real Chrome binary, real
 * outbound internet access to Google's FCM, and real VAPID secrets in the
 * runner — each one is a legitimate thing to wire up later, but none of
 * them are free, and a flaky external dependency (Google's push service)
 * failing a PR check is worse than not having the check. Run it locally
 * before/after touching push-related code instead.
 *
 * The retry loop below isn't defensive boilerplate — it's the fix for a
 * real, reproducible finding from building this test: clicking the bell
 * icon via Playwright's synthetic click sometimes leaves
 * `pushManager.subscribe()` hanging forever (never resolves or rejects).
 * A `setTimeout`-triggered subscribe (no user gesture at all) hangs
 * every time; a real dispatched click succeeds most but not all of the
 * time. That's consistent with a race in Chrome/CDP's handling of
 * synthetic-click "user activation" for this specific API, not a bug in
 * the app's own code (the app's click handler was already trimmed to the
 * minimum awaits before subscribe() as part of this investigation, which
 * measurably helped but didn't eliminate the raciness). A real person
 * clicking with a real mouse is not expected to hit this at all — it's
 * an automation-specific quirk, so the retry loop is the correct way to
 * script around it, not a workaround for a real product bug.
 */
const { chromium } = require("playwright");
const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");

const FRONTEND_URL = process.env.FRONTEND_URL || "http://localhost:3100";
const MAX_SUBSCRIBE_CLICKS = 6;
const SUBSCRIBE_POLL_MS = 300;
const SUBSCRIBE_POLL_ATTEMPTS = 10; // ~3s of polling per click before retrying
const NOTIFICATION_POLL_ATTEMPTS = 15; // ~15s to allow the real FCM round trip

function log(msg) {
  console.log(`[push-e2e] ${msg}`);
}

async function isSubscribed(page) {
  return page.evaluate(async () => {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    return !!sub;
  });
}

async function clickBellUntilSubscribed(page) {
  for (let attempt = 1; attempt <= MAX_SUBSCRIBE_CLICKS; attempt++) {
    await page.click("#push-toggle");
    for (let i = 0; i < SUBSCRIBE_POLL_ATTEMPTS; i++) {
      await page.waitForTimeout(SUBSCRIBE_POLL_MS);
      if (await isSubscribed(page)) {
        log(`subscribed after ${attempt} click(s)`);
        return;
      }
    }
    log(`click ${attempt}/${MAX_SUBSCRIBE_CLICKS} didn't take (subscribe() likely hung) — retrying`);
  }
  throw new Error(`Never subscribed after ${MAX_SUBSCRIBE_CLICKS} clicks`);
}

async function main() {
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), "ross-push-e2e-"));
  const context = await chromium.launchPersistentContext(userDataDir, { channel: "chrome" });
  await context.grantPermissions(["notifications"], { origin: FRONTEND_URL });
  const page = context.pages()[0] || (await context.newPage());

  const consoleErrors = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });

  try {
    const email = `push-e2e+${Date.now()}@example.com`;

    log("registering a fresh account");
    await page.goto(`${FRONTEND_URL}/index.html`, { waitUntil: "networkidle" });
    await page.click("#tab-register");
    await page.fill("#register-account-name", "Push E2E");
    await page.fill("#register-email", email);
    await page.fill("#register-password", "supersecret123");
    await page.click('#register-form button[type="submit"]');
    await page.waitForSelector("#dashboard-view:not([hidden])", { timeout: 15000 });

    const vapidKey = await page.getAttribute("#push-toggle", "data-vapid-key").catch(() => null);
    await page.waitForSelector("#push-toggle:not([hidden])", { timeout: 10000 }).catch(() => {
      throw new Error(
        "The bell icon never appeared — either VAPID_PUBLIC_KEY isn't configured on the backend " +
          "(see prerequisites above), or GET /push/vapid-public-key failed."
      );
    });
    assert(vapidKey, "expected a data-vapid-key attribute once the toggle is visible");

    log("clicking the bell icon (with retries — see the doc comment above)");
    await clickBellUntilSubscribed(page);

    log("creating a store so the Reportes modal is reachable");
    await page.click("#empty-new-store-btn");
    await page.fill("#new-store-name", "Push E2E Store");
    await page.click('#new-store-form button[type="submit"]');
    await page.waitForSelector("#store-panel:not([hidden])");

    log('triggering a real send via the existing "Enviar ahora" button');
    await page.click("#reports-btn");
    await page.waitForSelector("#report-preferences-modal:not([hidden])");
    await page.click("#report-preferences-send-now");
    await page.waitForSelector("#report-preview:not([hidden])", { timeout: 10000 });

    log("waiting for the real push to round-trip through Google's FCM");
    let notifications = [];
    for (let i = 0; i < NOTIFICATION_POLL_ATTEMPTS; i++) {
      notifications = await page.evaluate(async () => {
        const reg = await navigator.serviceWorker.ready;
        return (await reg.getNotifications()).map((n) => ({ title: n.title, body: n.body }));
      });
      if (notifications.length > 0) break;
      await page.waitForTimeout(1000);
    }

    assert(notifications.length > 0, "no notification arrived — see prerequisites above (VAPID configured? Chrome real?)");
    assert(notifications[0].title.includes("Push E2E Store"), `unexpected notification title: ${notifications[0].title}`);
    assert(consoleErrors.length === 0, `unexpected console errors: ${consoleErrors.join("; ")}`);

    log(`PASS — received: "${notifications[0].title}"`);
  } finally {
    await context.close();
    fs.rmSync(userDataDir, { recursive: true, force: true });
  }
}

main().then(
  () => process.exit(0),
  (err) => {
    console.error(`[push-e2e] FAIL — ${err.message}`);
    process.exit(1);
  }
);
