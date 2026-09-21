// Aramal mascot (Ross) — what the agents do beyond decoration. Classic script; exposes window.Mascot.
//   - the login welcome agent, which reacts to the form: it invites first-time visitors to create an
//     account, covers its eyes on the password field, shakes its head on an error, reacts to the
//     strength of the password being chosen and celebrates a new account;
//   - a toast that reports errors (instead of the browser's alert());
//   - the "loading" block for the chart panel;
//   - the mood of the True ROAS card against the minimum the user set in Alertas;
//   - the "first steps" card for an account that still has things to set up.
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

  // The small "Ross" label that heads every message box (see .ross-box in agentes.css).
  function label() {
    const who = document.createElement("span");
    who.className = "ross-who";
    who.textContent = NAME;
    return who;
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
      box.className = "mascot-toast ross-box";
      box.setAttribute("role", "alert");
      document.body.appendChild(box);
    }
    const text = document.createElement("div");
    text.className = "mascot-toast-text";
    const heading = document.createElement("strong");
    heading.textContent = title;
    text.append(label(), heading);
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

    box.replaceChildren(sprite("mascota-error", [26, 23], 3, "mascot-toast-art"), text);
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
      note.className = "mascot-mood mascot-mood-note ross-box";
      const msg = document.createElement("span");
      msg.className = "ross-msg";
      const arrow = document.createElement("span");
      arrow.className = "ross-arrow";
      const words = document.createElement("span");
      words.className = "ross-words";
      msg.append(arrow, words);
      note.append(label(), msg);
      card.append(art, note);
    }
    note.classList.toggle("ok", ok);
    note.classList.toggle("low", !ok);
    note.querySelector(".ross-arrow").textContent = ok ? "▲" : "▼";
    note.querySelector(".ross-words").textContent = ` ${ok ? "Por encima" : "Por debajo"} de tu mínimo (${formatRoas(threshold)}x)`;
    show(art, ok ? "mascota-festeja" : "mascota-preocupada");
  }

  // ---------------------------------------------------------------------------------------------
  // First steps
  // ---------------------------------------------------------------------------------------------

  // The card at the top of the Dashboard. app.js decides what the steps are and what each button does:
  // `steps` is [{ title, detail, done, label, run }] and `run(button)` goes straight to that action, never
  // by pressing some other button. The card is drawn again each time app.js calls this with the new
  // result; null (or every step done) hides it. With `onHide`, the card offers to be put away.
  function firstSteps(steps, onHide) {
    const card = $("first-steps");
    if (!card) return;
    if (!steps || steps.every((step) => step.done)) {
      card.hidden = true;
      card.replaceChildren();
      delete card.dataset.key;
      return;
    }
    const key = steps.map((step) => (step.done ? 1 : 0)).join("");
    if (card.dataset.key === key && !card.hidden) return; // nothing changed: leave the drawing alone
    card.dataset.key = key;

    const done = steps.filter((step) => step.done).length;
    const next = steps.findIndex((step) => !step.done);
    const list = document.createElement("ol");
    steps.forEach((step, i) => {
      const row = document.createElement("li");
      row.classList.toggle("done", step.done);
      const text = document.createElement("div");
      const title = document.createElement("strong");
      title.textContent = step.title;
      const detail = document.createElement("span");
      detail.textContent = step.detail;
      text.append(title, detail);
      row.append(text);
      if (step.done) {
        const ok = document.createElement("em");
        ok.textContent = "Listo";
        row.append(ok);
      } else {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `btn ${i === next ? "btn-primary" : "btn-ghost"}`;
        button.textContent = step.label;
        button.addEventListener("click", () => step.run(button));
        row.append(button);
      }
      list.append(row);
    });

    const head = document.createElement("div");
    head.className = "first-steps-head";
    const title = document.createElement("h3");
    title.id = "first-steps-title";
    title.textContent = `Primeros pasos · ${done} de ${steps.length}`;
    head.append(title);
    if (onHide) {
      const hide = document.createElement("button");
      hide.type = "button";
      hide.className = "first-steps-hide";
      hide.textContent = "Ocultar";
      hide.addEventListener("click", onHide);
      head.append(hide);
    }
    const intro = document.createElement("p");
    intro.className = "muted";
    intro.textContent = `${NAME} te acompaña: cada botón te lleva directo al paso.`;

    const body = document.createElement("div");
    body.append(head, intro, list);
    card.replaceChildren(sprite("login-reposo", [22, 15], 5, "first-steps-art"), body);
    card.hidden = false;
    Agents.hydrate(card);
  }

  window.Mascot = {
    celebrate: () => login.celebrate(),
    markVisited,
    toast,
    loading,
    roasMood,
    firstSteps,
  };
})();
