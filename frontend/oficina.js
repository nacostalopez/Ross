// "Oficina de agentes" — the dashboard widget that shows each ROSS module as an agent at a
// meeting table (seen from above), with a lounge where they take turns for coffee. Clicking an
// agent, on the drawing or in the list beside it, unfolds its tasks; running one makes the agent
// work and reports what the real API returned. Classic script; exposes window.Office.
//
// It borrows a few globals from app.js (state, api, fmtMoney, refresh*, runConnectorBackfill,
// openConnectModal, ...), all of which exist by the time a widget is mounted. The drawing is
// decorative: if canvas is unavailable the list of agents and tasks still works on its own.
(function () {
  "use strict";

  const W = 320, H = 240;
  const COLOR_KEY = "ross_oficina_color";
  const ME_KEY = "ross_oficina_me";

  const esc = (v) => String(v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const reduceMotion = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const SKINS = ["#f1c9a0", "#c98f64", "#8a5a3c", "#f6d7b8"];
  const HAIRS = ["#2b1d14", "#0e0e12", "#7a4a22", "#d9b36a", "#b5523a"];
  const CLOTHS = ["#8fbbe8", "#2d7bc4", "#3fae6d", "#e0a526", "#d9534f", "#8a63d2"];
  const SKIN_NAMES = ["Piel clara", "Piel trigueña", "Piel oscura", "Piel muy clara"];
  const HAIR_NAMES = ["Cabello castaño oscuro", "Cabello negro", "Cabello castaño", "Cabello rubio", "Cabello pelirrojo"];
  const CLOTH_NAMES = ["Ropa celeste", "Ropa azul", "Ropa verde", "Ropa amarilla", "Ropa roja", "Ropa violeta"];

  const SEATS = [
    { id: "con", name: "Pulso", mod: "Conectores", mide: "Qué tan frescos están los datos: última sincronización y errores.", role: "Le toma el pulso a la sincronización de tiendas y plataformas.", x: 70, y: 78, face: "down", cloth: "#2d7bc4", skin: "#f1c9a0", hair: "#2b1d14", hue: "#8fc2f0" },
    { id: "fin", name: "Saldo", mod: "Finanzas", mide: "True ROAS y ganancia real después de anuncios.", role: "Muestra lo que queda al final, después de todos los costos.", x: 120, y: 78, face: "down", cloth: "#3fae6d", skin: "#c98f64", hair: "#0e0e12", hue: "#8fd9ae" },
    { id: "ads", name: "Foco", mod: "Anuncios", mide: "Gasto, CTR y CPC de cada creativo.", role: "Ayuda a ver dónde conviene poner el gasto.", x: 170, y: 78, face: "down", cloth: "#e8833a", skin: "#8a5a3c", hair: "#7a4a22", hue: "#f4b585" },
    { id: "cli", name: "Retorno", mod: "Clientes", mide: "Valor de vida por cohorte, recompra y recupero del CAC.", role: "Sigue si los clientes regresan a comprar.", x: 70, y: 158, face: "up", cloth: "#8a63d2", skin: "#f6d7b8", hair: "#d9b36a", hue: "#c3aaf0" },
    { id: "ale", name: "Vigía", mod: "Alertas", mide: "CAC por canal y ROAS mínimo.", role: "Vigila los números y avisa si se desvían.", x: 120, y: 158, face: "up", cloth: "#d9534f", skin: "#c98f64", hair: "#2b1d14", hue: "#f2a39c" },
    { id: "rep", name: "Bitácora", mod: "Reportes", mide: "El resumen de la semana de cada tienda.", role: "Lleva el registro de la semana y lo arma como resumen.", x: 170, y: 158, face: "up", cloth: "#e0a526", skin: "#f1c9a0", hair: "#0e0e12", hue: "#f0cf7a" },
  ];
  const HUB = { x: 120, y: 118 };

  // ---------- palette: colour mode, or one slate ramp ("sobrio") ----------
  const KEEP = { "#4cc38a": 1, "#ef6a5b": 1, "#fff": 1, "#111": 1, "#5fb0f0": 1, "#c0392b": 1, "#1f8a53": 1 };
  const PAINT = {
    "#1c2742": "#4f6580", "#1f2b48": "#56708c", "#2a3552": "#2f6f62", "#2d3957": "#337a6b",
    "#3c4b6c": "#b5524a", "#2c3755": "#8f3d38", "#27324d": "#4b6a9a", "#161d30": "#2b3a5c",
    "#3b4a6b": "#7fb0cf", "#3a4660": "#9a7a56", "#4a5b82": "#2f9a8c", "#5b6f99": "#44b5a4",
    "#58739f": "#86c8f0", "#394664": "#4a5a80", "#2a3550": "#5a3f6b", "#1b2338": "#3b2a4a",
    "#8794b3": "#d8e2f2", "#c9d4ea": "#f1f5fb", "#0b1a33": "#0b2a3d",
  };
  const toneCache = {};
  function sober(c) {
    if (c.charAt(0) !== "#" || KEEP[c]) return c;
    if (toneCache[c]) return toneCache[c];
    let h = c.slice(1);
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    const R = parseInt(h.slice(0, 2), 16), G = parseInt(h.slice(2, 4), 16), B = parseInt(h.slice(4, 6), 16);
    const L = Math.pow((0.2126 * R + 0.7152 * G + 0.0722 * B) / 255, 0.85);
    return (toneCache[c] = "rgb(" + Math.round(10 + L * 206) + "," + Math.round(16 + L * 212) + "," + Math.round(32 + L * 213) + ")");
  }

  let inst = null;

  function readMe() {
    const me = { name: "", gender: "m", hair: 0, skin: 0, cloth: 0 };
    try {
      const s = JSON.parse(localStorage.getItem(ME_KEY) || "null");
      if (s && typeof s === "object") Object.assign(me, { name: String(s.name || "").slice(0, 12), gender: s.gender === "f" ? "f" : "m", hair: (s.hair | 0) % HAIRS.length, skin: (s.skin | 0) % SKINS.length, cloth: (s.cloth | 0) % CLOTHS.length });
    } catch (e) { /* storage unavailable: defaults */ }
    return me;
  }
  function readColor() { try { return localStorage.getItem(COLOR_KEY) !== "sober"; } catch (e) { return true; } }

  // -------------------------------------------------------------------------------------------
  // Tasks: what each agent can do, backed by the real API. Each returns { text, lines? }.
  // -------------------------------------------------------------------------------------------
  const storeId = () => state.activeStoreId;
  const money = (n) => fmtMoney(n, state.activeStoreCurrency);
  const rangeQs = () => { const { start, end } = dateRange(); return "start=" + encodeURIComponent(start) + "&end=" + encodeURIComponent(end); };
  const monthLabel = (iso) => new Intl.DateTimeFormat("es-AR", { month: "short", year: "numeric" }).format(new Date(iso));
  const canWrite = () => {
    const store = (state.stores || []).find((s) => s.id === state.activeStoreId);
    return ["owner", "admin"].includes(store && store.effective_role) && !isDemoSession();
  };

  const TASKS = {
    con: [
      { label: "Actualizar estado de conexiones", run: async () => {
        await refreshConnectorHealth();
        const h = state.lastConnectorHealth || {};
        const keys = Object.keys(h).filter((k) => CONNECTOR_STATUS_PROVIDERS.includes(k));
        const ok = keys.filter((k) => h[k].connected).length, bad = keys.filter((k) => h[k].last_sync_status === "error" || (!h[k].connected && h[k].last_error)).length;
        return { text: ok + " conectada" + (ok === 1 ? "" : "s") + (bad ? " y " + bad + " con error." : ", ninguna con error.") };
      } },
      { label: "Traer historial de 30 días", write: true, run: async () => {
        const h = state.lastConnectorHealth || {};
        const targets = CONNECTOR_STATUS_PROVIDERS.filter((p) => h[p] && h[p].connected && CONNECTOR_BACKFILL_ENDPOINTS[p]);
        if (!targets.length) return { text: "No hay plataformas conectadas de las que traer historial." };
        const lines = [];
        for (const p of targets) {
          await runConnectorBackfill(p);
          const run = connectorBackfillState[storeId() + ":" + p];
          lines.push((PROVIDER_CONNECT_CONFIG[p] ? PROVIDER_CONNECT_CONFIG[p].label : p) + ": " + (run && run.message ? run.message : "listo."));
        }
        return { text: "Terminó el historial.", lines };
      } },
    ],
    fin: [
      { label: "Actualizar métricas", run: async () => {
        await refreshMetrics();
        const s = state.lastSummary;
        if (!s) return { text: "Todavía no hay métricas para este período." };
        return { text: "Métricas al día.", lines: ["True ROAS: " + (s.true_roas == null ? "—" : s.true_roas + "x"), "Ganancia real después de anuncios: " + money(s.real_profit_after_ads)] };
      } },
      { label: "Explicar mi semana", run: async () => {
        const id = storeId();
        const data = await api("/stores/" + id + "/narrative");
        return { text: data.text, lines: (data.facts || []).map((f) => f.label + ": " + f.value) };
      } },
    ],
    ads: [
      { label: "Revisar creativos", run: async () => {
        const rows = await api("/stores/" + storeId() + "/metrics/creatives?" + rangeQs());
        if (!rows.length) return { text: "Todavía no hay datos de creativos en este período." };
        const top = rows.slice().sort((a, b) => b.spend - a.spend).slice(0, 3);
        return { text: rows.length + " creativo" + (rows.length === 1 ? "" : "s") + " en el período. Los de mayor gasto:", lines: top.map((r) => (r.ad_name || r.ad_id) + " · " + (PLATFORM_LABELS[r.platform] || r.platform) + " · gasto " + money(r.spend) + (r.ctr == null ? "" : " · CTR " + r.ctr + "%")) };
      } },
      { label: "Ver CAC por canal", run: async () => {
        const rows = await api("/stores/" + storeId() + "/metrics/cac-by-channel?" + rangeQs());
        if (!rows.length) return { text: "Todavía no hay clientes nuevos con gasto por canal en este período." };
        const recent = rows.slice().sort((a, b) => String(b.cohort_month).localeCompare(String(a.cohort_month))).slice(0, 4);
        return { text: "Costo de adquisición por canal, de lo más reciente a lo más antiguo:", lines: recent.map((r) => monthLabel(r.cohort_month) + " · " + r.channel + " · " + r.new_customers + " cliente" + (r.new_customers === 1 ? "" : "s") + " nuevo" + (r.new_customers === 1 ? "" : "s") + " · CAC " + (r.cac == null ? "—" : money(r.cac))) };
      } },
    ],
    cli: [
      { label: "Revisar cohortes", run: async () => {
        const rows = await api("/stores/" + storeId() + "/metrics/ltv-cohorts?" + rangeQs());
        if (!rows.length) return { text: "Todavía no hay cohortes en este período." };
        const recent = rows.slice().sort((a, b) => String(b.cohort_month).localeCompare(String(a.cohort_month))).slice(0, 3);
        return { text: "Las cohortes más recientes:", lines: recent.map((r) => monthLabel(r.cohort_month) + " · " + r.new_customers + " clientes · CAC " + (r.cac == null ? "—" : money(r.cac)) + " · recupero " + (r.payback_month == null ? "todavía no" : "en el mes " + r.payback_month)) };
      } },
      { label: "Ver atribución por canal", run: async () => {
        const rows = await api("/stores/" + storeId() + "/metrics/attribution-by-channel?" + rangeQs());
        if (!rows.length) return { text: "Todavía no hay pedidos para atribuir en este período." };
        return { text: "Ingresos por canal:", lines: rows.map((r) => r.channel + " · " + r.orders + " pedido" + (r.orders === 1 ? "" : "s") + " (" + r.repeat_orders + " de recompra) · " + money(r.revenue)) };
      } },
    ],
    ale: [
      { label: "Revisar alertas ahora", write: true, run: async () => {
        const r = await api("/stores/" + storeId() + "/alert-preferences/check-now", { method: "POST" });
        const lines = (r.cac_alerts_sent || []).map((x) => "CAC: " + x);
        if (r.roas_alert_sent) lines.push("Se envió un aviso de ROAS bajo.");
        return lines.length ? { text: "Se disparó " + (lines.length === 1 ? "una alerta:" : "estas alertas:"), lines } : { text: "Todo bien: no se disparó ninguna alerta." };
      } },
      { label: "Configurar umbrales", run: async () => { document.getElementById("alerts-btn").click(); return { text: "Abrí la configuración de alertas." }; } },
    ],
    rep: [
      { label: "Enviarme el resumen por email", write: true, run: async () => {
        const r = await api("/stores/" + storeId() + "/report-preferences/send-now", { method: "POST" });
        return { text: "Resumen enviado. Así se ve:", preview: r.body };
      } },
      { label: "Configurar reportes", run: async () => { await openReportPreferencesModal(); return { text: "Abrí la configuración de reportes." }; } },
    ],
  };

  // -------------------------------------------------------------------------------------------
  // Mount
  // -------------------------------------------------------------------------------------------
  function mount(root) {
    if (inst) inst.destroy();
    if (!root) return;
    inst = create(root);
  }
  function unmount() { if (inst) inst.destroy(); inst = null; }

  function create(root) {
    const S = {
      tick: 0, color: readColor(), me: readMe(), openId: null, visible: true, nextBreak: 50,
    };
    const agents = {};
    SEATS.forEach((s, i) => {
      const top = s.y < 100, aisle = top ? 68 : 172;
      const route = [{ x: s.x, y: s.y }, { x: s.x, y: aisle }, { x: 206, y: aisle }, { x: 206, y: 120 }, { x: 244, y: 120 }, { x: 244, y: 40 }];
      const cum = [0];
      for (let k = 1; k < route.length; k++) cum.push(cum[k - 1] + Math.abs(route[k].x - route[k - 1].x) + Math.abs(route[k].y - route[k - 1].y));
      agents[s.id] = Object.assign({}, s, { i, mode: "sit", s: 0, route, cum, total: cum[cum.length - 1], wait: 0, busy: false, okUntil: 0, errUntil: 0, alert: false, result: null, log: [] });
    });
    const list = () => SEATS.map((s) => agents[s.id]);
    const night = () => document.documentElement.dataset.theme === "dark";

    // ----- DOM -----
    root.innerHTML = "";
    const wrap = document.createElement("div");
    wrap.className = "office";
    wrap.innerHTML =
      '<div class="office-stage"><canvas class="office-canvas" role="img" aria-label="Oficina vista desde arriba: una mesa con seis agentes, uno por módulo, y una zona de descanso con café. Elegí un agente en la lista para ver sus tareas."></canvas>' +
      '<span class="office-zone" style="left:3.4%;top:3.6%">OFICINA</span><span class="office-zone" style="left:73%;top:15%">DESCANSO</span></div>' +
      '<div class="office-side"><div class="office-tools"><button type="button" class="btn btn-ghost office-mode" aria-pressed="' + S.color + '"></button></div><div class="office-acc"></div></div>';
    root.appendChild(wrap);
    const stage = wrap.querySelector(".office-stage"), cv = wrap.querySelector("canvas"), acc = wrap.querySelector(".office-acc"), modeBtn = wrap.querySelector(".office-mode");
    cv.width = W; cv.height = H;
    const ctx = cv.getContext("2d");
    if (ctx) ctx.imageSmoothingEnabled = false;

    SEATS.forEach((s) => {
      const t = document.createElement("span"); t.className = "office-tag"; t.textContent = s.name.toUpperCase();
      t.style.left = (s.x / W * 100) + "%"; t.style.top = ((s.y < 100 ? 101 : 130) / H * 100) + "%"; stage.appendChild(t);
    });
    const meTag = document.createElement("span"); meTag.className = "office-tag"; meTag.style.left = (36 / W * 100) + "%"; meTag.style.top = (130 / H * 100) + "%"; stage.appendChild(meTag);
    const meName = () => (S.me.name || "").trim().toUpperCase();
    const updateMeTag = () => { meTag.textContent = meName() || "TU LUGAR"; };
    updateMeTag();
    const updateModeBtn = () => { modeBtn.textContent = S.color ? "Modo sobrio" : "Modo color"; modeBtn.setAttribute("aria-pressed", String(!S.color)); };
    updateModeBtn();

    // ----- drawing -----
    const tone = (c) => (S.color ? (PAINT[c] || c) : sober(c));
    const r = (x, y, w, h, c) => { ctx.fillStyle = tone(c); ctx.fillRect(Math.round(x), Math.round(y), w, h); };

    function posAt(a) {
      const s = Math.max(0, Math.min(a.total, a.s));
      let k = 1; while (k < a.cum.length - 1 && s > a.cum[k]) k++;
      const p0 = a.route[k - 1], p1 = a.route[k], seg = a.cum[k] - a.cum[k - 1] || 1, f = (s - a.cum[k - 1]) / seg;
      return { x: p0.x + (p1.x - p0.x) * f, y: p0.y + (p1.y - p0.y) * f, dy: p1.y - p0.y };
    }
    const seated = (a) => a.mode === "sit";
    const away = (a) => a.mode === "going" || a.mode === "coffee" || a.mode === "back";

    function sitter(a, t) {
      const x = a.x, y = a.y, s = (t >> 1) % 2, working = true;
      if (a.face === "down") {
        r(x - 6, y - 9, 12, 12, "#2a3550"); r(x - 6, y - 10, 12, 2, "#1b2338");
        r(x - 4, y - 4, 8, 6, a.cloth);
        r(x - 5, y + 1 + (working ? s : 0), 2, 7, a.cloth); r(x + 3, y + 1 + (working ? 1 - s : 0), 2, 7, a.cloth);
        r(x - 2, y - 9, 4, 5, a.skin); r(x - 2, y - 9, 4, 2, a.hair); r(x - 1, y - 6, 1, 1, "#111"); r(x + 1, y - 6, 1, 1, "#111");
      } else {
        r(x - 6, y - 2, 12, 12, "#2a3550"); r(x - 6, y + 8, 12, 2, "#1b2338");
        r(x - 4, y - 1, 8, 6, a.cloth);
        r(x - 5, y - 8 + s, 2, 7, a.cloth); r(x + 3, y - 8 + (1 - s), 2, 7, a.cloth);
        r(x - 2, y - 6, 4, 5, a.hair); r(x - 2, y - 6, 4, 1, a.skin);
      }
    }
    function walker(x, y, a, back, fr) {
      const s = fr % 2;
      r(x - 3, y, 6, 1, "rgba(0,0,0,.35)");
      r(x - 2, y - 3, 2, 3 - (s ? 1 : 0), "#1a1f33"); r(x, y - 3, 2, 3 - (s ? 0 : 1), "#1a1f33");
      r(x - 3, y - 8, 6, 5, a.cloth);
      r(x - 4, y - 8 + (s ? 0 : 1), 1, 4, a.cloth); r(x + 3, y - 8 + (s ? 1 : 0), 1, 4, a.cloth);
      r(x - 2, y - 12, 4, 4, back ? a.hair : a.skin);
      if (!back) { r(x - 2, y - 12, 4, 2, a.hair); r(x - 1, y - 10, 1, 1, "#111"); r(x + 1, y - 10, 1, 1, "#111"); }
    }
    function bubble(x, y, kind, t) {
      r(x - 5, y - 20, 11, 9, "#fff"); r(x - 1, y - 11, 3, 2, "#fff");
      if (kind === "!") { r(x, y - 18, 1, 4, "#c0392b"); r(x, y - 13, 1, 1, "#c0392b"); }
      else if (kind === "..") { for (let d = 0; d < 3; d++) if (((t >> 2) + d) % 3 !== 0) r(x - 3 + d * 3, y - 15, 2, 2, "#111"); }
      else { r(x - 3, y - 15, 1, 1, "#1f8a53"); r(x - 2, y - 14, 1, 1, "#1f8a53"); r(x - 1, y - 15, 1, 1, "#1f8a53"); r(x, y - 16, 1, 1, "#1f8a53"); r(x + 1, y - 17, 1, 1, "#1f8a53"); r(x + 2, y - 18, 1, 1, "#1f8a53"); }
    }

    function drawRoom(t) {
      r(0, 0, W, H, "#05080f");
      for (let ty = 6; ty < 236; ty += 8) for (let tx = 6; tx < 314; tx += 8) r(tx, ty, 8, 8, ((tx + ty) / 8) % 2 ? "#1c2742" : "#1f2b48");
      for (let ly = 28; ly < 236; ly += 8) for (let lx = 222; lx < 314; lx += 8) r(lx, ly, 8, 8, ((lx + ly) / 8) % 2 ? "#2a3552" : "#2d3957");
      r(228, 148, 80, 82, "#3c4b6c"); r(231, 151, 74, 76, "#2c3755");
      r(6, 6, 308, 22, "#27324d"); r(6, 26, 308, 2, "#161d30");
      const sky = night() ? "#1c2a4a" : "#58739f";
      for (let w = 0; w < 3; w++) { r(16 + w * 22, 10, 18, 14, "#0a0f1c"); r(17 + w * 22, 11, 16, 12, sky); r(24 + w * 22, 11, 1, 12, "#0a0f1c"); }
      r(86, 9, 100, 15, "#0a0f1c"); r(87, 10, 98, 13, "#0b1a33");
      for (let b = 0; b < 12; b++) { const bh = 3 + Math.round((Math.sin(t / 14 + b * 0.7) + 1) * 4); r(91 + b * 7, 22 - bh, 4, bh, b === 11 ? "#4cc38a" : "#2d7bc4"); }
      const ca = t / 40; r(206, 10, 14, 14, "#e8eefb"); r(208, 12, 10, 10, "#16213f"); r(213 + Math.round(Math.cos(ca) * 3), 17 + Math.round(Math.sin(ca) * 3), 1, 1, "#e8eefb"); r(213, 17, 1, 1, "#e8eefb");
      r(216, 28, 3, 76, "#3b4a6b"); r(216, 134, 3, 102, "#3b4a6b"); r(219, 28, 1, 76, "#161d30"); r(219, 134, 1, 102, "#161d30");
      r(224, 16, 88, 10, "#3a4660"); r(224, 14, 88, 2, "#6b7a99");
      r(236, 6, 16, 18, "#1b2338"); r(238, 8, 12, 8, "#2d3957"); r(240, 18, 8, 3, "#0a0f1c"); r(243, 10, 2, 2, ((t >> 2) % 2) ? "#4cc38a" : "#1f6b49");
      r(258, 16, 4, 4, "#e8eefb"); r(266, 16, 4, 4, "#e8eefb"); r(274, 16, 4, 4, "#e8eefb");
      r(292, 6, 16, 20, "#8794b3"); r(294, 8, 12, 8, "#c9d4ea"); r(300, 18, 2, 2, "#2d3957");
      r(236, 196, 72, 6, "#2a3550"); r(236, 202, 72, 18, "#4a5b82"); r(238, 204, 34, 14, "#5b6f99"); r(274, 204, 32, 14, "#5b6f99");
      r(256, 166, 32, 16, "#6b4e34"); r(258, 168, 28, 12, "#7a5a3c"); r(262, 171, 4, 4, "#e8eefb"); r(276, 172, 4, 4, "#e8eefb");
      r(296, 50, 12, 10, "#7a5236"); r(292, 36, 20, 16, "#2f8a4a"); r(298, 32, 8, 8, "#3fae6d");
      r(262, 44, 9, 9, "#4a5b82"); r(284, 44, 9, 9, "#4a5b82");
      r(6, 28, 3, 208, "#3b4a6b"); r(311, 28, 3, 208, "#3b4a6b"); r(6, 233, 308, 3, "#3b4a6b");
    }
    function drawMe(t) {
      const m = S.me, s = (t >> 2) % 2, sk = SKINS[m.skin], hr = HAIRS[m.hair], cl = CLOTHS[m.cloth], f = m.gender === "f";
      r(30, 109, 4, 18, "#1b2338"); r(34, 109, 10, 18, "#2a3550");
      r(43, 112 + s, 9, 2, cl); r(43, 122 - s, 9, 2, cl);
      r(36, 112, 7, 12, cl);
      if (f) r(35, 111, 7, 16, hr);
      r(40, 114, 6, 7, sk);
      r(39, 113, 7, 2, hr); r(39, 113, 3, 8, hr);
      if (f) { r(41, 121, 2, 3, hr); r(41, 118, 1, 1, "#f2b84b"); }
      r(44, 116, 1, 1, "#111");
      if (f) r(44, 119, 2, 1, "#c0392b");
    }
    function drawTable(t) {
      r(56, 151, 136, 8, "rgba(0,0,0,.30)");
      r(54, 146, 6, 9, "#2b1c10"); r(180, 146, 6, 9, "#2b1c10"); r(54, 90, 6, 6, "#2b1c10"); r(180, 90, 6, 6, "#2b1c10");
      r(52, 144, 136, 7, "#4a3220"); r(52, 149, 136, 2, "#2b1c10");
      r(52, 88, 136, 57, "#7a5a3c"); r(52, 88, 136, 2, "#b58f60"); r(52, 88, 2, 57, "#a67f52"); r(186, 88, 2, 57, "#5e432a");
      r(55, 92, 130, 49, "#86643f");
      for (let g = 0; g < 7; g++) { r(60 + g * 18, 95 + (g % 3) * 16, 12, 1, "#74553a"); r(66 + g * 18, 99 + ((g + 1) % 3) * 15, 9, 1, "#93714a"); }
      r(56, 110, 128, 16, "#a89f88"); r(57, 111, 126, 14, "#d9d2c0");
      for (let q = 0; q < 12; q++) r(60 + q * 10, 112, 1, 12, "#cbc3ad");
      r(HUB.x - 9, HUB.y - 9, 18, 18, "#2b3654"); r(HUB.x - 6, HUB.y - 6, 12, 12, "#0a0f1c");
      r(HUB.x - 1, HUB.y - 1, 2, 2, ((t >> 2) % 2) ? "#5fb0f0" : "#2d7bc4");
      const rad = 9 + ((t >> 1) % 8);
      ctx.strokeStyle = "rgba(95,176,240," + Math.max(0.05, 0.6 - (rad - 9) * 0.07) + ")"; ctx.lineWidth = 1;
      ctx.strokeRect(HUB.x - rad + 0.5, HUB.y - rad + 0.5, rad * 2, rad * 2);
      r(68, 114, 10, 7, "#e8eefb"); r(69, 115, 8, 5, "#c9d4ea"); r(160, 115, 3, 8, "#e8833a");
      drawMe(t);
    }
    function drawSeatItems(t) {
      list().forEach((a, i) => {
        const top = a.face === "down", ly = top ? 92 : 138;
        r(a.x - 7, ly, 14, 8, "#394664"); r(a.x - 6, ly + (top ? 1 : 0), 12, 6, "#0a0f1c");
        if (!away(a)) {
          for (let l = 0; l < 3; l++) r(a.x - 5, ly + 1 + l * 2, 3 + ((t >> 3) + l * 3 + i) % (a.busy ? 8 : 5), 1, a.busy ? "#5fb0f0" : "#8ad7a0");
          const p = ((t * 0.035) + i * 0.17) % 1;
          r(HUB.x + (a.x - HUB.x) * p, HUB.y + ((top ? 94 : 142) - HUB.y) * p, 2, 2, "#5fb0f0");
          r(a.x + 9, ly + 2, 4, 4, "#e8eefb"); r(a.x + 13, ly + 3, 1, 2, "#e8eefb");
        }
      });
    }

    function render() {
      if (!ctx) return;
      const t = S.tick, fr = t >> 1;
      drawRoom(t); drawTable(t);
      list().forEach((a) => { if (away(a)) { if (a.face === "down") r(a.x - 6, a.y - 9, 12, 12, "#2a3550"); else r(a.x - 6, a.y - 2, 12, 12, "#2a3550"); } });
      drawSeatItems(t);
      list().forEach((a) => { if (seated(a)) sitter(a, t); });
      list().forEach((a) => { if (a.mode === "coffee" && a.wait > 30) r(243, 22 + ((t >> 1) % 4), 2, 3, "#6b4e34"); });
      list().forEach((a) => {
        if (!away(a)) return;
        const p = posAt(a), back = a.mode === "coffee" || (a.mode === "going" ? p.dy < 0 : p.dy > 0);
        walker(Math.round(p.x), Math.round(p.y) + 4, a, back, fr);
        if (a.mode === "coffee") { r(Math.round(p.x) + 4, Math.round(p.y) - 6, 3, 3, "#e8eefb"); r(Math.round(p.x) + 5, Math.round(p.y) - 9 - ((t >> 2) % 3), 1, 2, "rgba(232,238,251,.6)"); }
      });
      list().forEach((a) => {
        const sat = seated(a), p = sat ? null : posAt(a);
        const bx = sat ? a.x : Math.round(p.x), by = sat ? (a.face === "down" ? a.y - 8 : a.y - 4) : Math.round(p.y) - 6;
        if (a.busy) bubble(bx, by, "..", t);
        else if (a.errUntil > t) bubble(bx, by, "!", t);
        else if (a.okUntil > t) bubble(bx, by, "ok", t);
        else if (a.alert && sat) bubble(bx, by, "!", t);
      });
      if (night()) {
        r(0, 0, W, H, "rgba(4,8,24,.4)");
        r(46, 82, 148, 72, "rgba(255,214,120,.09)"); r(224, 8, 90, 30, "rgba(255,214,120,.1)"); r(236, 160, 72, 60, "rgba(255,214,120,.07)");
      }
    }

    // ----- behaviour -----
    function update() {
      const t = ++S.tick;
      list().forEach((a) => {
        if (a.mode === "going") { a.s += 3; if (a.s >= a.total) { a.s = a.total; a.mode = "coffee"; a.wait = 90; } }
        else if (a.mode === "coffee") { if (--a.wait <= 0) a.mode = "back"; }
        else if (a.mode === "back") { a.s -= 3; if (a.s <= 0) { a.s = 0; a.mode = "sit"; S.nextBreak = t + 70 + Math.floor(Math.random() * 90); } }
      });
      if (!list().some(away) && t >= S.nextBreak) {
        const cand = list().filter((a) => a.mode === "sit" && !a.busy && S.openId !== a.id);
        if (cand.length) { const a = cand[Math.floor(Math.random() * cand.length)]; a.mode = "going"; a.s = 0; }
      }
    }
    const motion = !reduceMotion();
    const timer = setInterval(() => {
      if (!motion || document.hidden || !S.visible || !root.isConnected) return;
      update(); render();
      if (S.tick % 10 === 0) refreshStatuses();
    }, 100);
    let io = null;
    if ("IntersectionObserver" in window) { io = new IntersectionObserver((e) => { S.visible = e[0].isIntersecting; }, { threshold: 0.05 }); io.observe(root); }
    const mo = new MutationObserver(() => render());
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

    // ----- accordion -----
    function statusOf(a) {
      if (a.busy) return ["Trabajando en tu pedido", "busy"];
      if (a.mode === "going") return ["Yendo por un café", "coffee"];
      if (a.mode === "coffee") return ["Sirviéndose un café", "coffee"];
      if (a.mode === "back") return ["Volviendo a su puesto", "coffee"];
      if (a.alert) return ["Necesita atención", "alert"];
      return ["Trabajando", ""];
    }
    function swatches(kind, items, names) {
      return items.map((c, i) => '<button type="button" class="office-sw" data-set="' + kind + '" data-v="' + i + '" aria-pressed="' + (S.me[kind] === i) + '" aria-label="' + names[i] + '" title="' + names[i] + '" style="background:' + c + '"></button>').join("");
    }
    function meBody() {
      return '<p class="office-hint">Así te ven los agentes cuando te sentás a la mesa. Los cambios se ven al instante.</p>' +
        '<label class="office-label" for="office-me-name">Nombre</label><input id="office-me-name" class="office-input" type="text" maxlength="12" autocomplete="off" placeholder="Tu nombre" value="' + esc(S.me.name) + '">' +
        '<span class="office-label">Personaje</span><div class="office-row" role="group" aria-label="Personaje"><button type="button" class="btn btn-ghost office-seg" data-gender="m" aria-pressed="' + (S.me.gender === "m") + '">Hombre</button><button type="button" class="btn btn-ghost office-seg" data-gender="f" aria-pressed="' + (S.me.gender === "f") + '">Mujer</button></div>' +
        '<span class="office-label">Cabello</span><div class="office-row">' + swatches("hair", HAIRS, HAIR_NAMES) + '</div>' +
        '<span class="office-label">Piel</span><div class="office-row">' + swatches("skin", SKINS, SKIN_NAMES) + '</div>' +
        '<span class="office-label">Ropa</span><div class="office-row">' + swatches("cloth", CLOTHS, CLOTH_NAMES) + '</div>';
    }
    function liveBlock(a) {
      if (a.id === "con") {
        const h = state.lastConnectorHealth || {};
        return '<ul class="office-conns">' + CONNECTOR_STATUS_PROVIDERS.map((p) => {
          const info = h[p] || {}, label = PROVIDER_CONNECT_CONFIG[p] ? PROVIDER_CONNECT_CONFIG[p].label : p;
          const bad = info.last_sync_status === "error" || (!info.connected && info.last_error);
          const cls = bad ? "bad" : info.connected ? "ok" : "off";
          const txt = bad ? "Con error" : info.connected ? (info.last_synced_at ? "Sincronizada " + new Date(info.last_synced_at).toLocaleDateString("es-AR") : "Conectada") : "Sin conectar";
          const btn = !info.connected && PROVIDER_CONNECT_CONFIG[p] ? ' <button type="button" class="btn btn-ghost office-mini" data-connect="' + p + '">' + (bad ? "Reconectar" : "Conectar") + "</button>" : "";
          return '<li><span class="office-pill ' + cls + '">' + esc(txt) + "</span> <b>" + esc(label) + "</b>" + btn + "</li>";
        }).join("") + "</ul>";
      }
      if (a.id === "fin" && state.lastSummary) {
        const s = state.lastSummary;
        return '<dl class="office-kpis"><div><dt>True ROAS</dt><dd>' + (s.true_roas == null ? "—" : esc(s.true_roas) + "x") + "</dd></div><div><dt>Ganancia real</dt><dd>" + esc(money(s.real_profit_after_ads)) + "</dd></div><div><dt>Gasto en anuncios</dt><dd>" + esc(money(s.total_ad_spend)) + "</dd></div></dl>";
      }
      return "";
    }
    function resultBlock(a) {
      const x = a.result; if (!x) return "";
      let h = '<div class="office-result ' + (x.ok ? "" : "err") + '" role="status"><p>' + esc(x.text) + "</p>";
      if (x.lines && x.lines.length) h += "<ul>" + x.lines.map((l) => "<li>" + esc(l) + "</li>").join("") + "</ul>";
      if (x.preview) h += '<pre class="office-preview">' + esc(x.preview) + "</pre>";
      return h + "</div>";
    }
    function agentBody(a) {
      const blocked = !state.activeStoreId;
      const tasks = (TASKS[a.id] || []).map((tk, i) => {
        const noWrite = tk.write && !canWrite();
        const dis = a.busy || blocked || noWrite;
        const why = noWrite ? ' title="Hace falta ser propietario o admin, y no estar en la cuenta de demo."' : "";
        return '<button type="button" class="btn btn-primary office-task" data-task="' + i + '"' + (dis ? " disabled" : "") + why + ">" + esc(tk.label) + "</button>";
      }).join("");
      const logHtml = a.log.length ? '<ul class="office-log">' + a.log.slice(0, 5).map((l) => "<li><time>" + esc(l.t) + "</time> " + esc(l.m) + "</li>").join("") + "</ul>" : '<p class="office-hint">Todavía no hizo nada en esta sesión.</p>';
      return '<p class="office-hint"><b>Módulo ' + esc(a.mod) + '</b> · mide: ' + esc(a.mide) + '</p><p class="office-hint">' + esc(a.role) + "</p>" + (away(a) ? '<p class="office-hint">Está fuera de su puesto. Si le pedís algo, vuelve antes de terminar el café.</p>' : "") +
        '<div class="office-row">' + tasks + "</div>" + liveBlock(a) + '<div class="office-result-slot">' + resultBlock(a) + '</div><span class="office-label">Actividad reciente</span>' + logHtml;
    }
    function buildAcc() {
      const items = [{ id: "me", name: "Tu lugar", sub: meName() || "Tu perfil", dot: "#8fbbe8" }].concat(list().map((a) => ({ id: a.id, name: a.name, sub: statusOf(a)[0], dot: a.hue, st: statusOf(a)[1] })));
      acc.innerHTML = items.map((it) => {
        const open = S.openId === it.id;
        return '<section class="acc-item" data-id="' + it.id + '"><h4><button type="button" class="acc-head" id="acc-h-' + it.id + '" aria-expanded="' + open + '" aria-controls="acc-b-' + it.id + '"><span class="acc-dot ' + (it.st || "") + '" style="' + (it.st === "alert" ? "" : "background:" + (S.color ? it.dot : "#9fb7d9")) + '"></span><span class="acc-name">' + esc(it.name) + '</span><span class="acc-sub">' + esc(it.sub) + '</span><span class="acc-chev" aria-hidden="true"></span></button></h4>' +
          '<div class="acc-body" id="acc-b-' + it.id + '" role="region" aria-labelledby="acc-h-' + it.id + '"' + (open ? "" : " hidden") + ">" + (open ? (it.id === "me" ? meBody() : agentBody(agents[it.id])) : "") + "</div></section>";
      }).join("");
    }
    let lastStatusKey = "";
    function refreshStatuses() {
      const key = list().map((a) => statusOf(a)[0] + statusOf(a)[1]).join("|") + meName() + S.color;
      if (key === lastStatusKey) return;
      lastStatusKey = key;
      list().forEach((a) => {
        const head = acc.querySelector('[data-id="' + a.id + '"] .acc-head'); if (!head) return;
        const st = statusOf(a);
        head.querySelector(".acc-sub").textContent = st[0];
        const dot = head.querySelector(".acc-dot"); dot.className = "acc-dot " + st[1]; dot.style.background = st[1] === "alert" ? "" : (S.color ? a.hue : "#9fb7d9");
      });
      const meSub = acc.querySelector('[data-id="me"] .acc-sub'); if (meSub) meSub.textContent = meName() || "Tu perfil";
    }
    function rerenderOpenBody() {
      if (!S.openId || S.openId === "me") return;
      const b = acc.querySelector("#acc-b-" + S.openId); if (!b) return;
      b.innerHTML = agentBody(agents[S.openId]);
    }
    function open(id) {
      S.openId = S.openId === id ? null : id;
      lastStatusKey = ""; buildAcc();
      if (S.openId) { const h = acc.querySelector("#acc-h-" + S.openId); if (h && h.scrollIntoView) h.scrollIntoView({ block: "nearest", behavior: motion ? "smooth" : "auto" }); }
    }

    // ----- running a task -----
    const nowStr = () => { const d = new Date(); return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0"); };
    function waitSeated(a) {
      return new Promise((resolve) => {
        if (a.mode === "going" || a.mode === "coffee") a.mode = "back";
        if (!motion) { a.mode = "sit"; a.s = 0; return resolve(); }
        const iv = setInterval(() => { if (a.mode === "sit" || !root.isConnected) { clearInterval(iv); resolve(); } }, 150);
      });
    }
    async function runTask(a, idx) {
      const tk = TASKS[a.id][idx]; if (!tk || a.busy) return;
      const storeAtStart = state.activeStoreId;
      a.result = null; a.busy = true; rerenderOpenBody(); lastStatusKey = ""; refreshStatuses();
      await waitSeated(a);
      if (!root.isConnected) return;
      let ok = true, out;
      try { out = await tk.run(); }
      catch (err) { ok = false; out = { text: "No se pudo completar: " + (err && err.message ? err.message : "error desconocido") + "." }; }
      if (!root.isConnected || state.activeStoreId !== storeAtStart) return;
      a.busy = false; a.result = Object.assign({ ok }, out);
      (ok ? (a.okUntil = S.tick + 24) : (a.errUntil = S.tick + 30));
      a.log.unshift({ t: nowStr(), m: (ok ? "" : "Falló: ") + tk.label }); if (a.log.length > 8) a.log.pop();
      if (a.id === "con") a.alert = Object.keys(state.lastConnectorHealth || {}).some((k) => { const h = state.lastConnectorHealth[k]; return h.last_sync_status === "error" || (!h.connected && h.last_error); });
      rerenderOpenBody(); lastStatusKey = ""; refreshStatuses(); render();
    }

    // ----- events -----
    acc.addEventListener("click", (e) => {
      const head = e.target.closest(".acc-head");
      if (head) return open(head.closest(".acc-item").dataset.id);
      const task = e.target.closest("[data-task]");
      if (task) return runTask(agents[task.closest(".acc-item").dataset.id], Number(task.dataset.task));
      const conn = e.target.closest("[data-connect]");
      if (conn) return openConnectModal(conn.dataset.connect);
      const g = e.target.closest("[data-gender]");
      if (g) { S.me.gender = g.dataset.gender; saveMe(); refreshMeBody(); render(); return; }
      const sw = e.target.closest("[data-set]");
      if (sw) { S.me[sw.dataset.set] = Number(sw.dataset.v); saveMe(); refreshMeBody(); render(); }
    });
    acc.addEventListener("input", (e) => {
      if (e.target.id !== "office-me-name") return;
      S.me.name = e.target.value.slice(0, 12); saveMe(); updateMeTag(); lastStatusKey = ""; refreshStatuses();
    });
    function refreshMeBody() { const b = acc.querySelector("#acc-b-me"); if (b && !b.hidden) { const had = document.activeElement && document.activeElement.id; b.innerHTML = meBody(); if (had) { const el = b.querySelector("#" + had); if (el) el.focus(); } } }
    function saveMe() { try { localStorage.setItem(ME_KEY, JSON.stringify(S.me)); } catch (e) { /* not persisted */ } }
    modeBtn.addEventListener("click", () => {
      S.color = !S.color; try { localStorage.setItem(COLOR_KEY, S.color ? "color" : "sober"); } catch (e) { /* not persisted */ }
      updateModeBtn(); lastStatusKey = ""; refreshStatuses(); render();
    });
    cv.addEventListener("click", (e) => {
      const rc = cv.getBoundingClientRect(), x = (e.clientX - rc.left) / rc.width * W, y = (e.clientY - rc.top) / rc.height * H;
      let best = null, bd = 14;
      list().forEach((a) => { const p = seated(a) ? { x: a.x, y: a.y } : posAt(a); const d = Math.hypot(p.x - x, p.y - 4 - y); if (d < bd) { bd = d; best = a.id; } });
      if (Math.hypot(40 - x, 118 - y) < 14) best = "me";
      if (best && S.openId !== best) open(best);
      else if (best) acc.querySelector("#acc-h-" + best).focus();
    });

    // the connector list reads the health the dashboard already fetched; refresh the agent's state from it
    const h0 = state.lastConnectorHealth || {};
    agents.con.alert = Object.keys(h0).some((k) => h0[k].last_sync_status === "error" || (!h0[k].connected && h0[k].last_error));

    buildAcc(); render(); refreshStatuses();

    return {
      destroy() { clearInterval(timer); if (io) io.disconnect(); mo.disconnect(); },
      refresh() { rerenderOpenBody(); render(); },
    };
  }

  // Called by app.js when connector health or metrics arrive, so an open panel shows the new data.
  function refresh() { if (inst) inst.refresh(); }

  window.Office = { mount, unmount, refresh };
})();
