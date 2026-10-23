/* Optimalizace vytápění — obrazovky rozhraní */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const NF = d => new Intl.NumberFormat("cs-CZ", { minimumFractionDigits: d, maximumFractionDigits: d });
const num = (v, d = 0) => v == null || isNaN(v) ? "—" : NF(d).format(v);
const kc = v => num(v, 0) + " Kč";
const kKc = v => {
  if (v == null) return "—";
  const a = Math.abs(v);
  if (a >= 1e6) return num(v / 1e6, 2) + " mil. Kč";
  if (a >= 1e3) return num(v / 1e3, 0) + " tis. Kč";
  return num(v, 0) + " Kč";
};
const years = y => y == null ? "nevrátí se" : y * 12 < 1 ? "do 1 měs." : y < 1 ? `${num(y * 12, 0)} měs.` : `${num(y, 1)} let`;
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const DAYS = ["po", "út", "st", "čt", "pá", "so", "ne"];
const SEV = { critical: "kritické", warning: "upozornění", info: "informace" };

const api = async (path, body) => {
  const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
};

const cache = {};
const get = async path => cache[path] || (cache[path] = await api(path));

const VIEWS = {
  overview: { title: "Proč tolik spotřebuje", render: renderOverview },
  faults: { title: "Závady", render: renderFaults },
  hydraulics: { title: "Simulace větví", render: renderHydraulics },
  optimize: { title: "Optimální nastavení", render: renderOptimize },
  savings: { title: "Úspory a návratnost", render: renderSavings },
  bms: { title: "Napojení na bms-monitor", render: renderBms },
};

// --- navigace ------------------------------------------------------------------
async function route() {
  const view = (location.hash.slice(1) || "overview").split("?")[0];
  const v = VIEWS[view] || VIEWS.overview;
  document.querySelectorAll("#nav a").forEach(a => a.classList.toggle("active", a.dataset.view === view));
  $("#view-title").textContent = v.title;
  const screen = $("#screen");
  screen.innerHTML = `<div class="loading">Načítám…</div>`;
  try {
    await v.render(screen);
  } catch (e) {
    screen.innerHTML = `<div class="loading">Nepodařilo se načíst data: ${esc(e.message)}</div>`;
    console.error(e);
  }
  screen.scrollTop = 0;
}
document.querySelectorAll("#nav a").forEach(a => a.addEventListener("click", () => { location.hash = a.dataset.view; }));
addEventListener("hashchange", route);

async function boot() {
  const ls = $("#link-state");
  try {
    const m = await get("/api/meta");
    ls.classList.add("up");
    ls.lastElementChild.textContent = `spočteno za ${num(m.computed_in, 1)} s`;
    $("#version").textContent = `verze ${m.version}`;
    const [a, b] = m.period;
    $("#chip-period").innerHTML = `měření <b>${fmtDate(a)} – ${fmtDate(b)}</b>`;
    $("#chip-tariff").innerHTML = `plyn <b>${num(m.tariffs.gas, 2)}</b> · elektřina <b>${num(m.tariffs.electricity, 2)}</b> Kč/kWh`;
    const o = await get("/api/overview");
    const c = o.summary.counts;
    $("#nav-faults").textContent = c.critical + c.warning + c.info;
    const dot = document.querySelector('#nav a[data-view="faults"] .dot');
    dot.className = "dot " + (c.critical ? "bad" : c.warning ? "warn" : "");
    document.querySelector('#nav a[data-view="overview"] .dot').className = "dot " + (o.total.saving_pct > 5 ? "warn" : "");
  } catch (e) {
    ls.classList.add("down");
    ls.lastElementChild.textContent = "server neodpovídá";
  }
  route();
}

