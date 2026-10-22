/* Panel RCE — page logic. Plain JS; talks to /api (panel/web.py); charts with uPlot. */
(function () {
  "use strict";
  const $ = (s, el) => (el || document).querySelector(s);
  const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));

  const MODE_ES = {cold: "Modo frío", hot: "Modo calor", parada: "Parada", automatic: "Automático", unknown: "Desconocido"};
  const MODE_ICO = {cold: "❄", hot: "☀", parada: "⏸", automatic: "A", unknown: "?"};
  const COVER_ES = {open: "Tapa abierta", closed: "Tapa cerrada", moving: "Tapa en movimiento", plc: "Tapa: la mueve el PLC", unknown: "Tapa: desconocida", error: "Tapa: error de finales de carrera"};
  const COVER_ICO = {open: "▲", closed: "▼", moving: "⇅", plc: "A", unknown: "?", error: "!"};
  const STATE_ES = {ColdMode1: "modo frío", ColdMode2: "modo frío (2)", HeatMode1: "modo calor", HeatMode2: "modo calor (2)", Parada: "parada", AutomaticMode: "automático", null: "desconocido", "": "desconocido"};
  const ACT = {b1: "Bomba B1", b2: "Bomba B2", ev1: "Válvula EV1", ev2: "Válvula EV2"};
  const ACT_ES = {on: "en marcha", off: "parada", auto: "automático", "": "?"};
  const LIGHT_TXT = {ok: "en orden", bad: "problema", stopped: "parado", missing: "no instalado", unknown: "sin datos"};

  let me = null, role = null, status = null, refreshTimer = null, cycleTimer = null, charts = {};
  const ROLE_ES = {admin: "superadministración", team: "equipo", viewer: "solo visualización"};

  // ------------------------------------------------------------------ http
  async function api(path, opts) {
    const r = await fetch(path, Object.assign({headers: {"Content-Type": "application/json"}, credentials: "same-origin"}, opts || {}));
    let body = null;
    try { body = await r.json(); } catch (e) { body = null; }
    if (r.status === 401) { showLogin(); throw new Error("sin sesión"); }
    if (!r.ok) {
      const msg = (body && (body.detail || body.message)) || ("Error " + r.status);
      if (r.status === 423 && body && body.lock) toast(`${msg} (${body.lock.name} desde ${body.lock.host})`, "bad");
      else toast(typeof msg === "string" ? msg : JSON.stringify(msg), "bad");
      const err = new Error(msg); err.status = r.status; err.body = body; throw err;
    }
    return body;
  }
  const post = (path, data) => api(path, {method: "POST", body: JSON.stringify(data || {})});

  function toast(text, kind) {
    const t = $("#toast"); t.textContent = text; t.className = "toast " + (kind || ""); t.hidden = false;
    clearTimeout(t._h); t._h = setTimeout(() => { t.hidden = true; }, 4500);
  }
  const fmtTs = (s) => s ? s.replace("T", " ").slice(5, 16) : "—";
  const fmtAge = (s) => s == null ? "—" : s < 90 ? `hace ${Math.round(s)} s` : s < 5400 ? `hace ${Math.round(s / 60)} min` : `hace ${(s / 3600).toFixed(1)} h`;
  const fmtNum = (v, d) => (v == null || isNaN(v)) ? "—" : Number(v).toFixed(d == null ? 1 : d);

  // ------------------------------------------------------------------ login
  function showLogin(msg) {
    $("#login").hidden = false;
    if (msg) $("#login-msg").textContent = msg;
  }
  async function checkSession() {
    const r = await api("/api/me");
    me = r.name; role = r.role;
    if (!r.admin_exists) {
      $("#login-msg").textContent = "Primero hay que crear la cuenta de superadministración rce_admin (solo desde el PC del panel), con una contraseña de 4 a 32 caracteres.";
      $("#login-name").value = "rce_admin"; $("#login-name").readOnly = true;
      $("#login-btn").textContent = "Crear rce_admin y entrar"; $("#login-form").dataset.setup = "1";
      showLogin();
      return false;
    }
    if (!me) { showLogin(); return false; }
    $("#login").hidden = true;
    $("#user-chip").textContent = me;
    if (r.must_change) { showPwChange(true); return false; }
    applyRole();
    return true;
  }
  function applyRole() {
    document.body.dataset.role = role || "";
    $("#btn-lock").hidden = role === "viewer";
  }
  function showPwChange(forced, current) {
    $("#pwchange").hidden = false; $("#pwchange-error").textContent = "";
    $("#pwchange-msg").textContent = forced
      ? "Es tu primera entrada (o te han puesto una contraseña nueva): elige tu contraseña para seguir. De 4 a 32 caracteres y distinta de tu usuario."
      : "Elige una contraseña nueva (de 4 a 32 caracteres; distinta de tu usuario).";
    $("#pwc-current").value = current || ""; $("#pwc-new").value = ""; $("#pwc-new2").value = "";
    $("#pwc-cancel").hidden = !!forced;
    $("#pwchange").dataset.forced = forced ? "1" : "";
    (current ? $("#pwc-new") : $("#pwc-current")).focus();
  }
  $("#pwc-cancel").addEventListener("click", () => { $("#pwchange").hidden = true; });
  $("#pwchange-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if ($("#pwc-new").value !== $("#pwc-new2").value) { $("#pwchange-error").textContent = "Las dos contraseñas nuevas no coinciden"; return; }
    try {
      await post("/api/password", {current: $("#pwc-current").value, new: $("#pwc-new").value});
      const forced = $("#pwchange").dataset.forced; $("#pwchange").hidden = true; toast("Contraseña cambiada", "ok");
      if (forced) { applyRole(); start(); }
    } catch (e) { $("#pwchange-error").textContent = e.message; }
  });
  $("#login-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const name = $("#login-name").value.trim(), pin = $("#login-pin").value;
    $("#login-error").textContent = "";
    try {
      if ($("#login-form").dataset.setup) { await post("/api/setup", {name, pin}); delete $("#login-form").dataset.setup; $("#login-btn").textContent = "Entrar"; $("#login-name").readOnly = false; }
      const r = await post("/api/login", {name, pin});
      $("#login").hidden = true; me = r.name; role = r.role; $("#user-chip").textContent = me;
      if (r.must_change) { showPwChange(true, pin); return; }
      applyRole();
      start();
    } catch (e) { $("#login-error").textContent = e.message; }
  });
  $("#btn-logout").addEventListener("click", async () => { await post("/api/logout"); location.reload(); });

  // ------------------------------------------------------------------ tabs
  $$("#tabs button").forEach((b) => b.addEventListener("click", () => {
    $$("#tabs button").forEach((x) => x.classList.toggle("active", x === b));
    $$(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + b.dataset.tab));
    try { localStorage.setItem("panel.tab", b.dataset.tab); } catch (e) {}
    onTab(b.dataset.tab);
  }));
  function onTab(name) {
    if (name === "lecturas") loadReadings();
    if (name === "control" && status) renderControl(status);
    if (name === "historial") loadHistory();
    if (name === "diario") loadDiary();
    if (name === "auditoria") loadAudit(true);
    if (name === "agenda") loadAgenda();
    if (name === "ajustes") loadSettings();
    if (name === "simulador") {
      if (!$("#sim-now").value) $("#sim-now").value = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16);
      if (!$("#simf-cold").value) fillSimFromStatus();
      if (!$("#replay-date").value) $("#replay-date").value = new Date(Date.now() - 86400000 - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
    }
  }

  // ------------------------------------------------------------------ status (Inicio + top bar)
  async function loadStatus() {
    try { status = await api("/api/status"); } catch (e) { return; }
    renderTop(status); renderInicio(status); renderMandosCheck(status);
    clearTimeout(refreshTimer); refreshTimer = setTimeout(loadStatus, (status.refresh_s || 10) * 1000);
  }
  function renderTop(s) {
    const badge = $("#mode-badge");
    badge.textContent = s.mode === "real" ? "REAL" : "SOMBRA"; badge.classList.toggle("real", s.mode === "real");
    $("#top-wincc").textContent = "WinCC: " + (s.wincc.last_write ? `${fmtTs(s.wincc.last_write)} (${fmtAge(s.wincc.age_s)})` : "sin datos");
    const lock = s.lock, chip = $("#lock-chip"), btn = $("#btn-lock");
    if (!lock) { chip.textContent = "Nadie al mando"; chip.className = "chip"; btn.textContent = "Tomar el mando"; btn.disabled = false; }
    else if (lock.name === s.me) { chip.textContent = "Tienes el mando"; chip.className = "chip mine"; btn.textContent = "Soltar el mando"; btn.disabled = false; }
    else { chip.textContent = `Al mando: ${lock.name} (${lock.host})`; chip.className = "chip other"; btn.textContent = "Tomar el mando"; btn.disabled = true; }
    clearInterval(cycleTimer);
    const next = s.control && s.control.next_cycle_time ? new Date(s.control.next_cycle_time.replace(" ", "T")) : null;
    const tick = () => {
      if (!next || !s.supervisor.wanted) { $("#top-cycle").textContent = "Siguiente ciclo: —"; return; }
      const d = Math.round((next - Date.now()) / 1000);
      $("#top-cycle").textContent = d >= 0 ? `Siguiente ciclo en ${Math.floor(d / 60)}:${String(d % 60).padStart(2, "0")}` : `Ciclo previsto a las ${fmtTs(s.control.next_cycle_time).slice(6)} (retrasado ${Math.floor(-d / 60)} min)`;
    };
    tick(); cycleTimer = setInterval(tick, 1000);
  }
  function actuators(p, el) {
    el.innerHTML = Object.keys(ACT).map((k) => `<span class="act ${esc(p && p[k] || "")}">${ACT[k]}: ${ACT_ES[p && p[k]] || "?"}</span>`).join("");
  }
  function renderInicio(s) {
    let plcEl = $(".light[data-k=plc]");
    if (s.plc && !plcEl) { $("#lights").insertAdjacentHTML("beforeend", '<div class="light" data-k="plc"><div class="dot"></div><div class="lt">PLC</div><div class="ls">—</div></div>'); plcEl = $(".light[data-k=plc]"); }
    if (!s.plc && plcEl) plcEl.remove();
    if (s.plc) { plcEl.className = "light " + (s.lights.plc === "alarm" ? "missing" : s.lights.plc); $(".ls", plcEl).textContent = s.plc.ok ? `${s.plc_ip} · ${MODE_ES[s.plc.plc_mode] || s.plc.state || "?"} · ${COVER_ES[s.plc.cover] || s.plc.cover}${s.lights.plc === "alarm" ? " · ALARMA lluvia/viento" : ""}` : "no responde"; }
    for (const k of ["wincc", "api", "sc", "filebeat"]) {
      const el = $(`.light[data-k=${k}]`), v = s.lights[k];
      el.className = "light " + v;
      let txt = LIGHT_TXT[v] || v;
      if (k === "wincc" && s.wincc.last_write) txt = fmtAge(s.wincc.age_s);
      if (k === "sc" && s.supervisor.sc && s.supervisor.sc.running) txt = `en marcha${s.heartbeat_age_s != null ? ", latido " + fmtAge(s.heartbeat_age_s) : ""}`;
      $(".ls", el).textContent = txt;
    }
    const c = s.control || {};
    const mode = c.plc_mode || "unknown", cover = c.cover || "unknown";
    const sm = $("#st-mode"); sm.className = "big " + mode; $(".ico", sm).textContent = MODE_ICO[mode] || "?"; $("span:last-child", sm).textContent = MODE_ES[mode] || mode;
    const sc = $("#st-cover"); sc.className = "big " + cover; $(".ico", sc).textContent = COVER_ICO[cover] || "?"; $("span:last-child", sc).textContent = COVER_ES[cover] || cover;
    actuators(c.pumps, $("#st-pumps"));
    const tag = $("#state-tag"); tag.textContent = s.mode === "real" ? "aplicado al PLC" : "estado simulado"; tag.className = "tag " + s.mode;
    const ag = s.agenda || {}, agEl = $("#st-agenda");
    const agText = ag.running ? `Agenda: ${ag.running.label} hasta las ${ag.running.end.slice(11, 16)} (${ag.running.end.slice(8, 10)}/${ag.running.end.slice(5, 7)}) · #${ag.running.id}, ${ag.running.author}`
      : ag.next ? `Próximo en la agenda: ${ag.next.label}, ${ag.next.start.slice(8, 10)}/${ag.next.start.slice(5, 7)} a las ${ag.next.start.slice(11, 16)}` : "";
    agEl.hidden = !agText; agEl.textContent = agText; agEl.classList.toggle("running", !!ag.running);
    $("#st-since").textContent = c.cycle_time ? `Último ciclo: ${fmtTs(c.cycle_time)}${c.paused ? ` · control en pausa (${c.paused_by})` : ""}${c.cover_measured ? ` · tapa medida: ${COVER_ES[c.cover_measured] || c.cover_measured}` : ""}` : "Sin ciclos del control todavía";
    const d = c.decision || {};
    if (c.cycle_time) {
      const act = {si: "Sí: cambiar de modo", no: "No: mantener", parada: "Parada", "": "sin decisión"}[d.action] || d.action;
      const why = (d.reasons || []).slice(0, 3).map((x) => `<li>${esc(x)}</li>`).join("");
      $("#decision").innerHTML = `<div class="when">${esc(fmtTs(c.cycle_time))}</div><div><b>${esc(act)}</b> → ${esc(STATE_ES[d.next_state] || d.next_state || "")}${c.paused ? " <i>(no aplicado: control en pausa)</i>" : ""}</div>` +
        (why ? `<ul class="reasons small">${why}</ul>` : "") +
        `<div class="muted">Franja activa: ${d.in_time_frame === true ? "sí" : d.in_time_frame === false ? "no" : "—"} · producción prevista ahora: calor ${fmtNum(d.hot_prod / 3.6e6, 2)} kWh, frío ${fmtNum(d.cold_prod / 3.6e6, 2)} kWh · <a href="#" data-goto="control">ver el plan</a></div>`;
      $("#decision a[data-goto]").addEventListener("click", (ev) => { ev.preventDefault(); $("#tabs button[data-tab=control]").click(); });
      const p = c.plan || {};
      $("#plan").textContent = `Plan: demanda pendiente ${fmtNum((p.target_demand_j || 0) / 3.6e6, 2)} kWh · franjas elegidas: ${(p.selected_frames || []).length} · producción prevista del plan: ${fmtNum((p.predicted_production_j || 0) / 3.6e6, 2)} kWh`;
    } else { $("#decision").textContent = "El control todavía no ha decidido nada."; $("#plan").textContent = ""; }
    const w = s.supervisor.wanted;
    const viewer = role === "viewer";
    $("#btn-start").disabled = w || viewer; $("#btn-stop").disabled = !w || viewer; $("#btn-restart").disabled = !w || viewer;
    $("#btn-fb-restart").hidden = s.lights.filebeat !== "bad" || viewer;
    const retry = (p) => (!p.running && p.next_restart_in_s != null) ? `, código ${p.last_exit_code}, reintento en ${p.next_restart_in_s} s` : "";
    $("#sup-info").textContent = w ? `Sistema en marcha desde ${fmtTs(s.supervisor.wanted_since)} · predicción ${s.supervisor.api.running ? "activa" : "parada"} (${s.supervisor.api.restarts} reinicios${retry(s.supervisor.api)}) · control ${s.supervisor.sc.running ? "activo" : "parado"} (${s.supervisor.sc.restarts} reinicios${retry(s.supervisor.sc)})` : "Sistema parado";
    const ul = $("#alerts");
    ul.innerHTML = s.alerts.length ? s.alerts.map((a) => `<li class="${a.level}"><span class="t">${esc(a.title)}</span><span class="a">→ ${esc(a.action)}</span>${a.detail ? `<span class="d">${esc(a.detail)}</span>` : ""}</li>`).join("") : '<li class="ok">Todo en orden</li>';
    $("#main-readings").innerHTML = s.readings.map((r) => `<div class="rd ${r.stale ? "stale" : r.frozen ? "frozen" : ""}"><div class="l">${esc(r.label)}${r.system === "RCE-b" ? " (B)" : ""}</div><div><span class="v">${fmtNum(r.value, r.unit === "W/m²" ? 0 : 1)}</span><span class="u">${esc(r.unit)}</span></div><div class="t">${esc(fmtTs(r.ts))} ${r.stale ? "· sin lecturas" : r.frozen ? "· sin cambios" : ""}</div></div>`).join("");
  }
  // ------------------------------------------------------------------ Control (plan y previsiones)
  function renderControl(s) {
    const c = s.control || {}, p = c.plan || {}, f = c.forecast || {}, d = c.decision || {};
    const frames = (p.selected_frames || []).slice().sort();
    $("#ctl-plan").innerHTML = [
      ["Demanda pendiente", `${fmtNum((p.target_demand_j || 0) / 3.6e6, 2)} kWh`],
      ["Producción prevista del plan", `${fmtNum((p.predicted_production_j || 0) / 3.6e6, 2)} kWh` + (p.predicted_production_j === -1 ? " (insuficiente: no se encontró plan)" : "")],
      ["Franjas planificadas", frames.length ? `${frames.length} franjas de 15 min · primera ${fmtTs(frames[0])} · última ${fmtTs(frames[frames.length - 1])}` : "ninguna"],
      ["Previsiones actualizadas", f.updated_day != null ? `día ${f.updated_day}` : "—"],
      ["Último ciclo", c.cycle_time ? fmtTs(c.cycle_time) : "—"],
    ].map(([k, v]) => `<b>${esc(k)}</b><span>${esc(v)}</span>`).join("");
    const sch = c.schedule;
    $("#ctl-schedule").textContent = sch ? `Horario semanal del PLC simulado: calor ${sch.heat_start.slice(0, 5)}–${sch.heat_end.slice(0, 5)} · parada hasta ${sch.parada_end.slice(0, 5)} · el resto frío · fines de semana parada. El control no lo usa para decidir: con «sí» pone el modo de la franja planificada.` : "En modo real el horario se lee del PLC (DB80/83).";
    $("#ctl-reasons").innerHTML = (d.reasons || []).length ? d.reasons.map((x) => `<li>${esc(x)}</li>`).join("") : "<li class='muted'>Sin decisión todavía</li>";
    const tNow = Date.parse((s.now || "").replace(" ", "T")) / 1000;
    const kv = (obj, parse) => Object.entries(obj || {}).map(([k, v]) => [parse(k), v / 3.6e6]).filter((x) => !isNaN(x[0])).sort((a, b) => a[0] - b[0]);
    const p15 = (k) => Date.parse(k.replace(" ", "T") + (k.length === 16 ? ":00" : "")) / 1000;
    const ph = (k) => Date.parse(k) / 1000;
    const prodHot = kv(f.prod_hot, p15), prodCold = kv(f.prod_cold, p15);
    const frameSeries = frames.map((k) => [p15(k.length > 16 ? k.slice(0, 16) : k), 1]);
    const toPts = (arr) => arr.map(([t, v]) => [new Date(t * 1000).toISOString(), v]);
    const span = prodHot.concat(prodCold);
    const xr = span.length ? [Math.min(tNow, span[0][0]) - 1800, span[span.length - 1][0] + 1800] : [tNow - 3600, tNow + 48 * 3600];
    lineChart("ch-prod", [["Calor previsto", toPts(prodHot), "#c2410c"], ["Frío previsto", toPts(prodCold), "#0b5fa5"], ["Franja planificada", toPts(frameSeries), "#1e7a46"]], "kWh / 15 min", {xr, iso: true});
    lineChart("ch-dem", [["Demanda de calor", toPts(kv(f.demand_hot, ph)), "#c2410c"], ["Demanda de frío", toPts(kv(f.demand_cold, ph)), "#0b5fa5"]], "kWh / h", {xr, iso: true, step: true});
    const rain = f.rain && typeof f.rain === "object" ? Object.entries(f.rain) : [];
    $("#ctl-rain").textContent = rain.length ? rain.map(([k, v]) => `${k}: ${typeof v === "number" ? fmtNum(v, 1) + " mm" : JSON.stringify(v)}`).join(" · ") : "Sin previsión de lluvia en el estado del control";
  }

  $("#btn-start").addEventListener("click", () => act("/api/system/start", "Arrancando…"));
  $("#btn-stop").addEventListener("click", () => confirm("¿Parar el control y la predicción?") && act("/api/system/stop", "Parando…"));
  $("#btn-restart").addEventListener("click", () => act("/api/system/restart", "Reiniciando…"));
  $("#btn-fb-restart").addEventListener("click", async () => {
    try { const r = await post("/api/filebeat/restart"); toast(r.ok ? "Envío a Kibana reiniciado" : "No se pudo reiniciar el envío: avisar a Vítor", r.ok ? "ok" : "bad"); await loadStatus(); } catch (e) {}
  });
  async function act(path, msg) { try { toast(msg); await post(path); await loadStatus(); } catch (e) {} }
  $("#btn-lock").addEventListener("click", async () => {
    try {
      if (status && status.lock && status.lock.name === me) await post("/api/lock/release"); else await post("/api/lock/take");
      await loadStatus();
    } catch (e) {}
  });

  // ------------------------------------------------------------------ Lecturas
  let showAll = false;
  async function loadReadings() {
    const hours = $("#lect-hours").value;
    let d; try { d = await api(`/api/readings?hours=${hours}`); } catch (e) { return; }
    $("#lect-info").textContent = `${d.readings.length} series · ${fmtTs(d.now)}`;
    const tb = $("#lect-table tbody");
    const MAIN = ["t_cold_tank_c", "t_hot_tank_c", "t_circuit_2_c", "t_circuit_4_c", "flow_rce_l_min", "ir_net_w_m2", "solar_global_w_m2", "t_ambient_c", "rh_ambient_pct", "wind_speed_m_s",
                  "t_cold_tank_l1_c", "t_cold_tank_l2_c", "t_cold_tank_l3_c", "t_surface_1_c", "t_surface_2_c", "t_surface_3_c", "t_surface_4_c", "t_surface_5_c", "t_surface_6_c", "p_circuit_1_bar", "p_circuit_2_bar"];
    const nOther = d.readings.filter((r) => !MAIN.includes(r.variable)).length;
    tb.innerHTML = d.readings.map((r, i) => `<tr class="${MAIN.includes(r.variable) ? "" : "other"}" ${MAIN.includes(r.variable) || showAll ? "" : "hidden"}><td>${esc(r.label)}${r.system === "RCE-b" ? " (B)" : ""}<div class="muted">${esc(r.variable)}</div></td><td class="num">${fmtNum(r.value, r.unit === "W/m²" ? 0 : 2)}</td><td>${esc(fmtTs(r.ts))}<div class="muted">${fmtAge(r.age_s)}</div></td><td>${esc(r.unit)}</td><td><div class="spark" id="sp-${i}"></div></td><td>${r.stale ? '<span class="st-bad">sin lecturas</span>' : r.frozen ? '<span class="st-warn">sin cambios</span>' : !r.valid ? '<span class="st-warn">no válida</span>' : '<span class="st-ok">ok</span>'}</td><td class="muted">${esc(r.source)}<br>${esc(r.tag)}</td></tr>`).join("");
    tb.insertAdjacentHTML("beforeend", `<tr id="lect-more-row"><td colspan="7"><button class="btn small" id="btn-lect-more">${showAll ? "Ocultar" : "Mostrar"} las otras ${nOther} series (contadores de energía, fases, sondas auxiliares…)</button></td></tr>`);
    $("#btn-lect-more").addEventListener("click", () => { showAll = !showAll; loadReadings(); });
    d.readings.forEach((r, i) => { if (MAIN.includes(r.variable) || showAll) sparkline($("#sp-" + i), r.points); });
    const u = d.used_by_control || {};
    $("#used-by-control").innerHTML = Object.keys(u).length ? Object.entries(u).map(([k, v]) => `<b>${esc(k)}</b><span>${fmtNum(v, 2)}</span>`).join("") : "<span class='muted'>Sin ciclos del control todavía</span>";
  }
  $("#btn-lect-refresh").addEventListener("click", loadReadings);
  $("#lect-hours").addEventListener("change", loadReadings);
  function sparkline(el, points) {
    if (!el || !points || points.length < 2) return;
    const xs = points.map((p) => Date.parse(p[0].replace(" ", "T")) / 1000), ys = points.map((p) => p[1]);
    new uPlot({width: 160, height: 36, cursor: {show: false}, legend: {show: false}, axes: [{show: false}, {show: false}], scales: {x: {time: true}},
      series: [{}, {stroke: "#0b4f6c", width: 1.5}]}, [xs, ys], el);
  }

  // ------------------------------------------------------------------ Mandos
  function renderMandosCheck(s) {
    const mine = s.lock && s.lock.name === s.me;
    const paused = s.control && s.control.paused;
    $("#chk-pause").checked = !!paused;
    $("#mandos-mode").textContent = s.mode === "real" ? "REAL: escribe en el PLC" : "SOMBRA: en seco"; $("#mandos-mode").className = "tag " + s.mode;
    const wm = $("#tab-mandos .watermark"); wm.dataset.wm = s.mode === "real" ? "REAL" : "SIMULADO";
    $("#mandos-help").textContent = s.mode === "real" ? "MODO REAL: cada mando escribe en el PLC. Hace falta tener el mando y el control automático en pausa. La secuencia se muestra paso a paso y se puede abortar." : "En modo SOMBRA los mandos se ejecutan en seco: se muestra la secuencia exacta de escrituras que se harían en el PLC y, si lo marcas, se aplica el resultado al estado virtual del control. No se toca el PLC.";
    if (s.job && s.job.status === "running" && !jobTimer) renderJob(s.job);
    $("#mandos-check").innerHTML = [
      [mine, mine ? "Tienes el mando" : "Hace falta tomar el mando"],
      [s.supervisor.wanted, s.supervisor.wanted ? "Control en marcha" : "Control parado (los mandos en seco funcionan igual)"],
      [paused || s.mode !== "real", paused ? "Control automático en pausa" : "Control automático activo"],
      [s.mode !== "real", s.mode === "real" ? "Modo REAL: los mandos escriben en el PLC" : "Modo SOMBRA: nada toca el PLC"],
      ...(s.mode === "real" ? [[s.plc && s.plc.ok, s.plc && s.plc.ok ? `PLC ${s.plc_ip} responde` : "El PLC no responde"], [!(s.lights.plc === "alarm"), s.lights.plc === "alarm" ? "Alarma de lluvia o viento: no se puede abrir" : "Sin alarma de lluvia o viento"]] : []),
    ].map(([ok, t]) => `<li class="${ok ? "yes" : "no"}">${ok ? "✔" : "✖"} ${esc(t)}</li>`).join("");
    $$(".btn.cmd").forEach((b) => { b.disabled = !mine || (s.mode === "real" && (!paused || !(s.plc && s.plc.ok) || (s.job && s.job.status === "running"))); });
    $("#chk-apply").parentElement.style.display = s.mode === "real" ? "none" : "";
  }
  $("#chk-pause").addEventListener("change", async (ev) => {
    try { await post("/api/control/pause", {paused: ev.target.checked}); toast(ev.target.checked ? "Control automático en pausa" : "Control automático activo", "ok"); await loadStatus(); }
    catch (e) { ev.target.checked = !ev.target.checked; }
  });
  let jobTimer = null;
  function renderJob(j) {
    if (!j || j.status === "none") { $("#job-bar").hidden = true; return; }
    $("#dryrun-result").hidden = false;
    const st = {running: "EN EJECUCIÓN", done: "terminado", aborted: "ABORTADO", error: "ERROR"}[j.status] || j.status;
    $("#dr-title").textContent = `${j.label} — REAL · ${st}${j.error ? ": " + j.error : ""}`;
    const cov = j.cover_timeline.length ? j.cover_timeline.map((c) => `${c[0].slice(11)} ${COVER_ES[c[1]] || c[1]}`).join(" → ") : "—";
    $("#dr-summary").innerHTML = `<span>Estado: <b>${esc(STATE_ES[j.state_before] || j.state_before || "?")}</b> → <b>${esc(STATE_ES[j.state_after] || j.state_after || "…")}</b></span><span>Tapa: ${esc(cov)}</span><span>${j.elapsed_s} s</span>${j.timings.open_s ? `<span>abrir ${j.timings.open_s} s</span>` : ""}${j.timings.close_s ? `<span>cerrar ${j.timings.close_s} s</span>` : ""}`;
    $("#dr-steps").innerHTML = j.steps.map((s) => `<li class="${esc(s.kind)}">${esc(s.text)}${s.kind === "write" ? ` <span class="muted">(DB${s.db}.${s.byte}.${s.bit} = ${s.value ? 1 : 0})</span>` : ""}${s.kind === "wait" && s.seconds != null && s.progress != null ? ` <b>${s.progress}/${s.seconds} s</b>` : ""}${s.ok === true ? " ✔" : s.ok === false ? " ✖" : ""}</li>`).join("");
    $("#job-bar").hidden = j.status !== "running";
    $("#job-info").textContent = j.status === "running" ? "El mando se está ejecutando en el PLC. Abortar deja los actuadores como estén en ese momento." : "";
    if (j.status === "running") { clearTimeout(jobTimer); jobTimer = setTimeout(pollJob, 1000); } else { setTimeout(loadStatus, 500); }
  }
  async function pollJob() { try { renderJob(await api("/api/job")); } catch (e) {} }
  $("#btn-job-abort").addEventListener("click", async () => { if (confirm("¿Abortar el mando en curso?")) { try { await post("/api/job/abort"); } catch (e) {} } });
  $$(".btn.cmd").forEach((b) => b.addEventListener("click", async () => {
    const cmd = b.dataset.cmd;
    if (status && status.mode === "real") {
      if (!$("#chk-pause").checked) { toast("Pon el control automático en pausa antes de mandar", "bad"); return; }
      if (!confirm(`MODO REAL: «${b.textContent.trim()}» se ejecutará en el PLC ${status.plc_ip}.\n¿Estás seguro?`)) return;
      let j; try { j = await post("/api/command", {command: cmd, confirm: true}); } catch (e) { return; }
      $("#dryrun-result").hidden = false; renderJob(j); return;
    }
    let r; try { r = await post("/api/dryrun", {command: cmd, apply: $("#chk-apply").checked, alarm: $("#chk-alarm").checked}); } catch (e) { return; }
    $("#dryrun-result").hidden = false;
    $("#dr-title").textContent = `${r.label} — ${r.simulated ? "simulado en seco" : "ejecutado"}${r.aborted ? " · SECUENCIA ABORTADA" : ""}${r.applied ? " · aplicado al estado virtual" : ""}`;
    $("#dr-summary").innerHTML = `<span>Estado: <b>${esc(STATE_ES[r.state_before] || r.state_before)}</b> → <b>${esc(STATE_ES[r.state_after] || r.state_after || "desconocido")}</b></span><span>Tapa: <b>${esc(COVER_ES[r.cover_before] || r.cover_before)}</b> → <b>${esc(COVER_ES[r.cover_after] || r.cover_after)}</b></span><span>${r.writes} escrituras</span>`;
    $("#dr-steps").innerHTML = r.steps.map((s) => `<li class="${esc(s.kind)}">${esc(s.text)}${s.kind === "write" ? ` <span class="muted">(DB${s.db}.${s.byte}.${s.bit} = ${s.value ? 1 : 0})</span>` : ""}${s.ok === false ? " ✖" : ""}</li>`).join("");
    if (r.applied) setTimeout(loadStatus, 500);
  }));

  // ------------------------------------------------------------------ Simulador
  $("#sim-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const body = {now: $("#sim-now").value, state: $("#sim-state").value, answer: $("input[name=sim-answer]:checked").value, alarm: $("#sim-alarm").checked,
      frame: $("input[name=sim-frame]:checked").value};
    let r; try { r = await post("/api/simulate", body); } catch (e) { return; }
    $("#sim-result").hidden = false;
    const sm = $("#sim-mode"); sm.className = "big " + r.plc_mode; $(".ico", sm).textContent = MODE_ICO[r.plc_mode] || "?"; $("span:last-child", sm).textContent = (MODE_ES[r.plc_mode] || r.plc_mode) + (r.changed ? " (cambia)" : " (se mantiene)");
    const sc = $("#sim-cover"); sc.className = "big " + r.cover; $(".ico", sc).textContent = COVER_ICO[r.cover] || "?"; $("span:last-child", sc).textContent = COVER_ES[r.cover] || r.cover;
    actuators(r.pumps, $("#sim-pumps"));
    $("#sim-reasons").innerHTML = r.reasons.map((x) => `<li>${esc(x)}</li>`).join("");
    $("#sim-schedule").textContent = r.policy === "toggle"
      ? `Política antigua del control. Horario usado: calor ${r.schedule.heat_start.slice(0, 5)}–${r.schedule.heat_end.slice(0, 5)}, parada hasta ${r.schedule.parada_end.slice(0, 5)}, el resto frío; fines de semana parada. Franja de la hora elegida: ${r.slot}.`
      : "Con «sí», el control pone el modo de la franja planificada activa; con «no» mantiene el estado; con «parada» para. Si el RCE está en automático, manda el programa del PLC.";
  });

  // ------------------------------------------------------------------ Simulador completo y reproducción
  function fillSimFromStatus() {
    if (!status) return;
    const get = (v) => { const r = status.readings.find((x) => x.variable === v); return r && r.value != null ? r.value : ""; };
    $("#simf-cold").value = get("t_cold_tank_c"); $("#simf-hot").value = get("t_hot_tank_c"); $("#simf-ir").value = get("ir_net_w_m2");
    $("#simf-solar").value = get("solar_global_w_m2"); $("#simf-wind").value = get("wind_speed_m_s");
    const st = status.control && status.control.plc_state; if (st && $(`#simf-state option[value=${st}]`)) $("#simf-state").value = st;
    if (!$("#simf-now").value) $("#simf-now").value = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  }
  $("#simf-fill").addEventListener("click", fillSimFromStatus);
  $("#simf-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const num = (id) => { const v = $(id).value; return v === "" ? null : Number(v); };
    const body = {now: $("#simf-now").value || null, state: $("#simf-state").value, alarm: $("#simf-alarm").checked,
      overrides: {cold: num("#simf-cold"), hot: num("#simf-hot"), ir_rad_w_m2: num("#simf-ir"), solar_rad_w_m2: num("#simf-solar"), wind_vel_m_s: num("#simf-wind")},
      demand_kwh: num("#simf-demand")};
    toast("Pidiendo la previsión al modelo…");
    let r; try { r = await post("/api/simulate_full", body); } catch (e) { return; }
    $("#simf-result").hidden = false;
    const sm = $("#simf-mode"); sm.className = "big " + r.plc_mode; $(".ico", sm).textContent = MODE_ICO[r.plc_mode] || "?"; $("span:last-child", sm).textContent = (MODE_ES[r.plc_mode] || r.plc_mode) + (r.changed ? " (cambia)" : " (se mantiene)");
    const sc = $("#simf-cover"); sc.className = "big " + r.cover; $(".ico", sc).textContent = COVER_ICO[r.cover] || "?"; $("span:last-child", sc).textContent = COVER_ES[r.cover] || r.cover;
    actuators(r.pumps, $("#simf-pumps"));
    const d = r.decision, f = r.forecast, p = r.plan;
    $("#simf-plan").innerHTML = [
      ["Respuesta de la IA", {si: "sí (cambiar de modo)", no: "no (mantener)", parada: "parada"}[d.label] || d.label],
      ["Plan activo", `${p.kind === "hot" ? "calor" : "frío"} · ventana ${fmtTs(p.window[0])} → ${fmtTs(p.window[1])}`],
      ["Demanda del plan", `${fmtNum(p.target_kwh, 2)} kWh · producción prevista ${p.total_kwh === -1 ? "insuficiente" : fmtNum(p.total_kwh, 2) + " kWh"} · ${p.frames_n} franjas`],
      ["Previsión 24 h", `producción calor ${fmtNum(f.prod_hot_24h_kwh, 2)} kWh, frío ${fmtNum(f.prod_cold_24h_kwh, 2)} kWh · demanda calor ${fmtNum(f.demand_hot_24h_kwh, 2)} kWh, frío ${fmtNum(f.demand_cold_24h_kwh, 2)} kWh`],
      ["Entradas usadas", Object.entries(r.inputs).map(([k, v]) => `${k} = ${fmtNum(v, 2)}`).join(" · ")],
    ].map(([k, v]) => `<b>${esc(k)}</b><span>${esc(v)}</span>`).join("");
    $("#simf-reasons").innerHTML = d.reasons.map((x) => `<li>${esc(x)}</li>`).join("");
    $("#simf-note").textContent = r.note;
  });
  $("#replay-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    toast("Reproduciendo el día (tres previsiones del modelo)…");
    let r; try { r = await post("/api/replay", {date: $("#replay-date").value, state: $("#replay-state").value}); } catch (e) { return; }
    $("#replay-result").hidden = false;
    $("#replay-info").textContent = `${r.summary.si} sí · ${r.summary.no} no · ${r.summary.parada} parada · ${r.state_changes} cambios de estado · ${r.predict_calls} previsiones. ${r.note}`;
    const MC = {parada: 0, hot: 1, cold: 2, automatic: 3};
    const t0 = Date.parse(r.day + "T00:00:00") / 1000;
    lineChart("ch-replay", [["Modo (0 parada · 1 calor · 2 frío)", r.steps.map((s) => [s.ts, MC[s.plc_mode] == null ? null : MC[s.plc_mode]]), "#5b6475"], ["Tapa (1 abierta)", r.steps.map((s) => [s.ts, s.cover === "open" ? 1 : 0]), "#1e7a46"], ["IA dice sí", r.steps.map((s) => [s.ts, s.action === "si" ? 3 : null]), "#b42318"]], "", {xr: [t0, t0 + 86400], yr: [-0.2, 3.2], step: true, ticks: [0, 1, 2, 3]});
    $("#replay-table tbody").innerHTML = r.steps.map((s) => `<tr class="${s.action === "si" ? "hl" : ""}"><td>${esc(s.ts.slice(11))}</td><td>${s.plan === "hot" ? "calor" : "frío"}</td><td>${s.in_frame ? "sí" : "—"}</td><td>${s.cop}</td><td><b>${esc(s.action)}</b></td><td>${esc(STATE_ES[s.next_state] || s.next_state)}</td><td>${esc(COVER_ES[s.cover] || s.cover)}</td><td>${s.demand_kwh}</td><td class="muted">${esc(s.reason)}</td></tr>`).join("");
  });

  // ------------------------------------------------------------------ Historial
  let histHours = 24;
  $$("#hist-range button").forEach((b) => b.addEventListener("click", () => { $$("#hist-range button").forEach((x) => x.classList.toggle("active", x === b)); histHours = +b.dataset.h; loadHistory(); }));
  $("#btn-hist-refresh").addEventListener("click", loadHistory);
  async function loadHistory() {
    let d; try { d = await api(`/api/history?hours=${histHours}`); } catch (e) { return; }
    $("#hist-info").textContent = `${d.decisions.length} ciclos del control · ${fmtTs(d.now)}`;
    const ser = d.series, get = (k) => ser[k] ? ser[k].points : [];
    const tNow = Date.parse(d.now.replace(" ", "T")) / 1000, xr = [tNow - d.hours * 3600, tNow + 600];
    lineChart("ch-tanks", [["Depósito frío T9", get("t_cold_tank_c"), "#0b5fa5"], ["Depósito caliente T6", get("t_hot_tank_c"), "#c2410c"], ["Entrada RCE T2", get("t_circuit_2_c"), "#7a8494"], ["Salida RCE T4", get("t_circuit_4_c"), "#1e7a46"]], "°C", {xr});
    const byExe = (field) => ["real", "shadow"].map((e) => [e === "real" ? "real (aplicado)" : "sombra (simulado)", d.decisions.filter((r) => r.execution === e && r[field] !== "" && r[field] != null).map((r) => [r.ts, r[field]]), e === "real" ? "#b42318" : "#5b6475"]);
    lineChart("ch-mode", byExe("plc_mode_code"), "modo", {xr, yr: [-0.2, 3.2], step: true, ticks: [0, 1, 2, 3]});
    lineChart("ch-cover", byExe("cover_open"), "tapa", {xr, yr: [-0.2, 1.2], step: true, ticks: [0, 1]});
    lineChart("ch-flow", [["Caudal", get("flow_rce_l_min"), "#0b4f6c"]], "L/min", {xr, yr: (u, mn, mx) => [0, Math.max(1, mx)]});
    lineChart("ch-rad", [["IR neto", get("ir_net_w_m2"), "#5a3fb0"], ["Solar", get("solar_global_w_m2"), "#b7791f"]], "W/m²", {xr});
    lineChart("ch-amb", [["Aire exterior", get("t_ambient_c"), "#c2410c"], ["Humedad", get("rh_ambient_pct"), "#0b5fa5"], ["Viento", get("wind_speed_m_s"), "#7a8494"]], "", {xr});
    const ev = d.events.slice().sort((a, b) => (b.ts || "").localeCompare(a.ts || "")).slice(0, 150);
    $("#events").innerHTML = ev.length ? ev.map((e) => `<li class="${esc(e.level || "")}"><span class="ts">${esc(fmtTs(e.ts))}</span>${esc(e.text)}</li>`).join("") : "<li class='muted'>Sin eventos en el periodo</li>";
  }
  function lineChart(id, series, unit, o) {
    o = o || {}; const step = !!o.step;
    const el = $("#" + id); if (!el) return;
    if (charts[id]) { charts[id].destroy(); charts[id] = null; }
    el.innerHTML = "";
    const nonEmpty = series.filter((s) => s[1].length);
    if (!nonEmpty.length) { el.innerHTML = "<div class='muted' style='padding:20px'>Sin datos en el periodo</div>"; return; }
    const px = (t) => Date.parse(o.iso ? t : t.replace(" ", "T")) / 1000;
    const xsSet = new Set(); nonEmpty.forEach((s) => s[1].forEach((p) => xsSet.add(px(p[0]))));
    const xs = Array.from(xsSet).sort((a, b) => a - b), idx = new Map(xs.map((x, i) => [x, i]));
    const data = [xs].concat(nonEmpty.map((s) => { const ys = new Array(xs.length).fill(null); s[1].forEach((p) => { ys[idx.get(px(p[0]))] = p[1]; }); return ys; }));
    const opts = {width: el.clientWidth || 800, height: el.clientHeight || 220,
      scales: {x: {time: true, range: o.xr ? () => o.xr : undefined}, y: {range: o.yr ? (typeof o.yr === "function" ? o.yr : () => o.yr) : undefined}},
      axes: [{}, {label: unit, size: 60, splits: o.ticks ? () => o.ticks : undefined}],
      series: [{label: "Hora"}].concat(nonEmpty.map((s) => ({label: s[0], stroke: s[2], width: 2, spanGaps: true, paths: step ? uPlot.paths.stepped({align: 1}) : undefined, points: {show: step}})))};
    charts[id] = new uPlot(opts, data, el);
  }
  window.addEventListener("resize", () => { Object.entries(charts).forEach(([id, c]) => { if (c) { const el = $("#" + id); c.setSize({width: el.clientWidth, height: el.clientHeight}); } }); });

  // ------------------------------------------------------------------ Diario
  async function loadDiary() {
    let rows; try { rows = await api("/api/diary?n=60"); } catch (e) { return; }
    const open = rows.slice().reverse().find((r) => r.tipo === "ensayo_inicio" || r.tipo === "ensayo_fin");
    const trial = open && open.tipo === "ensayo_inicio" ? open.ensayo_id : null;
    const OPS = {manual: "manual", automatico_plc: "automático PLC", control_ia: "control IA"};
    $("#trial-tag").textContent = trial ? `en curso: ${trial} · ${OPS[open.operacion] || open.operacion || "?"}` : "ninguno en curso";
    $("#btn-trial-start").disabled = !!trial; $("#btn-trial-end").disabled = !trial;
    const KIND = {ensayo_inicio: "Inicio de ensayo", ensayo_fin: "Fin de ensayo", incidencia: "Incidencia", nota: "Nota", mando: "Mando manual", modo: "Cambio de modo", sistema: "Sistema"};
    $("#diary-table tbody").innerHTML = rows.slice().reverse().map((r) => `<tr><td>${esc(fmtTs(r.timestamp))}</td><td>${esc(r.autor)}</td><td>${esc(KIND[r.tipo] || r.tipo)}</td><td>${esc(r.texto)}</td><td class="muted">${esc(r.ensayo_id)}${r.operacion ? " · " + esc(OPS[r.operacion] || r.operacion) : ""}</td></tr>`).join("") || "<tr><td colspan=5 class='muted'>Sin anotaciones</td></tr>";

  }
  $("#trial-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const t9 = $("#trial-t9").value;
    const text = `${$("#trial-type").value}; unidad ${$("#trial-unit").value}${t9 ? `; T9 inicial ${t9} °C` : ""}${$("#trial-notes").value ? "; " + $("#trial-notes").value : ""}`;
    try { await post("/api/diary", {kind: "ensayo_inicio", text, operation: $("#trial-op").value}); toast("Ensayo iniciado", "ok"); loadDiary(); } catch (e) {}
  });
  $("#btn-trial-end").addEventListener("click", async () => { try { await post("/api/diary", {kind: "ensayo_fin", text: ""}); toast("Ensayo terminado", "ok"); loadDiary(); } catch (e) {} });
  $("#note-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try { await post("/api/diary", {kind: $("#note-kind").value, text: `[${$("#note-cat").value}] ${$("#note-text").value.trim()}`}); $("#note-text").value = ""; toast("Anotado", "ok"); loadDiary(); } catch (e) {}
  });

  // ------------------------------------------------------------------ Ajustes
  async function loadSettings() {
    let s; try { s = await api("/api/settings"); } catch (e) { return; }
    const badge = $("#set-mode"); badge.textContent = s.mode === "real" ? "REAL" : "SOMBRA"; badge.classList.toggle("real", s.mode === "real");
    $("#set-exit").innerHTML = Object.entries(s.exit_states).map(([k, v]) => `<option value="${k}" ${k === s.exit_state ? "selected" : ""}>${esc(v)}</option>`).join("");
    $("#set-autostart").checked = !!s.autostart; $("#set-refresh").value = s.refresh_s; $("#set-plcread").checked = !!s.plc_read_allowed;
    const mine = status && status.lock && status.lock.name === me;
    const pwSet = !!s.real_password_set;
    const isAdmin = role === "admin";
    $("#btn-mode-real").disabled = !(isAdmin && mine && s.mode === "shadow" && pwSet); $("#btn-mode-shadow").disabled = !(isAdmin && mine && s.mode === "real");
    $("#btn-mode-real").title = !isAdmin ? "Solo rce_admin" : !pwSet ? "No hay contraseña del modo REAL" : mine ? "" : "Hace falta tener el mando";
    $("#set-real-note").textContent = pwSet ? "Activar REAL pide la contraseña del modo REAL además de las dos confirmaciones." : "No hay contraseña del modo REAL: hasta que se defina (abajo, desde el propio PC del laboratorio) el modo REAL no se puede activar.";
    $("#pw-current-row").style.display = pwSet ? "" : "none";
    const admin = role === "admin";
    $("#my-account").textContent = `${me} · ${ROLE_ES[role] || role}`;
    $("#btn-my-password").hidden = role === "viewer";
    $("#people-note").textContent = admin ? "Al dar de alta a alguien o cambiarle la contraseña, tendrá que cambiarla en su próxima entrada (salvo rce_viewer, que es compartida)." : "Solo rce_admin da de alta personas y cambia contraseñas de otros. La tuya: «Cambiar mi contraseña».";
    $("#people-form").hidden = !admin;
    ["#btn-settings-save", "#btn-real-password"].forEach((id) => { $(id).disabled = !admin; $(id).title = admin ? "" : "Solo rce_admin"; });
    $("#people").innerHTML = s.people.map((p) => `<li><span>${esc(p)}</span>${p === me ? "<span class='muted'>tú</span>" : admin ? `<button class="btn small" data-del="${esc(p)}">Quitar</button>` : ""}</li>`).join("");
    $$("#people [data-del]").forEach((b) => b.addEventListener("click", async () => { if (confirm(`¿Quitar a ${b.dataset.del}?`)) { try { await api(`/api/people/${encodeURIComponent(b.dataset.del)}`, {method: "DELETE"}); loadSettings(); } catch (e) {} } }));
    $("#info").innerHTML = [["Este PC", s.host], ["Carpeta de WinCC", s.wincc_dir], ["Dirección del panel", `http://${location.host}`], ["API de predicción", s.api_url]].map(([k, v]) => `<b>${esc(k)}</b><span>${esc(v)}</span>`).join("");
  }
  $("#btn-settings-save").addEventListener("click", async () => {
    try { await post("/api/settings", {exit_state: $("#set-exit").value, autostart: $("#set-autostart").checked, refresh_s: +$("#set-refresh").value, plc_read_allowed: $("#set-plcread").checked}); toast("Ajustes guardados", "ok"); } catch (e) {}
  });
  function renderPlcInfo(p) {
    if (!p) { $("#plc-info").innerHTML = ""; return; }
    $("#plc-info").innerHTML = (p.ok ? [["PLC", p.ip + " · leído " + fmtTs(p.ts)], ["Estado", (STATE_ES[p.state] || p.state || "desconocido") + " (" + (MODE_ES[p.plc_mode] || p.plc_mode) + ")"], ["Tapa", COVER_ES[p.cover] || p.cover], ["Alarmas", Object.entries(p.alarms || {}).map(([k, v]) => `${k}: ${v ? "ACTIVA" : "no"}`).join(" · ")], ["Horario de hoy (DB80/83)", p.schedule_today ? `calor ${p.schedule_today.heat_start}–${p.schedule_today.heat_end}, parada hasta ${p.schedule_today.parada_end}` : "fin de semana: parada"]] : [["PLC", p.ip], ["Error", p.error || "no responde"]]).map(([k, v]) => `<b>${esc(k)}</b><span>${esc(v)}</span>`).join("");
  }
  $("#btn-plc-read").addEventListener("click", async () => { try { renderPlcInfo(await api("/api/plc")); } catch (e) {} });
  $("#btn-mode-real").addEventListener("click", async () => {
    let p; try { p = await api("/api/plc"); } catch (e) { toast("Primero permite la lectura del PLC en Ajustes o comprueba la red", "bad"); return; }
    renderPlcInfo(p);
    if (!p.ok) { toast("El PLC no responde: no se puede activar REAL", "bad"); return; }
    if (!confirm(`¿Estás seguro?\n\nA partir de ahora el control escribirá en el PLC ${p.ip}: bombas, válvulas y tapa.\nEstado actual del PLC: ${STATE_ES[p.state] || p.state || "desconocido"}, tapa ${COVER_ES[p.cover] || p.cover}.\nLa siguiente decisión del control se aplicará en el PLC.`)) return;
    const word = prompt("Confirmar la decisión: escribe REAL en mayúsculas");
    if (word === null) return;
    const pw = await askPassword();
    if (pw === null) return;
    try { const r = await post("/api/mode", {mode: "real", confirm: word.trim(), password: pw}); toast("Modo REAL activado", "ok"); await loadStatus(); loadSettings(); } catch (e) {}
  });
  function askPassword() {
    return new Promise((resolve) => {
      const dlg = $("#pw-dialog"), input = $("#pw-input");
      input.value = "";
      dlg.onclose = () => resolve(dlg.returnValue === "ok" ? input.value : null);
      dlg.showModal(); input.focus();
    });
  }
  $("#btn-real-password").addEventListener("click", async () => {
    const pw = $("#pw-new").value, pw2 = $("#pw-new2").value;
    if (pw !== pw2) { toast("Las dos contraseñas no coinciden", "bad"); return; }
    try { await post("/api/real-password", {password: pw, current: $("#pw-current").value}); toast("Contraseña del modo REAL guardada", "ok"); $("#pw-new").value = $("#pw-new2").value = $("#pw-current").value = ""; loadSettings(); } catch (e) {}
  });
  $("#btn-mode-shadow").addEventListener("click", async () => {
    if (!confirm(`¿Volver a SOMBRA?\n\nEl control dejará de escribir en el PLC y este quedará en: ${$("#set-exit").selectedOptions[0] ? $("#set-exit").selectedOptions[0].textContent : "estado de salida"}.`)) return;
    try { const r = await post("/api/mode", {mode: "shadow"}); toast(r.ok ? "Modo SOMBRA activado; PLC en estado de salida" : "SOMBRA activado, pero el estado de salida falló: revisa el PLC", r.ok ? "ok" : "bad"); await loadStatus(); loadSettings(); } catch (e) {}
  });
  $("#btn-my-password").addEventListener("click", () => showPwChange(false));
  $("#people-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try { await post("/api/people", {name: $("#p-name").value.trim(), pin: $("#p-pin").value}); $("#p-name").value = ""; $("#p-pin").value = ""; toast("Persona guardada", "ok"); loadSettings(); } catch (e) {}
  });

  // ------------------------------------------------------------------ Agenda
  let agOpts = null;
  const AG_ST = {pendiente: "pendiente", en_curso: "en curso", hecha: "hecha", cancelada: "cancelada", fallida: "fallida", perdida: "no ejecutada"};
  const localIso = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  function agKind() { return $("input[name=ag-kind]:checked").value; }
  function agToggle() {
    const st = agKind() === "estado";
    $("#ag-state-row").hidden = !st; $("#ag-cmd-row").hidden = st; $("#ag-end-row").hidden = !st; $("#ag-atend-row").hidden = !st;
    $("#ag-trial-row").hidden = !st; $("#ag-start-hint").textContent = st ? "vacío = ahora" : "obligatorio";
  }
  $$("input[name=ag-kind]").forEach((r) => r.addEventListener("change", agToggle));
  async function loadAgenda() {
    let d; try { d = await api("/api/agenda"); } catch (e) { return; }
    if (!agOpts) {
      agOpts = d;
      $("#ag-state").innerHTML = Object.entries(d.states).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
      $("#ag-cmd").innerHTML = Object.entries(d.commands).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
      $("#ag-atend").innerHTML = Object.entries(d.at_end).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
      agToggle();
    }
    $("#ag-mode").textContent = d.mode === "real" ? "REAL: actúa en el PLC (solo rce_admin)" : "SOMBRA: estado simulado"; $("#ag-mode").className = "tag " + d.mode;
    const mine = status && status.lock && status.lock.name === me;
    $("#ag-table tbody").innerHTML = d.entries.map((e) => {
      const canCancel = mine && ["pendiente", "en_curso"].includes(e.status) && (e.author === me || role === "admin");
      const st = (AG_ST[e.status] || e.status) + (e.waiting && e.status === "pendiente" ? " (en espera: solo rce_admin en REAL)" : "");
      return `<tr><td>${e.id}</td><td>${esc(e.kind === "mando" ? "Mando: " + e.label : e.label)}</td><td>${esc(fmtTs(e.start))}</td><td>${esc(e.end ? fmtTs(e.end) : "—")}</td>` +
        `<td>${esc(e.at_end ? d.at_end[e.at_end] : "—")}</td><td>${esc(e.author)}</td><td><span class="ag-st ${esc(e.status)}">${esc(st)}</span>${e.message ? `<div class="muted">${esc(e.message)}</div>` : ""}</td>` +
        `<td>${canCancel ? `<button class="btn small" data-cancel="${e.id}">Cancelar</button>` : ""}</td></tr>`;
    }).join("") || "<tr><td colspan=8 class='muted'>No hay nada programado</td></tr>";
    $$("#ag-table [data-cancel]").forEach((b) => b.addEventListener("click", async () => {
      if (!confirm(`¿Cancelar la entrada #${b.dataset.cancel}? Si está en curso, termina ahora con su opción de final.`)) return;
      try { await post(`/api/agenda/${b.dataset.cancel}/cancel`); toast("Entrada cancelada", "ok"); loadAgenda(); loadStatus(); } catch (e) {}
    }));
  }
  $("#ag-preset").addEventListener("click", () => {
    const now = new Date(), start = new Date(now); start.setHours(19, 0, 0, 0);
    if (start <= now) start.setDate(start.getDate() + 1);
    const end = new Date(start); end.setHours(start.getHours() + 12);
    $("input[name=ag-kind][value=estado]").checked = true; agToggle();
    $("#ag-state").value = "ColdMode1"; $("#ag-start").value = localIso(start); $("#ag-end").value = localIso(end);
    $("#ag-atend").value = "parada"; $("#ag-repeat").value = 7;
    toast("Rellenado: 7 noches de modo frío de 19:00 a 07:00, con parada al terminar. Revisa y pulsa «Programar».", "ok");
  });
  $("#ag-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const kind = agKind();
    const body = {kind, target: kind === "estado" ? $("#ag-state").value : $("#ag-cmd").value, start: $("#ag-start").value || null,
      end: kind === "estado" ? ($("#ag-end").value || null) : null, at_end: $("#ag-atend").value, trial: $("#ag-trial").checked,
      repeat_days: +$("#ag-repeat").value || 1};
    try { await post("/api/agenda", body); toast("Programado", "ok"); loadAgenda(); loadStatus(); } catch (e) {}
  });

  // ------------------------------------------------------------------ Auditoría
  const CMD_ES = {cold: "modo frío", heat: "modo calor", parada: "parada", automatic: "automático", open_cover: "abrir tapa",
                  close_cover: "cerrar tapa", test_cover: "prueba de tapa"};
  function actionEs(a) {
    const [k, ...rest] = String(a).split(":"), r = rest.join(":");
    const fixed = {login: "Entrada", logout: "Salida", "lock:take": "Toma el mando", "lock:release": "Suelta el mando",
      "system:start": "Arranca la predicción y el control", "system:stop": "Para la predicción y el control",
      "system:restart": "Reinicia la predicción y el control", "filebeat:restart": "Reinicia el envío a Kibana",
      "control:pause": "Pone el control automático en pausa", "control:resume": "Reactiva el control automático",
      "mode:real": "Activa el modo REAL", "mode:shadow": "Vuelve al modo SOMBRA", "job:abort": "Aborta un mando",
      "password:change": "Cambia su contraseña", "simulate_full": "Simulador completo"}[a] || null;
    if (fixed) return fixed;
    if (k === "dryrun") return `Mando en seco: ${CMD_ES[r] || r}`;
    if (k === "command") return `Mando REAL: ${CMD_ES[r] || r}`;
    if (k === "system" && rest[0] === "restart") return `Reinicia ${rest[1] === "sc" ? "el control" : "la predicción"}`;
    if (k === "people") return rest[0] === "add" ? `Alta o contraseña nueva: ${rest[1]}` : `Quita a ${rest[1]}`;
    if (k === "real-password") return "Contraseña del modo REAL";
    if (k === "settings") return `Ajustes: ${r}`;
    if (k === "diario") return `Diario: ${r.replace("ensayo_inicio", "inicio de ensayo").replace("ensayo_fin", "fin de ensayo")}`;
    if (k === "replay") return `Reproduce el día ${r}`;
    if (k === "mode" && rest[0] === "real") return "Intento de activar REAL: contraseña incorrecta";
    if (k === "agenda") return `Agenda: ${r}`;
    return a;
  }
  function audParams() {
    const p = new URLSearchParams();
    if ($("#aud-user").value) p.set("user", $("#aud-user").value);
    if ($("#aud-cat").value) p.set("category", $("#aud-cat").value);
    if ($("#aud-since").value) p.set("since", $("#aud-since").value);
    if ($("#aud-until").value) p.set("until", $("#aud-until").value);
    return p;
  }
  async function loadAudit(fillFilters) {
    const p = audParams();
    let d; try { d = await api("/api/audit?" + p.toString()); } catch (e) { return; }
    if (fillFilters) {
      const keep = [$("#aud-user").value, $("#aud-cat").value];
      $("#aud-user").innerHTML = '<option value="">Todos</option>' + d.users.map((u) => `<option>${esc(u)}</option>`).join("");
      $("#aud-cat").innerHTML = '<option value="">Todos</option>' + d.categories.map((c) => `<option>${esc(c)}</option>`).join("");
      $("#aud-user").value = keep[0]; $("#aud-cat").value = keep[1];
    }
    $("#aud-csv").href = "/api/audit.csv?" + p.toString();
    $("#aud-info").textContent = `${d.rows.length} acciones${d.rows.length >= 500 ? " (las 500 más recientes; filtra o descarga el CSV para ver todas)" : ""}`;
    $("#aud-table tbody").innerHTML = d.rows.map((a) => `<tr class="${String(a.resultado).startsWith("error") ? "bad" : ""}"><td>${esc(fmtTs(a.timestamp))}</td><td>${esc(a.autor)}</td><td class="muted">${esc(a.host)}</td><td>${esc(a.categoria)}</td><td title="${esc(a.accion)}">${esc(actionEs(a.accion))}</td><td>${esc(a.modo)}</td><td>${esc(a.resultado)}</td></tr>`).join("") || "<tr><td colspan=7 class='muted'>Sin acciones con estos filtros</td></tr>";
  }
  $("#btn-aud").addEventListener("click", () => loadAudit(false));

  // ------------------------------------------------------------------ start
  async function start() {
    await loadStatus();
    let tab = "inicio"; try { tab = localStorage.getItem("panel.tab") || "inicio"; } catch (e) {}
    const b = $(`#tabs button[data-tab=${tab}]`); if (b) b.click();
  }
  checkSession().then((ok) => { if (ok) start(); });
})();
