/* Manual del Panel RCE: renders manual.json (the same content as the PPTX/PDF manual) with small SVG charts. */
(function () {
  "use strict";
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
  const ICO = {cold: "❄", hot: "☀", parada: "⏸", auto: "A", shadow: "S", real: "R", lock: "🔑"};

  function chart(c, size) {
    // viewBox width by layout, so that 12-unit text stays readable: 3 columns, 2 columns or full width
    const W = {third: 340, half: 520, full: 900}[size || "half"], H = size === "full" ? 250 : 220, L = 46, R = 12, T = 12, B = 30;
    const n = c.x.length, ymin = c.ymin, ymax = c.ymax;
    const px = (i) => L + (n === 1 ? 0 : i * (W - L - R) / (n - 1));
    const py = (v) => T + (ymax - v) * (H - T - B) / (ymax - ymin);
    const ticks = [];
    const span = ymax - ymin, raw = span / 4, mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map((k) => k * mag).find((k) => span / k <= 5) || raw;
    for (let v = Math.ceil(ymin / step) * step; v <= ymax + 1e-9; v += step) ticks.push(+v.toFixed(6));
    let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(c.title)}">`;
    ticks.forEach((v) => {
      s += `<line x1="${L}" x2="${W - R}" y1="${py(v)}" y2="${py(v)}" stroke="#d9dee7" stroke-width="1"/>`;
      s += `<text x="${L - 6}" y="${py(v) + 4}" font-size="12" text-anchor="end" fill="#5b6475">${String(v).replace(".", ",")}</text>`;
    });
    if (c.zero && ymin < 0 && ymax > 0) s += `<line x1="${L}" x2="${W - R}" y1="${py(0)}" y2="${py(0)}" stroke="#1d2433" stroke-width="1.2" stroke-dasharray="4 3"/>`;
    c.x.forEach((x, i) => { if ((size !== "third" && n <= 12) || i % 2 === 0) s += `<text x="${px(i)}" y="${H - 10}" font-size="12" text-anchor="middle" fill="#5b6475">${esc(x)}h</text>`; });
    c.series.forEach((se) => {
      const col = "#" + se.color;
      if (se.markers) {
        se.values.forEach((v, i) => { if (v != null) s += `<circle cx="${px(i)}" cy="${py(v)}" r="6" fill="${col}"/>`; });
        return;
      }
      let d = "", pen = false;
      se.values.forEach((v, i) => {
        if (v == null) { pen = false; return; }
        if (c.kind === "step" || se.step) {
          if (!pen) { d += `M${px(i)},${py(v)}`; pen = true; } else { d += `H${px(i)}V${py(v)}`; }
        } else { d += (pen ? "L" : "M") + `${px(i)},${py(v)}`; pen = true; }
      });
      if (d) s += `<path d="${d}" fill="none" stroke="${col}" stroke-width="2.6" stroke-linejoin="round"/>`;
    });
    s += "</svg>";
    const keys = c.series.map((se) => `<span><i style="background:#${se.color}${se.markers ? ";width:10px;height:10px;border-radius:50%" : ""}"></i>${esc(se.name)}</span>`).join("");
    const reading = (c.reading || []).map(([k, v]) => `<li><b>${esc(k)}:</b> ${esc(v)}</li>`).join("");
    return `<div class="chartbox"><h4>${esc(c.title)}</h4>${s}<div class="keys">${keys}</div>` +
      (c.formula ? `<p class="note"><span class="formula">${esc(c.formula)}</span></p>` : "") +
      (reading ? `<ul class="reading">${reading}</ul>` : "") + `</div>`;
  }

  const kv = (rows) => `<div class="kv">${rows.map(([k, v]) => `<b>${esc(k)}</b><span>${esc(v)}</span>`).join("")}</div>`;
  const steps = (items) => `<ol class="steps">${items.map((t) => `<li>${esc(t)}</li>`).join("")}</ol>`;
  const note = (t) => t ? `<p class="note">${esc(t)}</p>` : "";

  function section(sec, tabs) {
    let h = `<section class="sec" id="${esc(sec.id)}"><div class="kicker">${esc(sec.kicker)}</div>`;
    if (sec.tab) h += `<div class="pills">${tabs.map((t) => `<span class="pill${t === sec.tab ? " on" : ""}">${esc(t)}</span>`).join("")}</div>`;
    h += `<h2>${esc(sec.title)}</h2>`;
    if (sec.lead) h += `<p class="lead">${esc(sec.lead)}</p>`;
    if (sec.flow) h += `<div class="flow">${sec.flow.map((b, i) => (i ? '<div class="arrow">→</div>' : "") + `<div class="box"><b>${esc(b.title)}</b><div>${esc(b.text)}</div></div>`).join("")}</div>`;
    if (sec.cards) h += `<h3>Tres ideas clave</h3><div class="grid g3">${sec.cards.map((c) => `<div class="card"><span class="ico ${c.icon}">${ICO[c.icon] || ""}</span><b>${esc(c.title)}</b>${esc(c.text)}</div>`).join("")}</div>`;
    if (sec.id === "entrar") h += `<div class="grid g2"><div>${steps(sec.steps)}</div><div><h3>La barra de arriba</h3>${kv(sec.kv)}</div></div>`;
    if (sec.lights) {
      h += `<div class="grid g2">${sec.lights.map((l) => `<div class="card light"><div class="dot ${l.color}"></div><div><b>${esc(l.label)}</b>${esc(l.text)}</div></div>`).join("")}</div>`;
      h += `<div class="legend">${sec.legend.map(([c, t]) => `<span><i class="${c}"></i>${esc(t)}</span>`).join("")}</div>`;
      h += `<h3>Botones</h3>${kv(sec.buttons)}`;
    }
    if (sec.modes) {
      h += `<h3>Modo y tapa</h3><div class="grid g4">${sec.modes.map((m) => `<div class="card"><span class="ico ${m.icon}">${ICO[m.icon]}</span><b>${esc(m.title)}</b>${esc(m.text)}</div>`).join("")}</div>${note(sec.note)}`;
      h += `<h3>Las tres respuestas posibles de la última decisión</h3><div class="grid g3">${sec.decisions.map((d) => `<div class="card"><span class="badge ${d.color}">${esc(d.title)}</span><div style="margin-top:6px">${esc(d.text)}</div></div>`).join("")}</div>${note(sec.after)}`;
    }
    if (sec.table) {
      h += `<table><thead><tr>${sec.table.head.map((x) => `<th>${esc(x)}</th>`).join("")}</tr></thead><tbody>` +
        sec.table.rows.map(([lvl, a, b]) => `<tr class="${lvl}"><td><b>${esc(a)}</b></td><td>${esc(b)}</td></tr>`).join("") + `</tbody></table>${note(sec.note)}`;
    }
    if (sec.states) h += `<h3>La columna «Estado»</h3><div class="grid g2">${sec.states.map((s) => `<div class="card"><span class="badge ${s.color}">${esc(s.label)}</span><div style="margin-top:6px">${esc(s.text)}</div></div>`).join("")}</div>${note(sec.note)}`;
    if (sec.charts) {
      h += `<div class="grid g3">${sec.charts.map((c) => chart(c, "third")).join("")}</div>`;
      if (sec.tips) h += `<h3>Además</h3>${kv(sec.tips)}`;
    }
    if (sec.id === "control") {
      h += `<div class="grid g2"><div>${kv(sec.kv)}</div>${chart(sec.chart)}</div>`;
      h += `<h3>${esc(sec.cop.title)}</h3><p><span class="formula">${esc(sec.cop.formula)}</span></p><p>${esc(sec.cop.example)}</p>`;
      h += `<div class="card"><b>${esc(sec.cop.reasons_note)}</b><ul class="reasons">${sec.cop.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul></div>${note(sec.note)}`;
    }
    if (sec.checklist) {
      h += `<div class="grid g2"><div><h3>Antes de mandar, la lista</h3><ul class="check">${sec.checklist.map(([, t]) => `<li>${esc(t)}</li>`).join("")}</ul>` +
        `<h3>Botones</h3><div class="btnrow">${sec.buttons.map((b) => `<span>${esc(b)}</span>`).join("")}</div></div>` +
        `<div><h3>Interruptores</h3>${kv(sec.switches)}</div></div>`;
      h += `<h3>El resultado</h3><p>${esc(sec.result)}</p><div class="card"><b>Modo REAL</b>${esc(sec.real)}</div>`;
    }
    if (sec.levels) {
      h += `<div class="grid g3">${sec.levels.map((l) => `<div class="card"><b>${esc(l.title)}</b>${esc(l.text)}</div>`).join("")}</div>`;
      h += `<div style="margin-top:12px">${chart(sec.chart, "full")}</div>${note(sec.note)}`;
    }
    if (sec.items && sec.chart) h += `<div class="grid g2"><div>${kv(sec.items)}</div>${chart(sec.chart)}</div>${note(sec.note)}`;
    if (sec.items && !sec.chart) h += `<div class="grid g2"><div><h3>${esc(sec.items_title || "")}</h3>${kv(sec.items)}</div><div><h3>${esc(sec.howto_title || "")}</h3>${kv(sec.howto || [])}</div></div>${note(sec.note)}`;
    if (sec.types) h += `<div class="grid g2"><div>${steps(sec.steps)}</div><div class="card"><b>Tipos de ensayo</b><ul class="reasons">${sec.types.map((t) => `<li>${esc(t)}</li>`).join("")}</ul></div></div>${note(sec.note)}`;
    if (sec.exit) {
      h += `<div class="grid g2"><div><h3>Pasar a modo REAL</h3>${steps(sec.steps)}</div><div><h3>Al volver a SOMBRA, el PLC queda en</h3>${kv(sec.exit)}<h3>Además</h3>${kv(sec.others)}</div></div>`;
    }
    if (sec.dashboards) {
      h += `<div class="grid g2"><div><h3>Entrar</h3>${steps(sec.steps)}<h3>Explorar</h3>${kv(sec.howto)}</div>${chart(sec.chart)}</div>`;
      h += `<h3>Los cinco dashboards («RCE — …»)</h3>${kv(sec.dashboards)}`;
    }
    if (sec.rows) {
      h += `<table><thead><tr><th>Si ves</th><th>Significa</th><th>Qué hacer</th></tr></thead><tbody>${sec.rows.map(([a, b, c]) => `<tr><td><b>${esc(a)}</b></td><td>${esc(b)}</td><td>${esc(c)}</td></tr>`).join("")}</tbody></table>`;
    }
    return h + "</section>";
  }

  fetch("/static/manual.json").then((r) => r.json()).then((m) => {
    document.getElementById("m-title").textContent = m.title + " — " + m.subtitle;
    document.getElementById("m-sub").textContent = m.tagline;
    document.getElementById("m-note").textContent = m.example_note;
    document.getElementById("toc").innerHTML = m.sections.map((s) => `<a href="#${esc(s.id)}">${esc(s.toc || s.kicker)}</a>`).join("");
    document.getElementById("content").innerHTML = m.sections.map((s) => section(s, m.tabs)).join("");
    document.getElementById("foot").innerHTML = `<p>${esc(m.contact)}</p><p>${esc(m.acknowledgment)}</p>`;
  }).catch(() => { document.getElementById("content").innerHTML = "<p>No se ha podido cargar el manual.</p>"; });
})();