function fmtDate(iso) {
  const d = new Date(iso);
  return `${d.getDate()}. ${d.getMonth() + 1}. ${d.getFullYear()}`;
}
function fmtStamp(iso) {
  const d = new Date(iso);
  const day = (d.getDay() + 6) % 7;
  return `${DAYS[day]} ${d.getDate()}. ${d.getMonth() + 1}. ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function legend(items) {
  return `<div class="legend">${items.map(i =>
    `<span><i class="${i.kind || ""}" style="background:${i.color};color:${i.color}"></i>${esc(i.name)}</span>`).join("")}</div>`;
}

function findingItem(f, compact) {
  const ev = (f.evidence || []).map(e =>
    `<span>${esc(e.label)} <b>${typeof e.value === "number" ? num(e.value, Number.isInteger(e.value) || Math.abs(e.value) >= 10 ? 0 : 1) : esc(e.value)}${e.unit ? " " + esc(e.unit) : ""}</b></span>`).join("");
  return `<li class="${f.severity}" data-id="${esc(f.id)}">
    <span class="mark" aria-hidden="true"></span>
    <div>
      <div class="head"><span class="cat">${esc(f.category)}</span>${esc(f.title)}</div>
      <div class="detail">${esc(f.detail)}</div>
      <div class="evidence">${ev}</div>
      <div class="rec">${esc(f.recommendation)}</div>
    </div>
    <span class="sev">${SEV[f.severity]}</span>
  </li>`;
}

// --- 1) přehled ----------------------------------------------------------------
async function renderOverview(el) {
  const o = await get("/api/overview");
  const b = o.baseline, opt = o.optimal, t = o.total;
  const counts = o.summary.counts;
  el.innerHTML = `<div class="wrap">
    <p class="lead">Závod z <b>bms-monitoru</b> — kotelna 2 × 400 kW, ohřívače tří VZT jednotek a tři větve
      ústředního topení. Z týdne měření (${esc(fmtDate(o.summary.period[0]))} – ${esc(fmtDate(o.summary.period[1]))},
      venku průměrně ${num(o.summary.t_out_mean, 1)} °C) diagnostika našla <b>${counts.critical} kritické
      a ${counts.warning + counts.info} dalších zjištění</b>. Roční bilance je spočtená na typický rok pro Prahu
      (${num(b.heating_hours)} topných hodin).</p>
    <div class="tiles">
      <div class="tile loss"><div class="cap">Vytápění dnes</div><div class="big">${kKc(b.cost)}<small>/rok</small></div>
        <div class="sub">plyn ${num(b.gas_kwh / 1000)} MWh · čerpadla ${num(b.pump_kwh / 1000, 1)} MWh</div></div>
      <div class="tile"><div class="cap">Po optimalizaci</div><div class="big">${kKc(opt.cost)}<small>/rok</small></div>
        <div class="sub">plyn ${num(opt.gas_kwh / 1000)} MWh · čerpadla ${num(opt.pump_kwh / 1000, 1)} MWh</div></div>
      <div class="tile money"><div class="cap">Úspora</div><div class="big">${kKc(t.saving_kc)}<small>/rok</small></div>
        <div class="sub">−${num(t.saving_pct, 1)} % nákladů · −${num(t.saving_co2_t, 0)} t CO₂/rok</div></div>
      <div class="tile"><div class="cap">Investice</div><div class="big">${kKc(t.invest)}</div>
        <div class="sub">prostá návratnost ${years(t.payback_y)} · NPV 10 let ${kKc(t.npv)}</div></div>
    </div>
    <div class="grid2">
      <div class="panel"><h3>Kam utíkají peníze — roční náklady krok po kroku</h3>
        <div id="wf"></div>
        <div class="note">Každý sloupec je změna nákladů proti stavu po předchozím kroku, v pořadí, ve kterém
          se opatření na stavbě dělají. Vyčištění sítka a vyvážení náklady mírně <b>zvýší</b> — do studeného skladu
          konečně poteče voda; vydělá se na tom až nižší křivkou, kterou to umožní.</div>
      </div>
      <div class="panel"><h3>Co je špatně <span class="count ${counts.critical ? "bad" : ""}">${o.findings.length}</span></h3>
        <ul class="finds compact">${o.findings.map(f => findingItem(f, true)).join("")}</ul>
        <div class="note">Kliknutím se zjištění rozbalí. Úplné důkazy jsou na obrazovce <a href="#faults" style="color:var(--accent)">Závady</a>.</div>
      </div>
    </div>
    <div class="panel"><h3>Balíčky opatření</h3>
      <table class="t"><thead><tr><th>Balíček</th><th class="n">Investice</th><th class="n">Úspora / rok</th><th class="n">Návratnost</th><th class="n">NPV 10 let</th></tr></thead>
      <tbody>${o.packages.map(p => `<tr><td>${esc(p.name)}<div class="what">${p.ids.map(id => esc(o.measures.find(m => m.id === id).name)).join(" · ")}</div></td>
        <td class="n">${kc(p.invest)}</td><td class="n ${p.saving_kc >= 0 ? "pos" : "neg"}">${kc(p.saving_kc)}</td>
        <td class="n">${years(p.payback_y)}</td><td class="n">${kc(p.npv)}</td></tr>`).join("")}
        <tr class="sum"><td>Celkem</td><td class="n">${kc(t.invest)}</td><td class="n pos">${kc(t.saving_kc)}</td><td class="n">${years(t.payback_y)}</td><td class="n">${kc(t.npv)}</td></tr>
      </tbody></table>
    </div>
  </div>`;

  const items = [{ label: "Dnes", value: b.cost, kind: "total", color: "var(--s2)",
    tip: [{ name: "náklady", value: kc(b.cost) }, { name: "plyn", value: num(b.gas_kwh / 1000) + " MWh" }] }];
  o.measures.forEach(m => items.push({
    label: m.name, value: -m.saving_kc,
    tip: [{ name: m.saving_kc >= 0 ? "úspora" : "navýšení", value: kc(Math.abs(m.saving_kc)) + "/rok" },
      { name: "plyn", value: (m.saving_gas_kwh >= 0 ? "−" : "+") + num(Math.abs(m.saving_gas_kwh) / 1000, 1) + " MWh" },
      { name: "elektřina", value: (m.saving_el_kwh >= 0 ? "−" : "+") + num(Math.abs(m.saving_el_kwh)) + " kWh" },
      { name: "investice", value: kc(m.invest) }, { name: "návratnost", value: years(m.payback_y) }],
  }));
  items.push({ label: "Po optimalizaci", value: opt.cost, kind: "total", color: "var(--s1)",
    tip: [{ name: "náklady", value: kc(opt.cost) }, { name: "úspora", value: kc(t.saving_kc) }] });
  Charts.waterfall($("#wf"), { items, height: 300, fmt: v => num(v / 1e6, 2) + " M", label: "Vodopád ročních nákladů" });
  el.querySelectorAll(".finds.compact > li").forEach(li => li.addEventListener("click", () => li.classList.toggle("open")));
}

// --- 2) závady -------------------------------------------------------------------
async function renderFaults(el) {
  const [d, ts] = await Promise.all([get("/api/diagnosis"), get("/api/timeseries")]);
  const hours = ts.ts.map((_, i) => i * 0.25);
  const tipX = h => fmtStamp(ts.ts[Math.round(h / 0.25)]);
  const xFmt = h => DAYS[Math.floor(h / 24) % 7] + (h % 24 ? ` ${h % 24}h` : "");
  const xTicks = [0, 24, 48, 72, 96, 120, 144, 168];
  const S = ts.series;
  const c = d.summary.counts;

  el.innerHTML = `<div class="wrap">
    <p class="lead">Diagnostika vidí jen to, co by se dalo opravdu naměřit — teploty, povely ventilů, kalorimetr na
      rozdělovači a otáčky čerpadla. Průtoky větví se neměří; dopočítávají se z tepelné bilance prostorů
      (<b>virtuální průtokoměry</b>) a porovnávají s hydraulickým modelem. Kde se měření a model rozejdou, je ve
      větvi něco, o čem projekt neví.</p>
    <div class="panel"><h3>Zjištění <span class="count bad">${c.critical} kritické</span><span class="count warn">${c.warning} upozornění</span><span class="count">${c.info} info</span></h3>
      <ul class="finds">${d.findings.map(f => findingItem(f)).join("")}</ul></div>
    <div class="panel"><h3>Virtuální průtokoměry větví</h3>
      <table class="t"><thead><tr><th>Větev</th><th>Regulace</th><th class="n">Průtok z bilance</th><th class="n">Model</th><th class="n">Návrh</th>
        <th style="min-width:150px">Průtok vůči návrhu</th><th class="n">Ochlazení</th><th class="n">Prostor / žádaná</th><th class="n">Přetopeno</th></tr></thead>
      <tbody>${d.branches.map(r => {
        const pct = Math.min(r.ratio / 2.5, 1) * 100;
        const cls = r.ratio > 1.3 ? "over" : r.ratio < 0.85 && r.control === "none" ? "under" : "";
        return `<tr><td>${esc(r.name)}${r.derived ? `<div class="what">zpátečka dopočtená z kalorimetru</div>` : ""}</td>
          <td>${r.control === "zone" ? `ventil ${num(r.valve_cmd)} %` : `<span class="mute">bez regulace</span>`}</td>
          <td class="n">${num(r.flow_est, 2)} m³/h</td><td class="n">${num(r.flow_model, 2)}</td><td class="n">${num(r.flow_design, 2)}</td>
          <td><div class="ratio" title="${num(r.ratio * 100)} % návrhu"><i class="${cls}" style="width:${pct}%"></i><b style="left:${100 / 2.5}%"></b></div>
            <div class="what">${num(r.ratio * 100)} % návrhu</div></td>
          <td class="n">${num(r.delta_t, 1)} K</td>
          <td class="n">${num(r.t_room, 1)} / ${num(r.setpoint, 0)} °C</td>
          <td class="n">${r.over_hours ? num(r.over_hours) + " h" : "—"}</td></tr>`;
      }).join("")}</tbody></table>
      <div class="note">Regulované větve (zónový ventil) průtok samy škrtí, proto mají hluboko pod návrhem — to je v pořádku.
        Rozhoduje, jestli sedí s modelem při daném povelu. Svislá čárka v pruhu = 100 % návrhu.</div>
    </div>
    <div class="grid2 even">
      <div class="panel"><h3>Čidlo rozdělovače proti ostatním</h3>
        ${legend([{ name: "čidlo regulace", color: "var(--s2)" }, { name: "kalorimetr", color: "var(--s1)" }, { name: "přívod VZT 3 (větev)", color: "var(--s3)" }])}
        <div id="ch-hdr"></div>
        <div class="note">Kalorimetr a čidla větví se shodují, čidlo regulace ukazuje o <b>${num(Math.abs(d.sensor_offset), 1)} K</b> méně —
          kotelna proto topí o tolik víc.</div></div>
      <div class="panel"><h3>Zamrzlé čidlo zpátečky šaten</h3>
        ${legend([{ name: "přívod šaten", color: "var(--s2)" }, { name: "zpátečka šaten (čidlo)", color: "var(--s1)" }])}
        <div id="ch-stuck"></div>
        <div class="note">Přívod se během týdne mění o víc než 20 K, čidlo zpátečky stojí na jedné hodnotě.</div></div>
    </div>
    <div class="panel"><h3>Teplota v prostoru</h3>
      <div class="toolbar"><span class="lbl2">Větev</span><div class="seg" id="room-sel">${d.branches.map((r, i) =>
        `<button data-id="${r.id}" class="${i === 0 ? "on" : ""}">${esc(r.short)}</button>`).join("")}</div></div>
      ${legend([{ name: "teplota prostoru", color: "var(--s1)" }, { name: "žádaná", color: "var(--mute)", kind: "dash" }])}
      <div id="ch-room"></div>
      <div id="ch-valve-wrap"><h3 style="margin-top:14px">Povel zónového ventilu</h3><div id="ch-valve"></div></div>
    </div>
  </div>`;
  el.querySelectorAll(".finds > li").forEach(li => li.addEventListener("click", () => li.classList.toggle("open")));

  const base = { x: hours, xTicks, xFmt, tipX, height: 210 };
  Charts.line($("#ch-hdr"), { ...base, yFmt: v => num(v, 1) + " °C", series: [
    { name: "čidlo regulace", values: S["header.t_supply"], color: "var(--s2)" },
    { name: "kalorimetr", values: S["meter.t_supply"], color: "var(--s1)" },
    { name: "přívod VZT 3", values: S["vzt3.t_supply"], color: "var(--s3)" }] });
  Charts.line($("#ch-stuck"), { ...base, yFmt: v => num(v, 1) + " °C", series: [
    { name: "přívod šaten", values: S["sat.t_supply"], color: "var(--s2)" },
    { name: "zpátečka šaten", values: S["sat.t_return"], color: "var(--s1)" }] });

  const drawRoom = id => {
    Charts.line($("#ch-room"), { ...base, height: 230, yFmt: v => num(v, 1) + " °C", series: [
      { name: "teplota prostoru", values: S[`${id}.t_room`], color: "var(--s1)" },
      { name: "žádaná", values: S[`${id}.setpoint`], color: "var(--mute)", dash: "5 4", width: 1.5 }] });
    const has = S[`${id}.valve_cmd`];
    $("#ch-valve-wrap").style.display = has ? "" : "none";
    if (has) Charts.line($("#ch-valve"), { ...base, height: 150, yMin: 0, yMax: 100, yFmt: v => num(v, 0) + " %",
      series: [{ name: "povel ventilu", values: has, color: "var(--s4)" }] });
  };
  drawRoom(d.branches[0].id);
  $("#room-sel").addEventListener("click", e => {
    const b = e.target.closest("button"); if (!b) return;
    $("#room-sel").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b));
    drawRoom(b.dataset.id);
  });
}

// --- 3) simulace větví -------------------------------------------------------------
const sim = { base: "baseline", t_out: -5, occupied: true, overrides: {}, last: null };

async function renderHydraulics(el) {
  const p = await get("/api/plan");
  const states = [{ id: "baseline", label: "Dnes" }, ...p.steps.map(s => ({ id: s.id, label: "+ " + s.label }))];
  el.innerHTML = `<div class="wrap">
    <p class="lead">Ustálený stav celé soustavy: hydraulika páteře a větví, tělesa a teploty prostorů, zónová regulace.
      Vyberte stav soustavy (dnešní, nebo po jednotlivých opatřeních), venkovní teplotu a cokoli přenastavte —
      model dopočítá průtoky, tlaky a teploty. Nevyvážené větve se poznají podle poměru k návrhu a malého ochlazení vody.</p>
    <div class="toolbar">
      <span class="lbl2">Stav</span>
      <select class="select" id="st-sel">${states.map(s => `<option value="${s.id}">${esc(s.label)}</option>`).join("")}</select>
      <span class="lbl2" style="margin-left:10px">Venku</span>
      <input type="range" id="t-out" min="-15" max="15" step="1" value="${sim.t_out}" style="width:180px;accent-color:var(--accent)">
      <output id="t-out-v" style="font-weight:650;font-variant-numeric:tabular-nums">${sim.t_out} °C</output>
      <div class="seg" id="occ"><button data-v="1" class="on">provoz</button><button data-v="0">mimo provoz</button></div>
    </div>
    <div class="panel" style="padding:8px 10px"><div id="plan"></div></div>
    <div class="grid3">
      <div class="panel"><h3>Nastavení</h3><div id="ctl"></div></div>
      <div class="panel"><h3>Tlakový profil páteře</h3>
        ${legend([{ name: "k dispozici na uzlu", color: "var(--s1)" }, { name: "potřeba větve při plném zatížení", color: "var(--s2)", kind: "dot" }])}
        <div id="ch-dp"></div>
        <div class="note">Oranžový bod = tlak, který větev potřebuje na návrhový průtok při plně otevřených ventilech.
          Co je nad ním, musí větev zmařit na ventilech — bez vyvážení o to přetéká. Při částečném zatížení zónové
          ventily přivírají a potřebují méně, proto může být čára i pod bodem.</div></div>
      <div class="panel"><h3>Výsledek</h3><ul class="kv" id="res"></ul>
        <div style="margin-top:10px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <button class="btn primary" id="btn-annual">Spočítat roční náklady</button><span class="mute" id="annual-note" style="font-size:12px"></span></div></div>
    </div>
  </div>`;
  $("#st-sel").value = sim.base;
  $("#st-sel").addEventListener("change", e => { sim.base = e.target.value; sim.overrides = {}; runSim(true); });
  $("#t-out").addEventListener("input", e => { sim.t_out = +e.target.value; $("#t-out-v").textContent = `${sim.t_out} °C`; runSim(); });
  $("#occ").addEventListener("click", e => {
    const b = e.target.closest("button"); if (!b) return;
    sim.occupied = b.dataset.v === "1";
    $("#occ").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b));
    runSim();
  });
  $("#btn-annual").addEventListener("click", () => runSim(false, true));
  await runSim(true);
}

let simTimer = null, simSeq = 0;
function runSim(resetControls = false, annual = false) {
  clearTimeout(simTimer);
  return new Promise(res => {
    simTimer = setTimeout(async () => {
      const seq = ++simSeq;
      if (annual) { $("#btn-annual").disabled = true; $("#annual-note").textContent = "počítám typický rok…"; }
      const body = { base: sim.base, t_out: sim.t_out, occupied: sim.occupied, annual, ...sim.overrides };
      const r = await api("/api/simulate", body);
      if (seq !== simSeq && !annual) return res();
      sim.last = r;
      drawPlan(r);
      if (resetControls || !$("#ctl").children.length) drawControls(r.network);
      drawResult(r);
      drawDp(r);
      if (annual) $("#btn-annual").disabled = false;
      res();
    }, resetControls || annual ? 0 : 120);
  });
}

function drawControls(n) {
  const o = sim.overrides;
  const v = (k, d) => o[k] ?? d;
  const slider = (id, label, min, max, step, val, fmt) =>
    `<div class="ctl"><label for="${id}">${label}</label><output id="${id}-v">${fmt(val)}</output>
      <input type="range" id="${id}" min="${min}" max="${max}" step="${step}" value="${val}"></div>`;
  const mode = v("pump_mode", n.pump_mode);
  $("#ctl").innerHTML = `
    ${slider("c-slope", "Sklon ekvitermní křivky", 0.5, 5, 0.05, v("curve_slope", n.curve_slope), x => num(x, 2))}
    ${slider("c-shift", "Posun křivky", -15, 15, 0.5, v("curve_shift", n.curve_shift), x => (x >= 0 ? "+" : "") + num(x, 1) + " K")}
    <div class="ctl"><label>Čerpadlo</label><span></span>
      <div class="seg" id="c-mode" style="grid-column:1/-1"><button data-v="fixed" class="${mode === "fixed" ? "on" : ""}">pevné otáčky</button><button data-v="dp" class="${mode === "dp" ? "on" : ""}">Δp na konci páteře</button></div></div>
    <div id="c-pump-wrap">${mode === "fixed"
      ? slider("c-speed", "Otáčky čerpadla", 0.4, 1, 0.01, v("pump_speed", n.pump_speed), x => num(x * 100) + " %")
      : slider("c-dp", "Žádaná Δp na konci páteře", 5, 120, 1, v("dp_target", n.dp_target ?? 35), x => num(x) + " kPa")}</div>
    <div class="ctl"><label>Útlum mimo provoz</label><span></span>
      <div class="seg" id="c-sb" style="grid-column:1/-1"><button data-v="1" class="${v("setback", n.setback_enabled) ? "on" : ""}">zapnutý</button><button data-v="0" class="${v("setback", n.setback_enabled) ? "" : "on"}">vypnutý</button></div></div>
    <h3 style="margin-top:12px">Vyvažovací ventily [otáčky 0,5–4]</h3>
    <div class="turns">${n.branches.map(b => `<span>${esc(b.short)} <small class="mute">DN${b.bv_dn}</small></span>
      <input type="number" min="0.5" max="4" step="0.1" data-bid="${b.id}" value="${(o.turns || {})[b.id] ?? b.bv_turns}">`).join("")}</div>
    <div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap">
      <button class="btn" id="c-repair">${o.repair ? "✓ závady odstraněny" : "Odstranit závady"}</button>
      <button class="btn" id="c-reset">Vrátit nastavení stavu</button></div>`;

  const bind = (id, key, fmt, tr = x => x) => {
    const inp = $("#" + id); if (!inp) return;
    inp.addEventListener("input", e => { o[key] = tr(+e.target.value); $("#" + id + "-v").textContent = fmt(+e.target.value); runSim(); });
  };
  bind("c-slope", "curve_slope", x => num(x, 2));
  bind("c-shift", "curve_shift", x => (x >= 0 ? "+" : "") + num(x, 1) + " K");
  bind("c-speed", "pump_speed", x => num(x * 100) + " %");
  bind("c-dp", "dp_target", x => num(x) + " kPa");
  $("#c-mode").addEventListener("click", e => {
    const b = e.target.closest("button"); if (!b) return;
    o.pump_mode = b.dataset.v; drawControls(n); runSim();
  });
  $("#c-sb").addEventListener("click", e => {
    const b = e.target.closest("button"); if (!b) return;
    o.setback = b.dataset.v === "1"; drawControls(n); runSim();
  });
  $("#ctl").querySelectorAll("input[data-bid]").forEach(inp => inp.addEventListener("change", e => {
    o.turns = { ...(o.turns || {}), [e.target.dataset.bid]: +e.target.value }; runSim();
  }));
  $("#c-repair").addEventListener("click", () => { o.repair = !o.repair; drawControls(n); runSim(); });
  $("#c-reset").addEventListener("click", () => { sim.overrides = {}; runSim(true); });
}

function drawResult(r) {
  const s = r.steady;
  let html = `
    <li><span>Teplota vody přívod / zpátečka</span><span>${num(s.t_supply, 1)} / ${num(s.t_return, 1)} °C</span></li>
    <li><span>Průtok čerpadlem</span><span>${num(s.q_total, 1)} m³/h</span></li>
    <li><span>Teplo do budovy</span><span>${num(s.heat_kw)} kW</span></li>
    <li><span>Plyn (účinnost kotle ${num(s.boiler_eta * 100, 1)} %)</span><span>${num(s.gas_kw)} kW</span></li>
    <li><span>Čerpadlo — otáčky, tlak, příkon</span><span>${num(s.pump_speed * 100)} % · ${num(s.pump_head)} kPa · ${num(s.pump_kw, 2)} kW</span></li>
    <li><span>Náklady v tomto bodě</span><span>${num(s.gas_kw * 1.6 + s.pump_kw * 4.2)} Kč/h</span></li>`;
  if (r.annual) {
    const d = r.baseline_cost - r.annual.cost;
    html += `<li><span>Roční náklady (typický rok)</span><span>${kKc(r.annual.cost)}</span></li>
      <li><span>Proti dnešku</span><span class="${d >= 0 ? "pos" : "neg"}">${d >= 0 ? "−" : "+"}${kKc(Math.abs(d))}/rok</span></li>`;
    $("#annual-note").textContent = "";
  }
  $("#res").innerHTML = html;
}

function drawDp(r) {
  const s = r.steady, n = r.network;
  const nodes = s.node_dp.map((_, i) => i);
  const need = n.branches.map(b => [b.node, b.dp_need]);
  Charts.line($("#ch-dp"), {
    x: nodes, xTicks: nodes, height: 200,
    xFmt: i => i === 0 ? "rozdělovač" : n.branches.find(b => b.node === i)?.short || i,
    yFmt: v => num(v) + " kPa", yMin: 0,
    series: [{ name: "Δp na uzlu", values: s.node_dp, color: "var(--s1)" }],
    points: [{ name: "potřeba větve", data: need, color: "var(--s2)" }],
  });
}

function drawPlan(r) {
  const s = r.steady, n = r.network;
  const W = 1400, H = 590, yS = 140, yR = 186, x0 = 236;
  const xs = k => 392 + (k - 1) * 176;
  const lastX = xs(6) + 30;
  const br = id => s.branches.find(b => b.id === id);
  let g = `<svg class="plan" viewBox="0 0 ${W} ${H}" role="img" aria-label="Schéma otopné soustavy">`;

  // páteř
  g += `<path class="pipe warm" stroke-width="8" d="M${x0},${yS} H${lastX}"/>`;
  g += `<path class="pipe cold" stroke-width="8" d="M${x0},${yR} H${lastX}"/>`;
  const segFlow = k => n.branches.filter(b => b.node >= k).reduce((a, b) => a + br(b.id).flow, 0);
  for (let k = 1; k <= 6; k++) {
    const a = k === 1 ? x0 + 60 : xs(k - 1) + 22, z = xs(k) - 22;
    const on = segFlow(k) > 0.05 ? "" : "off";
    g += `<path class="flow warm ${on}" d="M${a},${yS} H${z + 0}"/>`;
    g += `<path class="flow cold ${on}" d="M${z + 44},${yR} H${k === 1 ? x0 : xs(k - 1) + 22}"/>`;
    g += `<text class="dp-tag" x="${xs(k)}" y="${yS - 16}" text-anchor="middle">Δp ${num(s.node_dp[k])} kPa</text>`;
  }

  // kotelna
  g += `<rect class="case" x="14" y="86" width="${x0 - 14}" height="400" rx="10"/>
    <text class="ttl" x="30" y="112">Kotelna</text><text class="lbl" x="30" y="128">2 × 400 kW, kondenzační</text>
    <rect class="case" x="30" y="214" width="86" height="72" rx="6" style="fill:#223047"/><text class="lbl-sm" x="73" y="254" text-anchor="middle">Kotel 1</text>
    <rect class="case" x="130" y="214" width="86" height="72" rx="6" style="fill:#223047"/><text class="lbl-sm" x="173" y="254" text-anchor="middle">Kotel 2</text>
    <text class="lbl" x="30" y="318">přívod / zpátečka</text>
    <text class="v-lg t-warm" x="30" y="346">${num(s.t_supply, 1)}</text><text class="v-lg t-cold" x="118" y="346">${num(s.t_return, 1)}</text><text class="lbl" x="200" y="346">°C</text>
    <text class="lbl" x="30" y="372">teplo</text><text class="v" x="${x0 - 12}" y="372" text-anchor="end">${num(s.heat_kw)} kW</text>
    <text class="lbl" x="30" y="394">plyn</text><text class="v" x="${x0 - 12}" y="394" text-anchor="end">${num(s.gas_kw)} kW</text>
    <text class="lbl" x="30" y="416">účinnost</text><text class="v ${s.boiler_eta > 1.0 ? "t-ok" : "t-warn"}" x="${x0 - 12}" y="416" text-anchor="end">${num(s.boiler_eta * 100, 1)} %</text>
    <text class="lbl" x="30" y="438">průtok</text><text class="v" x="${x0 - 12}" y="438" text-anchor="end">${num(s.q_total, 1)} m³/h</text>
    <text class="lbl" x="30" y="460">křivka</text><text class="v-sm" x="${x0 - 12}" y="460" text-anchor="end">${num(n.curve_slope, 2)} / ${n.curve_shift >= 0 ? "+" : ""}${num(n.curve_shift, 1)} K</text>
    ${n.header_sensor_error ? `<text class="lbl-sm t-bad" x="30" y="478" style="fill:var(--bad)!important">čidlo rozdělovače ${num(n.header_sensor_error, 1)} K</text>` : ""}`;
  // čerpadlo
  const px = x0 + 34;
  g += `<circle cx="${px}" cy="${yS}" r="17" class="case" style="fill:var(--panel)"/>
    <g class="rotor" style="animation-duration:${(1.6 / Math.max(s.pump_speed, 0.3)).toFixed(2)}s"><path d="M${px - 9},${yS} L${px + 9},${yS} M${px},${yS - 9} L${px},${yS + 9}" stroke="var(--accent)" stroke-width="2.4"/></g>
    <text class="lbl-sm" x="${px}" y="${yS - 46}" text-anchor="middle">čerpadlo ${n.pump_mode === "dp" ? "Δp" : "pevné"}</text>
    <text class="v-sm" x="${px}" y="${yS - 30}" text-anchor="middle">${num(s.pump_speed * 100)} % · ${num(s.pump_kw, 2)} kW</text>`;

  // větve
  n.branches.forEach(b => {
    const r_ = br(b.id), x = xs(b.node), xl = x - 22, xr = x + 22, yTop = 300;
    const ratio = r_.ratio;
    const on = r_.flow > 0.05 ? "" : "off";
    g += `<path class="pipe warm" stroke-width="6" d="M${xl},${yS} V${yTop}"/><path class="pipe cold" stroke-width="6" d="M${xr},${yTop} V${yR}"/>
      <path class="flow warm ${on}" d="M${xl},${yS} V${yTop}"/><path class="flow cold ${on}" d="M${xr},${yTop} V${yR}"/>`;
    // regulační ventil
    if (b.kvs) {
      const vy = 236, stuck = b.valve_stuck != null;
      g += `<path class="valve ${stuck ? "stuck" : ""}" d="M${xl - 9},${vy - 7} L${xl + 9},${vy + 7} L${xl + 9},${vy - 7} L${xl - 9},${vy + 7} Z"/>
        <line x1="${xl}" y1="${vy}" x2="${xl}" y2="${vy - 13}" stroke="var(--mute)" stroke-width="1.5"/><rect x="${xl - 6}" y="${vy - 20}" width="12" height="7" rx="1.5" fill="${stuck ? "var(--bad)" : "var(--mute)"}"/>
        <text class="v-sm" x="${xl - 14}" y="${vy + 4}" text-anchor="end">${num((r_.valve ?? 0) * 100)} %</text>`;
    }
    // vyvažovací ventil
    const by = 250;
    g += `<path class="valve bv" d="M${xr - 9},${by - 7} L${xr + 9},${by + 7} L${xr + 9},${by - 7} L${xr - 9},${by + 7} Z"/>
      <line x1="${xr - 7}" y1="${by - 13}" x2="${xr + 7}" y2="${by - 13}" stroke="var(--accent)" stroke-width="2"/>
      <text class="v-sm" x="${xr + 14}" y="${by + 4}">${num(b.bv_turns, 1)} ot.</text>`;
    if (b.kv_obstruction) g += `<circle cx="${xr}" cy="${by + 26}" r="6" fill="var(--bad)"/><text class="lbl-sm" x="${xr + 10}" y="${by + 30}" style="fill:#ffb9b3!important">sítko</text>`;

    // zóna
    const cold = r_.t_room < r_.setpoint - 0.5, hot = r_.t_room > r_.setpoint + 1.5;
    const tcls = cold ? "t-bad" : hot ? "t-warn" : "t-ok";
    const bw = 140, bx = x - 75, barW = Math.min(ratio / 2.5, 1) * bw;
    const fcls = ratio > 1.3 ? "over" : (b.control === "none" && ratio < 0.85) ? "under" : "";
    g += `<rect class="case ${b.valve_stuck != null || b.kv_obstruction ? "bad" : ""}" x="${x - 84}" y="${yTop}" width="168" height="272" rx="10"/>
      <text class="ttl" x="${bx}" y="${yTop + 22}">${esc(b.short)}</text>
      <text class="lbl-sm" x="${bx}" y="${yTop + 37}">${{ radiators: "radiátory", ahu_coil: "ohřívač VZT", unit_heaters: "teplovzdušné jednotky" }[b.kind]}</text>
      <text class="v-lg ${tcls}" x="${bx}" y="${yTop + 72}">${num(r_.t_room, 1)} °C</text>
      <text class="lbl" x="${bx}" y="${yTop + 89}">žádaná ${num(r_.setpoint, 1)} °C${hot ? " · přetopeno" : cold ? " · zima" : ""}</text>
      <text class="lbl" x="${bx}" y="${yTop + 113}">průtok / návrh</text>
      <rect class="bar-bg" x="${bx}" y="${yTop + 120}" width="${bw}" height="8" rx="4"/>
      <rect class="bar-fill ${fcls}" x="${bx}" y="${yTop + 120}" width="${Math.max(barW, 2)}" height="8" rx="4"/>
      <rect class="bar-mark" x="${bx + bw / 2.5 - 1}" y="${yTop + 116}" width="2" height="16"/>
      <text class="v-sm" x="${bx}" y="${yTop + 148}">${num(r_.flow, 2)} m³/h · ${num(ratio * 100)} %</text>
      <text class="lbl" x="${bx}" y="${yTop + 172}">zpátečka</text><text class="v-sm t-cold" x="${bx + bw}" y="${yTop + 172}" text-anchor="end">${num(r_.t_return, 1)} °C</text>
      <text class="lbl" x="${bx}" y="${yTop + 192}">ochlazení</text><text class="v-sm ${r_.t_supply - r_.t_return < 10 && r_.flow > 0.2 ? "t-warn" : ""}" x="${bx + bw}" y="${yTop + 192}" text-anchor="end">${num(r_.t_supply - r_.t_return, 1)} K</text>
      <text class="lbl" x="${bx}" y="${yTop + 212}">výkon</text><text class="v-sm" x="${bx + bw}" y="${yTop + 212}" text-anchor="end">${num(r_.power)} kW</text>
      <text class="lbl" x="${bx}" y="${yTop + 232}">Δp větve</text><text class="v-sm" x="${bx + bw}" y="${yTop + 232}" text-anchor="end">${num(r_.dp)} kPa</text>
      <text class="lbl-sm" x="${bx}" y="${yTop + 256}" ${b.valve_stuck != null ? 'style="fill:#ffb9b3!important"' : ""}>${b.kvs ? (b.valve_stuck != null ? `pohon zaseklý na ${num(b.valve_stuck * 100)} %` : `zónový ventil Kvs ${num(b.kvs, 1)}`) : "bez regulace"}</text>`;
  });
  g += `</svg>`;
  $("#plan").innerHTML = g;
}

// --- 4) optimální nastavení ---------------------------------------------------------
async function renderOptimize(el) {
  const p = await get("/api/plan");
  const b = p.balance;
  const groups = [...new Set(p.parameters.map(r => r.group))];
  const fmtV = (v, unit) => v == null ? "—" : typeof v === "number" ? num(v, unit === "" ? 2 : unit === "%" || unit === "kPa" ? 0 : 1) + (unit ? " " + unit : "") : esc(v);
  el.innerHTML = `<div class="wrap">
    <p class="lead">Návrh v pořadí, ve kterém se dělá na stavbě: nejdřív <b>opravy</b> (čidla, pohon, sítko), pak <b>hydraulické vyvážení</b>
      metodou indexové větve, <b>regulace čerpadla</b> na tlakovou diferenci, teprve potom <b>ekvitermní křivka</b> a nakonec <b>útlum</b>.
      Křivka se hledá jako nejnižší teplota vody, při které ještě všechny zóny dosáhnou žádané teploty, plus rezerva ${num(p.margin, 1)} K.</p>
    <div class="grid2">
      <div class="panel"><h3>Ekvitermní křivka</h3>
        ${legend([{ name: "dnes — skutečná teplota vody", color: "var(--s2)" }, { name: "projekt", color: "var(--mute)", kind: "dash" },
          { name: "navrženo", color: "var(--s1)" }, { name: "nutná teplota (model)", color: "var(--s3)", kind: "dot" }])}
        <div id="ch-curve"></div>
        <div class="note">Dnešní křivka je zvednutá o ${num(p.baseline.curve_shift, 1)} K a vadné čidlo přidává dalších
          ${num(-p.baseline.header_sensor_error, 1)} K. Po vyvážení stačí výrazně chladnější voda — hlavně v přechodném období,
          kde se topí nejvíc hodin.</div></div>
      <div class="panel"><h3>Čerpadlo po vyvážení</h3><ul class="kv">
        <li><span>Indexová (rozhodující) větev</span><span>${esc(p.optimal.branches.find(x => x.id === b.index).short)}</span></li>
        <li><span>Žádaná Δp na konci páteře</span><span>${num(b.dp_index, 1)} kPa</span></li>
        <li><span>Návrhový bod</span><span>${num(b.q_total, 1)} m³/h · ${num(b.pump_head)} kPa</span></li>
        <li><span>Otáčky v návrhovém bodě</span><span>${num(b.pump_speed * 100)} % (dnes 100 %)</span></li>
        <li><span>Režim</span><span>konstantní Δp na konci páteře</span></li></ul>
        ${b.warnings.length ? `<div class="note" style="color:var(--warn)">${b.warnings.map(esc).join("<br>")}</div>` : `<div class="note">Všechny vyvažovací ventily mají rozsah nastavení se zásobou.</div>`}
      </div>
    </div>
    <div class="grid2 even">
      <div class="panel"><h3>Vyvážení větví</h3>
        <table class="t"><thead><tr><th>Větev</th><th class="n">DN</th><th class="n">Dnes</th><th class="n">Nastavit</th><th class="n">Cílový průtok</th><th class="n">Návrh</th><th class="n">Ověření modelem</th></tr></thead>
        <tbody>${p.optimal.branches.map(x => {
          const now = p.baseline.branches.find(y => y.id === x.id);
          const chk = b.check[x.id];
          return `<tr><td>${esc(x.short)}${x.id === b.index ? ' <span class="mute">(indexová)</span>' : ""}</td><td class="n">${x.bv_dn}</td>
            <td class="n">${num(now.bv_turns, 1)} ot.</td><td class="n chg">${num(x.bv_turns, 1)} ot.</td>
            <td class="n">${num(b.targets[x.id], 2)} m³/h</td><td class="n mute">${num(x.q_design, 2)}</td>
            <td class="n ${Math.abs(chk - 1) < 0.05 ? "pos" : "neg"}">${num(chk * 100, 1)} %</td></tr>`;
        }).join("")}</tbody></table>
        <div class="note">Neregulované větve (šatny, sklad) nemají cíl z projektu, ale průtok, který při nové křivce dá právě žádanou
          teplotu — jejich tělesa jsou předimenzovaná a s projektovým průtokem by přetápěly dál.</div></div>
      <div class="panel"><h3>Všechny parametry — dnes → navrženo</h3>
        <table class="t"><tbody>${groups.map(gr => `<tr><td class="grp" colspan="3">${esc(gr)}</td></tr>` +
          p.parameters.filter(r => r.group === gr).map(r => `<tr><td>${esc(r.name)}</td><td class="n mute">${fmtV(r.now, r.unit)}</td>
            <td class="n chg">${fmtV(r.new, r.unit)}</td></tr>`).join("")).join("")}</tbody></table></div>
    </div>
  </div>`;
  const c = p.curves;
  Charts.line($("#ch-curve"), {
    x: c.map(r => r.t_out), height: 280, xTitle: "venkovní teplota [°C]",
    xFmt: v => num(v) + " °C", yFmt: v => num(v, 1) + " °C",
    series: [
      { name: "dnes (skutečnost)", values: c.map(r => r.now), color: "var(--s2)" },
      { name: "projekt", values: c.map(r => r.design), color: "var(--mute)", dash: "5 4", width: 1.5 },
      { name: "navrženo", values: c.map(r => r.new), color: "var(--s1)" }],
    points: [{ name: "nutná teplota", data: p.curve_points.map(r => [r.t_out, r.required]), color: "var(--s3)" }],
  });
}

// --- 5) úspory -------------------------------------------------------------------
async function renderSavings(el) {
  const [e, p] = await Promise.all([get("/api/economics"), get("/api/plan")]);
  const t = e.total, a = e.assumptions;
  const shorts = Object.fromEntries(p.optimal.branches.map(b => [b.id, b.short]));
  el.innerHTML = `<div class="wrap">
    <p class="lead">Roční spotřeba metodou teplotních tříd na typickém roce pro Prahu (${num(a.heating_hours)} topných hodin,
      topí se ve dnech pod 13 °C). Úspora každého opatření je rozdíl proti stavu po předchozím kroku — opatření se navzájem
      ovlivňují, takže se nesčítají zvlášť spočtené úspory. Ceny opatření jsou orientační bez DPH.</p>
    <div class="tiles">
      <div class="tile money"><div class="cap">Úspora za rok</div><div class="big">${kKc(t.saving_kc)}</div><div class="sub">−${num(t.saving_pct, 1)} % nákladů na vytápění</div></div>
      <div class="tile"><div class="cap">Investice celkem</div><div class="big">${kKc(t.invest)}</div><div class="sub">7 opatření, 4 výjezdy servisu</div></div>
      <div class="tile"><div class="cap">Prostá návratnost</div><div class="big">${years(t.payback_y)}</div><div class="sub">vnitřní výnosové procento ${t.irr == null ? "—" : num(t.irr * 100) + " %"}</div></div>
      <div class="tile"><div class="cap">Čistá současná hodnota</div><div class="big">${kKc(t.npv)}</div><div class="sub">${a.horizon} let, diskont ${num(a.discount * 100)} %, ceny +${num(a.escalation * 100)} %/rok</div></div>
    </div>
    <div class="panel"><h3>Opatření</h3>
      <table class="t"><thead><tr><th style="width:38%">Opatření</th><th class="n">Investice</th><th class="n">Plyn</th><th class="n">Elektřina</th><th class="n">Úspora / rok</th><th class="n">Návratnost</th><th class="n">NPV</th></tr></thead>
      <tbody>${e.measures.map(m => `<tr><td>${esc(m.name)}<div class="what">${esc(m.what)}</div></td>
        <td class="n">${kc(m.invest)}</td>
        <td class="n">${m.saving_gas_kwh >= 0 ? "−" : "+"}${num(Math.abs(m.saving_gas_kwh) / 1000, 1)} MWh</td>
        <td class="n">${m.saving_el_kwh >= 0 ? "−" : "+"}${num(Math.abs(m.saving_el_kwh))} kWh</td>
        <td class="n ${m.saving_kc >= 0 ? "pos" : "neg"}">${m.saving_kc >= 0 ? "" : "+"}${kc(Math.abs(m.saving_kc))}${m.saving_kc < 0 ? " navíc" : ""}</td>
        <td class="n">${years(m.payback_y)}</td><td class="n">${kc(m.npv)}</td></tr>`).join("")}
        <tr class="sum"><td>Celkem</td><td class="n">${kc(t.invest)}</td><td class="n">−${num(t.saving_gas_kwh / 1000, 1)} MWh</td><td class="n">−${num(t.saving_el_kwh)} kWh</td>
          <td class="n pos">${kc(t.saving_kc)}</td><td class="n">${years(t.payback_y)}</td><td class="n">${kc(t.npv)}</td></tr></tbody></table></div>
    <div class="grid2 even">
      <div class="panel"><h3>Kumulovaný tok peněz</h3><div id="ch-cash"></div>
        <div class="note">Investice v roce 0, potom roční úspora rostoucí s cenami energií.</div></div>
      <div class="panel"><h3>Kdy v roce se šetří — spotřeba plynu podle venkovní teploty</h3>
        ${legend([{ name: "dnes", color: "var(--s2)" }, { name: "po optimalizaci", color: "var(--s1)" }])}
        <div id="ch-bins"></div>
        <div class="note">Nejvíc plynu se spálí kolem 0 až +8 °C — mrazů je v roce málo. Proto rozhoduje hlavně křivka v přechodném období.</div></div>
    </div>
    <div class="grid2 even">
      <div class="panel"><h3>Komfort — kontrola, že se nešetří zimou</h3>
        <table class="t"><thead><tr><th>Zóna</th><th class="n">Pod žádanou dnes</th><th class="n">po optimalizaci</th><th class="n">Přetopeno dnes</th><th class="n">po optimalizaci</th></tr></thead>
        <tbody>${Object.keys(e.baseline.comfort).map(id => {
          const b0 = e.baseline.comfort[id], b1 = e.optimal.comfort[id];
          return `<tr><td>${esc(shorts[id])}</td><td class="n">${num(b0.under_kh)} Kh</td><td class="n">${num(b1.under_kh)} Kh</td>
            <td class="n">${num(b0.over_kh)} Kh</td><td class="n ${b1.over_kh < b0.over_kh - 1 ? "pos" : ""}">${num(b1.over_kh)} Kh</td></tr>`;
        }).join("")}</tbody></table>
        <div class="note">Kelvinhodiny v obsazených hodinách: pod žádanou o víc než 0,5 K, přetopeno o víc než 1 K. Hala A se přetápí
          vlastními zisky strojů v přechodném období — to topení nevyřeší, patří to do řízení větrání.</div></div>
      <div class="panel"><h3>Předpoklady</h3><ul class="kv">
        <li><span>Cena plynu</span><span>${num(a.tariffs.gas, 2)} Kč/kWh</span></li>
        <li><span>Cena elektřiny</span><span>${num(a.tariffs.electricity, 2)} Kč/kWh</span></li>
        <li><span>Emise plyn / elektřina</span><span>${num(a.tariffs.co2_gas, 3)} / ${num(a.tariffs.co2_el, 3)} kg CO₂/kWh</span></li>
        <li><span>Hodnocené období</span><span>${a.horizon} let</span></li>
        <li><span>Diskontní sazba</span><span>${num(a.discount * 100, 1)} %</span></li>
        <li><span>Růst cen energií</span><span>${num(a.escalation * 100, 1)} % ročně</span></li>
        <li><span>Topné hodiny typického roku</span><span>${num(a.heating_hours)} h</span></li></ul>
        <div class="note">Ceny se načítají z <b>bms-monitor/plant.py</b>, aby oba projekty počítaly se stejnými sazbami.</div></div>
    </div>
  </div>`;
  const cf = t.cashflow;
  Charts.columns($("#ch-cash"), { values: cf.map(r => r.cum), labels: cf.map(r => String(r.year)), height: 220,
    fmt: (v, full) => full ? kc(v) : num(v / 1e6, 1) + " M", name: "kumulovaně", tipTitle: i => `rok ${cf[i].year}` });
  const tb = e.baseline.by_temp, to = Object.fromEntries(e.optimal.by_temp.map(r => [r.t_out, r.gas_kwh]));
  Charts.line($("#ch-bins"), { x: tb.map(r => r.t_out), height: 220, xFmt: v => num(v) + " °C", yMin: 0,
    yFmt: (v, full) => full ? num(v / 1000, 1) + " MWh" : num(v / 1000, 0),
    series: [{ name: "dnes", values: tb.map(r => r.gas_kwh), color: "var(--s2)" },
      { name: "po optimalizaci", values: tb.map(r => to[r.t_out] ?? null), color: "var(--s1)" }] });
}

// --- 6) bms-monitor ------------------------------------------------------------------
async function renderBms(el) {
  const s = await get("/api/bms");
  const st = s.stats || {};
  const row = (k, label, unit) => st[k] ? `<tr><td>${label}</td><td class="n">${num(st[k].n)}</td><td class="n">${num(st[k].min, 1)}</td>
    <td class="n">${num(st[k].mean, 1)}</td><td class="n">${num(st[k].max, 1)}</td><td>${unit}</td></tr>` : "";
  el.innerHTML = `<div class="wrap">
    <p class="lead">Optimizer navazuje na <b>bms-monitor</b>: kotelna, okruh topné vody a ohřívače VZT jednotek jsou tytéž.
      Z jeho archivu se čte skutečná ekvitermní křivka a provoz kotelny, z <b>plant.py</b> ceny energií. Větve ústředního
      topení bms-monitor neměří, proto je pro diagnostiku vyrábí simulátor měření.</p>
    <div class="grid2 even">
      <div class="panel"><h3>Stav napojení</h3><ul class="kv">
        <li><span>Adresář</span><span>${esc(s.dir)}</span></li>
        <li><span>Archiv</span><span class="${s.available ? "pos" : "neg"}">${s.available ? "nalezen" : "nenalezen"}</span></li>
        ${s.period ? `<li><span>Období v archivu</span><span>${fmtStamp(s.period[0])} – ${fmtStamp(s.period[1])}</span></li>
        <li><span>Vzorků kotelny</span><span>${num(s.samples)}</span></li>` : ""}
        <li><span>Ceny energií</span><span>${esc(s.tariffs_source)}</span></li>
        ${s.curve_fit ? `<li><span>Křivka podle archivu</span><span>sklon ${num(s.curve_fit.slope, 2)} · posun ${num(s.curve_fit.shift, 1)} K (${s.curve_fit.n} bodů)</span></li>` : ""}
        ${s.error ? `<li><span>Chyba</span><span class="neg">${esc(s.error)}</span></li>` : ""}</ul>
        <div class="note">Umístění se dá změnit proměnnou prostředí <b>BMS_MONITOR_DIR</b>.</div></div>
      <div class="panel"><h3>Ekvitermní křivka kotelny z archivu</h3>
        ${legend([{ name: "žádaná teplota z archivu", color: "var(--s1)", kind: "dot" }, { name: "proložená křivka", color: "var(--s2)" }])}
        <div id="ch-bms"></div></div>
    </div>
    ${s.stats ? `<div class="panel"><h3>Provoz kotelny v archivu bms-monitoru</h3>
      <table class="t"><thead><tr><th>Veličina</th><th class="n">Vzorků</th><th class="n">Min</th><th class="n">Průměr</th><th class="n">Max</th><th></th></tr></thead><tbody>
      ${row("t_outdoor", "Venkovní teplota", "°C")}${row("sp_calc", "Žádaná teplota rozdělovače", "°C")}
      ${row("t_header_flow", "Rozdělovač — přívod", "°C")}${row("t_header_return", "Sběrač — zpátečka", "°C")}
      ${row("heat_power", "Výkon kotlů", "kW")}${row("load_power", "Odběr spotřebičů", "kW")}</tbody></table>
      <div class="note">Archiv ukládá jen změny hodnot (pásmo necitlivosti), proto má každá veličina jiný počet vzorků.</div></div>` : ""}
  </div>`;
  const pts = (s.curve_points || []).map(p => [p.t_out, p.sp]);
  if (pts.length && s.curve_fit) {
    const xs = []; for (let x = Math.floor(Math.min(...pts.map(p => p[0]))) - 1; x <= Math.ceil(Math.max(...pts.map(p => p[0]))) + 1; x++) xs.push(x);
    const f = x => Math.min(Math.max(20 + s.curve_fit.slope * Math.max(20 - x, 0) ** 0.8 + s.curve_fit.shift, 40), 80);
    Charts.line($("#ch-bms"), { x: xs, height: 230, xFmt: v => num(v) + " °C", yFmt: v => num(v, 1) + " °C",
      series: [{ name: "proložená křivka", values: xs.map(f), color: "var(--s2)" }],
      points: [{ name: "archiv", data: pts, color: "var(--s1)" }] });
  } else {
    $("#ch-bms").innerHTML = `<div class="mute" style="padding:20px 0">V archivu nejsou data o žádané teplotě kotelny.</div>`;
  }
}

boot();
