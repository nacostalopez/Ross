// Aramal mascot (Ross) — what the agents do beyond decoration. Classic script; exposes window.Mascot.
//   - the login welcome agent, which reacts to the form: it invites first-time visitors to create an
//     account, covers its eyes on the password field, shakes its head on an error, reacts to the
//     strength of the password being chosen and celebrates a new account;
//   - a toast that reports errors (instead of the browser's alert());
//   - the "loading" block for the chart panel;
//   - the mood of the True ROAS card against the minimum the user set in Alertas.
// It only reads the DOM that index.html and app.js already provide, and everything here is
// decorative or a nicer way to show a message: if a sprite fails to load, the text is still there.
(function () {
  "use strict";

  const Agents = window.Agents;
  if (!Agents) return;

  // The mascot's name. Keep any copy that mentions it grammatically neutral ("Ross está…", never an
  // adjective or article that carries a gender): the name is meant to work for anyone.
  const NAME = "Ross";
  const VISITED_KEY = "aramal_ya_ingreso";
  const $ = (id) => document.getElementById(id);
  const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Point a sprite host at a scene: painted right away, or when its section is shown. hydrate() is a
  // no-op for a scene that is already drawn.
  function show(host, scene) {
    host.dataset.agentScene = scene;
    Agents.hydrate(host);
  }

  function sprite(scene, size, max, cls) {
    const el = document.createElement("div");
    el.className = `pxa pxa-scene ${cls}`;
    el.style.setProperty("--w", size[0]);
    el.style.setProperty("--h", size[1]);
    el.style.setProperty("--max", max);
    el.dataset.agentScene = scene;
    el.setAttribute("aria-hidden", "true");
    return el;
  }

  // ---------------------------------------------------------------------------------------------
  // Login welcome agent
  // ---------------------------------------------------------------------------------------------

  const SCENE = {
    rest: "login-reposo",
    invite: "login-invita",
    shy: "login-clave",
    error: "login-error",
    worried: "login-preocupada",
    thumbs: "login-aprueba",
    celebrate: "login-festeja",
  };

  function wireLogin() {
    const host = $("auth-mascot");
    const bubble = $("auth-invite");
    if (!host || !bubble) return { celebrate: () => Promise.resolve() };

    // An override (error, celebration) wins until the person edits the form or the view changes.
    let override = null;
    let passwordFocused = false;

    function hasVisited() {
      try {
        return window.localStorage.getItem(VISITED_KEY) === "1";
      } catch (err) {
        return true; // no storage: never nag
      }
    }

    const mode = () => (!$("login-form").hidden ? "login" : !$("register-form").hidden ? "register" : "other");

    // What the agent does with nothing forcing it: invite a first-time visitor, follow the password
    // strength while registering, or rest.
    function idle() {
      const current = mode();
      if (current === "login") return hasVisited() ? "rest" : "invite";
      if (current === "register") {
        const meter = $("register-password-meter");
        if (meter.hidden) return "rest";
        const level = Number(meter.dataset.level);
        if (level <= 1) return "worried";
        return level === 3 ? "thumbs" : "rest";
      }
      return "rest";
    }

    function render() {
      const state = override || (passwordFocused && mode() === "login" ? "shy" : idle());
      show(host, SCENE[state]);
      bubble.hidden = state !== "invite";
      $("tab-register").classList.toggle("agents-invite-hint", state === "invite");
    }

    const watch = (el, attributes, handler) => {
      if (el) new MutationObserver(handler).observe(el, { attributes: true, attributeFilter: attributes });
    };

    $("login-form").addEventListener("focusin", (event) => {
      if (event.target.id === "login-password") {
        passwordFocused = true;
        render();
      }
    });
    $("login-form").addEventListener("focusout", (event) => {
      if (event.target.id === "login-password") {
        passwordFocused = false;
        render();
      }
    });
    // Editing after a failure clears the failure.
    for (const form of [$("login-form"), $("register-form")]) {
      form.addEventListener("input", () => {
        if (override === "error" || override === "worried") {
          override = null;
          render();
        }
      });
    }

    // Tab or mode switch: start over.
    const reset = () => {
      override = null;
      passwordFocused = false;
      render();
    };
    watch($("login-form"), ["hidden"], reset);
    watch($("register-form"), ["hidden"], reset);
    watch($("auth-view"), ["hidden"], reset);
    // The password meter is app.js's; its level is the strength the agent reacts to.
    watch($("register-password-meter"), ["hidden", "data-level"], render);
    // A failure message shown by app.js: an expired session worries the agent, anything else makes it
    // shake its head.
    const authError = $("auth-error");
    watch(authError, ["hidden"], () => {
      if (authError.hidden) return;
      override = authError.dataset.kind === "expired" ? "worried" : "error";
      render();
    });

    bubble.addEventListener("click", () => $("tab-register").click());
    render();

    return {
      // Resolves once the celebration has been seen (no wait with reduced motion).
      celebrate() {
        override = "celebrate";
        render();
        return new Promise((resolve) => setTimeout(resolve, reducedMotion() ? 0 : 1200));
      },
    };
  }

  const login = wireLogin();

  function markVisited() {
    try {
      window.localStorage.setItem(VISITED_KEY, "1");
    } catch (err) {
      // storage blocked: the invitation just never shows
    }
  }

  // ---------------------------------------------------------------------------------------------
  // Error toast
  // ---------------------------------------------------------------------------------------------

  let toastTimer = null;

  function dismissToast() {
    const box = $("mascot-toast");
    if (box) box.hidden = true;
  }

  // Text goes in as text (it can carry a server message), never as markup.
  function toast(title, detail) {
    let box = $("mascot-toast");
    if (!box) {
      box = document.createElement("div");
      box.id = "mascot-toast";
      box.className = "mascot-toast";
      box.setAttribute("role", "alert");
      document.body.appendChild(box);
    }
    const text = document.createElement("div");
    text.className = "mascot-toast-text";
    const heading = document.createElement("strong");
    heading.textContent = title;
    text.appendChild(heading);
    if (detail) {
      const extra = document.createElement("span");
      extra.textContent = detail;
      text.appendChild(extra);
    }
    const close = document.createElement("button");
    close.type = "button";
    close.className = "btn btn-ghost";
    close.textContent = "Cerrar";
    close.addEventListener("click", dismissToast);
    text.appendChild(close);

    box.replaceChildren(sprite("mascota-error", [26, 23], 4, "mascot-toast-art"), text);
    box.hidden = false;
    Agents.hydrate(box);
    clearTimeout(toastTimer);
    toastTimer = setTimeout(dismissToast, 12000);
  }

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") dismissToast();
  });

  // ---------------------------------------------------------------------------------------------
  // Loading block
  // ---------------------------------------------------------------------------------------------

  // Shown in `container` only if the wait is noticeable (over 300 ms), so a fast answer never flashes
  // it. Returns stop(failed): call it when the data arrived (the caller renders over the block) or
  // failed (the block is replaced by a message).
  function loading(container) {
    if (!container) return () => {};
    let shown = false;
    const timer = setTimeout(() => {
      shown = true;
      const text = document.createElement("div");
      const heading = document.createElement("strong");
      heading.textContent = `${NAME} está trayendo tus números…`;
      const extra = document.createElement("span");
      extra.textContent = "Los pedidos y el gasto en ads del rango elegido.";
      text.append(heading, extra);
      const block = document.createElement("div");
      block.className = "mascot-loading";
      block.append(sprite("mascota-carga", [26, 23], 5, "mascot-loading-art"), text);
      container.replaceChildren(block);
      Agents.hydrate(block);
    }, 300);
    return (failed) => {
      clearTimeout(timer);
      if (shown && failed) {
        const message = document.createElement("div");
        message.className = "chart-empty";
        message.textContent = "No pudimos traer tus números. Probá de nuevo en un momento.";
        container.replaceChildren(message);
      }
    };
  }

  // ---------------------------------------------------------------------------------------------
  // True ROAS mood
  // ---------------------------------------------------------------------------------------------

  const formatRoas = (n) => Number(n).toLocaleString("es-AR", { minimumFractionDigits: 1, maximumFractionDigits: 2 });

  // The mascot stands in the True ROAS card when that card is the hero tile, celebrating when the
  // result is at or above the user's minimum and worrying when it is below. The sentence next to it
  // says the same in words. With no result (no ad spend yet) there is no mascot.
  function roasMood(roas, threshold) {
    const value = $("stat-value-stat_roas");
    const card = value && value.closest(".stat-hero");
    document.querySelectorAll(".mascot-mood").forEach((el) => {
      if (!card || !card.contains(el)) el.remove();
    });
    if (!card) return;
    if (roas === null || roas === undefined || !Number.isFinite(Number(roas))) {
      card.querySelectorAll(".mascot-mood").forEach((el) => el.remove());
      return;
    }
    const ok = Number(roas) >= Number(threshold);
    let art = card.querySelector(".mascot-mood-art");
    let note = card.querySelector(".mascot-mood-note");
    if (!art) {
      art = sprite("mascota-festeja", [26, 23], 4.5, "mascot-mood mascot-mood-art");
      note = document.createElement("p");
      note.className = "mascot-mood mascot-mood-note";
      card.append(art, note);
    }
    note.classList.toggle("ok", ok);
    note.classList.toggle("low", !ok);
    note.textContent = `${ok ? "▲ Por encima" : "▼ Por debajo"} de tu mínimo (${formatRoas(threshold)}x)`;
    show(art, ok ? "mascota-festeja" : "mascota-preocupada");
  }

  window.Mascot = {
    celebrate: () => login.celebrate(),
    markVisited,
    toast,
    loading,
    roasMood,
  };
})();
