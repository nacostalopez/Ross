const CACHE_NAME = "aramal-shell-v3";

// Agent scenes (agentes/escenas/*.json) are not listed on purpose: they are fetched on
// demand and cached by the stale-while-revalidate handler below on first use.
const SHELL_ASSETS = [
  "index.html",
  "landing.html",
  "style.css",
  "app.js",
  "agentes/agentes.js",
  "agentes/mascota.js",
  "agentes/agentes.css",
  "agentes/paleta.css",
  "agentes/retratos.json",
  "manifest.json",
  "icons/icon-192.png",
  "icons/icon-512.png",
  "icons/icon-maskable-512.png",
  "icons/apple-touch-icon.png",
  "icons/favicon-32.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(SHELL_ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((names) => Promise.all(
        names.filter((name) => name !== CACHE_NAME).map((name) => caches.delete(name))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;

  // Only ever handle same-origin GETs — the API lives on a different
  // origin/port and must always go straight to the network untouched.
  if (request.method !== "GET" || new URL(request.url).origin !== self.location.origin) {
    return;
  }

  if (request.mode === "navigate") {
    // Network-first: an online user always gets the latest shell; offline
    // falls back to whatever was last cached.
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
          return response;
        })
        .catch(() => caches.match(request).then((cached) => cached || caches.match("index.html")))
    );
    return;
  }

  // Static assets: stale-while-revalidate.
  event.respondWith(
    caches.match(request).then((cached) => {
      const network = fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
          return response;
        })
        .catch(() => cached);
      return cached || network;
    })
  );
});

// ---------------------------------------------------------------------------
// Web Push — see app/services/push.py on the backend for who sends these
// (proactive CAC/ROAS alerts and weekly reports, opt-in, fanned out to
// every device subscribed via the topbar's notification toggle).
// ---------------------------------------------------------------------------

self.addEventListener("push", (event) => {
  let payload = { title: "ARAMAL", body: "" };
  try {
    if (event.data) payload = event.data.json();
  } catch (err) {
    // Not JSON (shouldn't happen — the backend always sends JSON) — fall
    // back to the default title/empty body rather than dropping the push.
  }

  event.waitUntil(
    self.registration.showNotification(payload.title || "ARAMAL", {
      body: payload.body || "",
      icon: "icons/icon-192.png",
      badge: "icons/icon-192.png",
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if (client.url.includes("index.html") && "focus" in client) return client.focus();
      }
      if (self.clients.openWindow) return self.clients.openWindow("index.html");
    })
  );
});
