// Aramal agents — renders the pixel-art scenes, module heads and role portraits that
// tools/agentes/export.py generates into this folder. Classic script (the frontend has no
// build step); exposes window.Agents. Everything here is decorative: a failed fetch is
// logged and the app carries on without the drawing.
//
// Markup contract (see hydrate): an element with one of these attributes gets its sprite.
//   data-agent-scene="conectores"                         animated scene, loaded on demand
//   data-agent-head="conectores" [data-agent-scale="2"]   module marker for titles
//   data-agent-chip="owner" data-agent-seed="<user id>"   head + chest for the topbar
//   data-agent-bust="owner" data-agent-seed="<user id>"   framed portrait for Perfil
(function () {
  "use strict";

  const BASE = new URL("./", document.currentScript.src).href;
  const PAUSE_KEY = "aramal_agents_paused";

  const cache = new Map();

  function fetchJson(path) {
    if (!cache.has(path)) {
      const request = fetch(BASE + path).then((res) => {
        if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
        return res.json();
      });
      // Don't cache a failure: the next hydrate() gets to retry.
      request.catch(() => cache.delete(path));
      cache.set(path, request);
    }
    return cache.get(path);
  }

  // The JSON is our own static export, but everything that reaches the markup is still coerced to a
  // number, so nothing unexpected can end up inside it.
  const num = (value) => Number(value) || 0;

  // A layer is [[role, [x, y, w, h, x, y, w, h, ...]], ...]: rectangles in reading order, delta-coded
  // (tools/agentes/px.py, delta_coded(): change both together). Each y is relative to the previous
  // rectangle's and, on the same row, so is x. Every role becomes one <path>.
  function rects(flat) {
    if (!Array.isArray(flat)) return ""; // a scene cached in the old format: draw nothing until it refreshes
    let d = "";
    let px = 0;
    let py = 0;
    for (let i = 0; i + 3 < flat.length; i += 4) {
      const dy = num(flat[i + 1]);
      const x = (dy ? 0 : px) + num(flat[i]);
      const y = py + dy;
      const w = num(flat[i + 2]);
      d += `M${x} ${y}h${w}v${num(flat[i + 3])}h-${w}z`;
      px = x;
      py = y;
    }
    return d;
  }

  function paths(list) {
    return list.map(([role, flat]) => `<path class="c${num(role)}" d="${rects(flat)}"/>`).join("");
  }

  // A drawing is a static background layer plus animated parts. Each part is a strip of
  // frames wider than its box; CSS slides it with steps(), so no JS runs per frame.
  function drawingHtml(data) {
    let html = `<svg class="pxa-lay" viewBox="0 0 ${num(data.w)} ${num(data.h)}">${paths(data.bg)}</svg>`;
    for (const part of data.parts) {
      const animated = num(part.n) > 1;
      html +=
        `<div class="pxa-pt" style="left:${num(part.l)}%;top:${num(part.t)}%;width:${num(part.w)}%;height:${num(part.h)}%">` +
        `<svg class="${animated ? "pxa-st" : "pxa-lay"}" viewBox="0 0 ${num(part.vw)} ${num(part.vh)}"` +
        (animated ? ` style="--n:${num(part.n)};--t:${num(part.s)}s"` : "") +
        `>${paths(part.p)}</svg></div>`;
    }
    return html;
  }

  function draw(el, data, kind, scale) {
    el.classList.add("pxa", kind);
    el.style.setProperty("--w", num(data.w));
    el.style.setProperty("--h", num(data.h));
    if (scale) el.style.setProperty("--s", scale);
    el.setAttribute("aria-hidden", "true");
    el.innerHTML = drawingHtml(data);
  }

  // Each person gets one of the skin/hair variants, always the same one for the same id.
  function variantFor(seed, count) {
    const text = String(seed == null ? "" : seed);
    let hash = 2166136261;
    for (let i = 0; i < text.length; i++) {
      hash ^= text.charCodeAt(i);
      hash = Math.imul(hash, 16777619);
    }
    return (hash >>> 0) % count;
  }

  // What to fetch and where to look inside it, from an element's data attributes.
  function sourceFor(el) {
    const d = el.dataset;
    if (d.agentScene) {
      return { key: `scene:${d.agentScene}`, kind: "pxa-scene", load: () => fetchJson(`escenas/${d.agentScene}.json`) };
    }
    const group = d.agentHead ? "heads" : d.agentChip ? "chips" : d.agentBust ? "busts" : null;
    if (!group) return null;
    const name = d.agentHead || d.agentChip || d.agentBust;
    const scale = d.agentScale || (group === "busts" ? 3 : 2);
    return {
      key: `${group}:${name}:${d.agentSeed || ""}:${scale}`,
      kind: "pxa-sprite",
      scale,
      load: () =>
        fetchJson("retratos.json").then((portraits) => {
          const id = group === "heads" ? name : `${name}-${variantFor(d.agentSeed, portraits.variants)}`;
          return portraits[group][id];
        }),
    };
  }

  function paint(el) {
    const source = sourceFor(el);
    if (!source || el.dataset.agentKey === source.key) return Promise.resolve();
    el.dataset.agentKey = source.key;
    return source.load().then(
      (data) => {
        // The element may have been re-pointed at another sprite while this one loaded.
        if (data && el.dataset.agentKey === source.key) draw(el, data, source.kind, source.scale);
      },
      (err) => {
        // Forget the key so a later hydrate() retries.
        if (el.dataset.agentKey === source.key) delete el.dataset.agentKey;
        console.warn("Agents: could not load a sprite", err);
      }
    );
  }

  const SELECTOR = "[data-agent-scene],[data-agent-head],[data-agent-chip],[data-agent-bust]";

  // Paint every sprite placeholder under `root` (or `root` itself) that is not inside a hidden
  // section. Safe to call repeatedly.
  function hydrate(root) {
    const scope = root || document;
    const targets = Array.from(scope.querySelectorAll(SELECTOR));
    if (scope.matches && scope.matches(SELECTOR)) targets.push(scope);
    return Promise.all(targets.filter((el) => !el.closest("[hidden]")).map(paint));
  }

  // A closed modal or another view is not drawn (nor its scene downloaded) until it is shown:
  // whenever something loses its `hidden` attribute, paint the placeholders inside it.
  new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      if (!mutation.target.hidden) hydrate(mutation.target);
    }
  }).observe(document.body, { attributes: true, attributeFilter: ["hidden"], subtree: true });

  // Pause/resume every animation. The choice is remembered; without storage it lasts the session.
  function initPause(button) {
    let paused = false;
    try {
      paused = window.localStorage.getItem(PAUSE_KEY) === "1";
    } catch (err) {
      // storage blocked: default to playing
    }
    function apply() {
      document.documentElement.toggleAttribute("data-agents-paused", paused);
      const label = paused ? "Reanudar las animaciones de los agentes" : "Pausar las animaciones de los agentes";
      button.setAttribute("aria-pressed", String(paused));
      button.setAttribute("aria-label", label);
      button.title = label;
    }
    button.addEventListener("click", () => {
      paused = !paused;
      apply();
      try {
        window.localStorage.setItem(PAUSE_KEY, paused ? "1" : "0");
      } catch (err) {
        // storage blocked: the choice lasts this session only
      }
    });
    apply();
  }

  window.Agents = { hydrate, initPause };

  const pauseButton = document.getElementById("agents-pause");
  if (pauseButton) initPause(pauseButton);
})();
