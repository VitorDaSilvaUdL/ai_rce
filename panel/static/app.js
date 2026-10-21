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

  let me = null, status = null, refreshTimer = null, cycleTimer = null, charts = {};

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
    me = r.name;
    if (!r.people_exist) {
      $("#login-msg").textContent = "Todavía no hay nadie dado de alta. Crea la primera persona (solo desde el PC del panel).";
      $("#login-btn").textContent = "Crear y entrar"; $("#login-form").dataset.setup = "1";
      showLogin();
      return false;
    }
    if (!me) { showLogin(); return false; }
    $("#login").hidden = true;
    $("#user-chip").textContent = me;
    return true;
  }
  $("#login-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const name = $("#login-name").value.trim(), pin = $("#login-pin").value;
    $("#login-error").textContent = "";
    try {
      if ($("#login-form").dataset.setup) { await post("/api/setup", {name, pin}); delete $("#login-form").dataset.setup; $("#login-btn").textContent = "Entrar"; }
      await post("/api/login", {name, pin});
      $("#login").hidden = true; me = name; $("#user-chip").textContent = me;
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
    if (name === "historial") loadHistory();
    if (name === "diario") loadDiary();
    if (name === "ajustes") loadSettings();
    if (name === "simulador" && !$("#sim-now").value) $("#sim-now").value = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16);
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
    $("#st-since").textContent = c.cycle_time ? `Último ciclo: ${fmtTs(c.cycle_time)}${c.paused ? ` · control en pausa (${c.paused_by})` : ""}${c.cover_measured ? ` · tapa medida: ${COVER_ES[c.cover_measured] || c.cover_measured}` : ""}` : "Sin ciclos del control todavía";
    const d = c.decision || {};
    if (c.cycle_time) {
      const act = {si: "Sí: cambiar de modo", no: "No: mantener", parada: "Parada", "": "sin decisión"}[d.action] || d.action;
      $("#decision").innerHTML = `<div class="when">${esc(fmtTs(c.cycle_time))}</div><div><b>${esc(act)}</b> → ${esc(STATE_ES[d.next_state] || d.next_state || "")}${c.paused ? " <i>(no aplicado: control en pausa)</i>" : ""}</div>` +
        `<div class="muted">Franja activa: ${d.in_time_frame === true ? "sí" : d.in_time_frame === false ? "no" : "—"} · producción prevista ahora: calor ${fmtNum(d.hot_prod / 3.6e6, 2)} kWh, frío ${fmtNum(d.cold_prod / 3.6e6, 2)} kWh</div>`;
      const p = c.plan || {};
      $("#plan").textContent = `Plan: demanda pendiente ${fmtNum((p.target_demand_j || 0) / 3.6e6, 2)} kWh · franjas elegidas: ${(p.selected_frames || []).length} · producción prevista del plan: ${fmtNum((p.predicted_production_j || 0) / 3.6e6, 2)} kWh`;
    } else { $("#decision").textContent = "El control todavía no ha decidido nada."; $("#plan").textContent = ""; }
    const w = s.supervisor.wanted;
    $("#btn-start").disabled = w; $("#btn-stop").disabled = !w; $("#btn-restart").disabled = !w;
    $("#sup-info").textContent = w ? `Sistema en marcha desde ${fmtTs(s.supervisor.wanted_since)} · predicción ${s.supervisor.api.running ? "activa" : "parada"} (${s.supervisor.api.restarts} reinicios) · control ${s.supervisor.sc.running ? "activo" : "parado"} (${s.supervisor.sc.restarts} reinicios)` : "Sistema parado";
    const ul = $("#alerts");
    ul.innerHTML = s.alerts.length ? s.alerts.map((a) => `<li class="${a.level}"><span class="t">${esc(a.title)}</span><span class="a">→ ${esc(a.action)}</span>${a.detail ? `<span class="d">${esc(a.detail)}</span>` : ""}</li>`).join("") : '<li class="ok">Todo en orden</li>';
    $("#main-readings").innerHTML = s.readings.map((r) => `<div class="rd ${r.stale ? "stale" : r.frozen ? "frozen" : ""}"><div class="l">${esc(r.label)}${r.system === "RCE-b" ? " (B)" : ""}</div><div><span class="v">${fmtNum(r.value, r.unit === "W/m²" ? 0 : 1)}</span><span class="u">${esc(r.unit)}</span></div><div class="t">${esc(fmtTs(r.ts))} ${r.stale ? "· sin lecturas" : r.frozen ? "· sin cambios" : ""}</div></div>`).join("");
  }
  $("#btn-start").addEventListener("click", () => act("/api/system/start", "Arrancando…"));
  $("#btn-stop").addEventListener("click", () => confirm("¿Parar el control y la predicción?") && act("/api/system/stop", "Parando…"));
  $("#btn-restart").addEventListener("click", () => act("/api/system/restart", "Reiniciando…"));
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
    $("#mandos-mode").textContent = s.mode === "real" ? "REAL" : "SOMBRA: en seco"; $("#mandos-mode").className = "tag " + s.mode;
    $("#mandos-check").innerHTML = [
      [mine, mine ? "Tienes el mando" : "Hace falta tomar el mando"],
      [s.supervisor.wanted, s.supervisor.wanted ? "Control en marcha" : "Control parado (los mandos en seco funcionan igual)"],
      [paused || s.mode !== "real", paused ? "Control automático en pausa" : "Control automático activo"],
      [s.mode !== "real", s.mode === "real" ? "Modo REAL: los mandos escribirían en el PLC" : "Modo SOMBRA: nada toca el PLC"],
    ].map(([ok, t]) => `<li class="${ok ? "yes" : "no"}">${ok ? "✔" : "✖"} ${esc(t)}</li>`).join("");
    $$(".btn.cmd").forEach((b) => { b.disabled = !mine; });
    $("#chk-apply").parentElement.style.display = s.mode === "real" ? "none" : "";
  }
  $("#chk-pause").addEventListener("change", async (ev) => {
    try { await post("/api/control/pause", {paused: ev.target.checked}); toast(ev.target.checked ? "Control automático en pausa" : "Control automático activo", "ok"); await loadStatus(); }
    catch (e) { ev.target.checked = !ev.target.checked; }
  });
  $$(".btn.cmd").forEach((b) => b.addEventListener("click", async () => {
    const cmd = b.dataset.cmd;
    if (status && status.mode === "real" && !confirm(`¿Ejecutar «${b.textContent.trim()}» en el PLC?`)) return;
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
    const body = {now: $("#sim-now").value, state: $("#sim-state").value, answer: $("input[name=sim-answer]:checked").value, alarm: $("#sim-alarm").checked};
    let r; try { r = await post("/api/simulate", body); } catch (e) { return; }
    $("#sim-result").hidden = false;
    const sm = $("#sim-mode"); sm.className = "big " + r.plc_mode; $(".ico", sm).textContent = MODE_ICO[r.plc_mode] || "?"; $("span:last-child", sm).textContent = (MODE_ES[r.plc_mode] || r.plc_mode) + (r.changed ? " (cambia)" : " (se mantiene)");
    const sc = $("#sim-cover"); sc.className = "big " + r.cover; $(".ico", sc).textContent = COVER_ICO[r.cover] || "?"; $("span:last-child", sc).textContent = COVER_ES[r.cover] || r.cover;
    actuators(r.pumps, $("#sim-pumps"));
    $("#sim-reasons").innerHTML = r.reasons.map((x) => `<li>${esc(x)}</li>`).join("");
    $("#sim-schedule").textContent = `Horario usado: calor ${r.schedule.heat_start.slice(0, 5)}–${r.schedule.heat_end.slice(0, 5)}, parada hasta ${r.schedule.parada_end.slice(0, 5)}, el resto frío; fines de semana parada. Franja de la hora elegida: ${r.slot}.`;
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
    const xsSet = new Set(); nonEmpty.forEach((s) => s[1].forEach((p) => xsSet.add(Date.parse(p[0].replace(" ", "T")) / 1000)));
    const xs = Array.from(xsSet).sort((a, b) => a - b), idx = new Map(xs.map((x, i) => [x, i]));
    const data = [xs].concat(nonEmpty.map((s) => { const ys = new Array(xs.length).fill(null); s[1].forEach((p) => { ys[idx.get(Date.parse(p[0].replace(" ", "T")) / 1000)] = p[1]; }); return ys; }));
    const opts = {width: el.clientWidth || 800, height: el.clientHeight || 220,
      scales: {x: {time: true, range: o.xr ? () => o.xr : undefined}, y: {range: o.yr ? (typeof o.yr === "function" ? o.yr : () => o.yr) : undefined}},
      axes: [{}, {label: unit, size: 60, splits: o.ticks ? () => o.ticks : undefined}],
      series: [{label: "Hora"}].concat(nonEmpty.map((s) => ({label: s[0], stroke: s[2], width: 2, spanGaps: true, paths: step ? uPlot.paths.stepped({align: 1}) : undefined, points: {show: step}})))};
    charts[id] = new uPlot(opts, data, el);
  }
  window.addEventListener("resize", () => { Object.entries(charts).forEach(([id, c]) => { if (c) { const el = $("#" + id); c.setSize({width: el.clientWidth, height: el.clientHeight}); } }); });

  // ------------------------------------------------------------------ Diario
  async function loadDiary() {
    let rows, acts; try { rows = await api("/api/diary?n=60"); acts = await api("/api/actions?n=40"); } catch (e) { return; }
    const open = rows.slice().reverse().find((r) => r.tipo === "ensayo_inicio" || r.tipo === "ensayo_fin");
    const trial = open && open.tipo === "ensayo_inicio" ? open.ensayo_id : null;
    $("#trial-tag").textContent = trial ? `en curso: ${trial}` : "ninguno en curso";
    $("#btn-trial-start").disabled = !!trial; $("#btn-trial-end").disabled = !trial;
    const KIND = {ensayo_inicio: "Inicio de ensayo", ensayo_fin: "Fin de ensayo", incidencia: "Incidencia", nota: "Nota", mando: "Mando manual", modo: "Cambio de modo", sistema: "Sistema"};
    $("#diary-table tbody").innerHTML = rows.slice().reverse().map((r) => `<tr><td>${esc(fmtTs(r.timestamp))}</td><td>${esc(r.autor)}</td><td>${esc(KIND[r.tipo] || r.tipo)}</td><td>${esc(r.texto)}</td><td class="muted">${esc(r.ensayo_id)}</td></tr>`).join("") || "<tr><td colspan=5 class='muted'>Sin anotaciones</td></tr>";
    $("#actions-table tbody").innerHTML = acts.slice().reverse().map((a) => `<tr><td>${esc(fmtTs(a.timestamp))}</td><td>${esc(a.autor)}</td><td>${esc(a.accion)}</td><td>${esc(a.modo)}</td><td>${esc(a.resultado)}</td></tr>`).join("") || "<tr><td colspan=5 class='muted'>Sin acciones</td></tr>";
  }
  $("#trial-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const t9 = $("#trial-t9").value;
    const text = `${$("#trial-type").value}; unidad ${$("#trial-unit").value}${t9 ? `; T9 inicial ${t9} °C` : ""}${$("#trial-notes").value ? "; " + $("#trial-notes").value : ""}`;
    try { await post("/api/diary", {kind: "ensayo_inicio", text}); toast("Ensayo iniciado", "ok"); loadDiary(); } catch (e) {}
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
    $("#set-autostart").checked = !!s.autostart; $("#set-refresh").value = s.refresh_s;
    $("#people").innerHTML = s.people.map((p) => `<li><span>${esc(p)}</span>${p !== me ? `<button class="btn small" data-del="${esc(p)}">Quitar</button>` : "<span class='muted'>tú</span>"}</li>`).join("");
    $$("#people [data-del]").forEach((b) => b.addEventListener("click", async () => { if (confirm(`¿Quitar a ${b.dataset.del}?`)) { try { await api(`/api/people/${encodeURIComponent(b.dataset.del)}`, {method: "DELETE"}); loadSettings(); } catch (e) {} } }));
    $("#info").innerHTML = [["Este PC", s.host], ["Carpeta de WinCC", s.wincc_dir], ["Dirección del panel", `http://${location.host}`], ["API de predicción", s.api_url]].map(([k, v]) => `<b>${esc(k)}</b><span>${esc(v)}</span>`).join("");
  }
  $("#btn-settings-save").addEventListener("click", async () => {
    try { await post("/api/settings", {exit_state: $("#set-exit").value, autostart: $("#set-autostart").checked, refresh_s: +$("#set-refresh").value}); toast("Ajustes guardados", "ok"); } catch (e) {}
  });
  $("#people-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try { await post("/api/people", {name: $("#p-name").value.trim(), pin: $("#p-pin").value}); $("#p-name").value = ""; $("#p-pin").value = ""; toast("Persona guardada", "ok"); loadSettings(); } catch (e) {}
  });

  // ------------------------------------------------------------------ start
  async function start() {
    await loadStatus();
    let tab = "inicio"; try { tab = localStorage.getItem("panel.tab") || "inicio"; } catch (e) {}
    const b = $(`#tabs button[data-tab=${tab}]`); if (b) b.click();
  }
  checkSession().then((ok) => { if (ok) start(); });
})();
