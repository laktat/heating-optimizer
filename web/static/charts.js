/* Grafy — čáry s nitkovým křížem a sloupce s popiskem při najetí.
 * Bez knihoven: SVG se skládá jako text a překreslí se při změně šířky. */
"use strict";

const Charts = (() => {
  const tip = () => document.getElementById("tip");

  function showTip(evt, title, rows) {
    const t = tip();
    t.innerHTML = (title ? `<div class="tt">${title}</div>` : "") + rows.map(r =>
      `<div class="tr"><span>${r.color ? `<i style="background:${r.color}"></i>` : ""}${r.name}</span><span>${r.value}</span></div>`
    ).join("");
    t.classList.add("on");
    const pad = 14, w = t.offsetWidth, h = t.offsetHeight;
    let x = evt.clientX + pad, y = evt.clientY + pad;
    if (x + w > innerWidth - 8) x = evt.clientX - w - pad;
    if (y + h > innerHeight - 8) y = evt.clientY - h - pad;
    t.style.left = x + "px"; t.style.top = y + "px";
  }
  function hideTip() { tip().classList.remove("on"); }

  function niceTicks(lo, hi, n = 5) {
    if (hi - lo < 1e-9) { hi = lo + 1; }
    const raw = (hi - lo) / n, mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw);
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10));
    return out;
  }

  const observers = new WeakMap();
  function mount(el, draw) {
    draw();
    if (!observers.has(el)) {
      let w = el.clientWidth;
      const ro = new ResizeObserver(() => { if (Math.abs(el.clientWidth - w) > 4) { w = el.clientWidth; draw(); } });
      ro.observe(el);
      observers.set(el, ro);
    }
  }

  /**
   * Čárový graf.
   * opt.x: hodnoty osy x (čísla), opt.series: [{name, values, color, dash}],
   * opt.points: [{name, data: [[x, y]], color}], opt.xFmt / yFmt / tipX
   */
  function line(el, opt) {
    mount(el, () => {
      const W = Math.max(el.clientWidth, 280), H = opt.height || 220;
      const m = { l: 44, r: opt.rightPad || 16, t: 12, b: opt.xTitle ? 38 : 26 };
      const xs = opt.x;
      const all = [];
      opt.series.forEach(s => s.values.forEach(v => v != null && all.push(v)));
      (opt.points || []).forEach(p => p.data.forEach(d => all.push(d[1])));
      let lo = opt.yMin ?? Math.min(...all), hi = opt.yMax ?? Math.max(...all);
      const padY = (hi - lo) * 0.08 || 1;
      if (opt.yMin == null) lo -= padY;
      if (opt.yMax == null) hi += padY;
      const yt = niceTicks(lo, hi, opt.yTicks || 5);
      lo = Math.min(lo, yt[0]); hi = Math.max(hi, yt[yt.length - 1]);
      const x0 = Math.min(...xs), x1 = Math.max(...xs);
      const X = v => m.l + (v - x0) / ((x1 - x0) || 1) * (W - m.l - m.r);
      const Y = v => m.t + (1 - (v - lo) / (hi - lo)) * (H - m.t - m.b);
      const xt = opt.xTicks || niceTicks(x0, x1, Math.max(3, Math.floor(W / 110)));
      const xFmt = opt.xFmt || (v => v), yFmt = opt.yFmt || (v => v);

      let s = `<svg class="chart" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${opt.label || ""}">`;
      yt.forEach(v => s += `<line class="grid" x1="${m.l}" x2="${W - m.r}" y1="${Y(v)}" y2="${Y(v)}"/>
        <text class="axis" x="${m.l - 7}" y="${Y(v) + 3.5}" text-anchor="end">${yFmt(v)}</text>`);
      xt.forEach(v => { if (v >= x0 && v <= x1) s += `<text class="axis" x="${X(v)}" y="${H - m.b + 15}" text-anchor="middle">${xFmt(v)}</text>`; });
      if (opt.xTitle) s += `<text class="axis-title" x="${(m.l + W - m.r) / 2}" y="${H - 4}" text-anchor="middle">${opt.xTitle}</text>`;
      if (opt.yTitle) s += `<text class="axis-title" x="${m.l}" y="${m.t - 2}" text-anchor="start" dy="-1">${""}</text>`;
      (opt.bands || []).forEach(b => {
        s += `<rect x="${X(b.x0)}" y="${m.t}" width="${Math.max(X(b.x1) - X(b.x0), 0)}" height="${H - m.t - m.b}" fill="${b.color}" opacity="${b.opacity ?? .07}"/>`;
      });
      opt.series.forEach(se => {
        let d = "", pen = false;
        se.values.forEach((v, i) => {
          if (v == null) { pen = false; return; }
          d += (pen ? "L" : "M") + X(xs[i]).toFixed(1) + "," + Y(v).toFixed(1);
          pen = true;
        });
        s += `<path class="line" d="${d}" stroke="${se.color}" ${se.dash ? `stroke-dasharray="${se.dash}"` : ""} stroke-width="${se.width || 2}"/>`;
        if (se.endLabel) {
          const i = se.values.length - 1;
          s += `<text class="dlabel" x="${X(xs[i]) + 6}" y="${Y(se.values[i]) + 4}">${se.endLabel}</text>`;
        }
      });
      (opt.points || []).forEach(p => p.data.forEach(d => {
        s += `<circle cx="${X(d[0])}" cy="${Y(d[1])}" r="4.5" fill="${p.color}" stroke="var(--panel)" stroke-width="2"/>`;
      }));
      s += `<line class="cross" x1="0" x2="0" y1="${m.t}" y2="${H - m.b}"/>`;
      opt.series.forEach(se => s += `<circle class="hover-dot" r="4.5" fill="${se.color}" cx="-20" cy="-20" visibility="hidden"/>`);
      s += `<rect class="hitbox" x="${m.l}" y="${m.t}" width="${W - m.l - m.r}" height="${H - m.t - m.b}" fill="transparent"/>`;
      s += `</svg>`;
      el.innerHTML = s;

      const svg = el.querySelector("svg"), cross = svg.querySelector(".cross");
      const dots = svg.querySelectorAll(".hover-dot");
      const hit = svg.querySelector(".hitbox");
      hit.addEventListener("mousemove", evt => {
        const r = svg.getBoundingClientRect();
        const px = (evt.clientX - r.left) * (W / r.width);
        let best = 0, bd = Infinity;
        xs.forEach((v, i) => { const d = Math.abs(X(v) - px); if (d < bd) { bd = d; best = i; } });
        const cx = X(xs[best]);
        cross.setAttribute("x1", cx); cross.setAttribute("x2", cx); cross.style.opacity = 1;
        const rows = [];
        opt.series.forEach((se, k) => {
          const v = se.values[best];
          if (v == null) { dots[k].setAttribute("visibility", "hidden"); return; }
          dots[k].setAttribute("cx", cx); dots[k].setAttribute("cy", Y(v));
          dots[k].setAttribute("visibility", "visible");
          rows.push({ name: se.name, value: yFmt(v, true), color: se.color });
        });
        (opt.points || []).forEach(p => {
          const hitp = p.data.find(d => Math.abs(d[0] - xs[best]) < 1e-6);
          if (hitp) rows.push({ name: p.name, value: yFmt(hitp[1], true), color: p.color });
        });
        showTip(evt, (opt.tipX || xFmt)(xs[best]), rows);
      });
      hit.addEventListener("mouseleave", () => {
        cross.style.opacity = 0; dots.forEach(d => d.setAttribute("visibility", "hidden")); hideTip();
      });
    });
  }

  /** Vodopád: první a poslední sloupec celý, mezi nimi přírůstky. */
  function waterfall(el, opt) {
    mount(el, () => {
      const W = Math.max(el.clientWidth, 300), H = opt.height || 280;
      const m = { l: 60, r: 10, t: 22, b: 58 };
      const items = opt.items;
      let run = 0;
      const bars = items.map(it => {
        if (it.kind === "total") { const b = { ...it, y0: 0, y1: it.value }; run = it.value; return b; }
        const b = { ...it, y0: run, y1: run + it.value }; run += it.value; return b;
      });
      const vals = bars.flatMap(b => [b.y0, b.y1]);
      let lo = Math.min(...vals), hi = Math.max(...vals);
      lo = opt.yMin ?? Math.max(0, lo - (hi - lo) * 0.25);
      const yt = niceTicks(lo, hi, 5);
      lo = yt[0]; hi = Math.max(hi, yt[yt.length - 1]);
      const Y = v => m.t + (1 - (v - lo) / (hi - lo)) * (H - m.t - m.b);
      const bw = (W - m.l - m.r) / bars.length;
      const fmt = opt.fmt || (v => v);
      let s = `<svg class="chart" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${opt.label || ""}">`;
      yt.forEach(v => s += `<line class="grid" x1="${m.l}" x2="${W - m.r}" y1="${Y(v)}" y2="${Y(v)}"/>
        <text class="axis" x="${m.l - 7}" y="${Y(v) + 3.5}" text-anchor="end">${fmt(v)}</text>`);
      bars.forEach((b, i) => {
        const x = m.l + i * bw + bw * 0.16, w = bw * 0.68;
        const top = Y(Math.max(b.y0, b.y1)), bot = Y(Math.min(b.y0, b.y1));
        const color = b.kind === "total" ? (b.color || "var(--s1)") : (b.value <= 0 ? "var(--ok)" : "var(--bad)");
        const h = Math.max(bot - top, 1.5);
        s += `<g class="wf" data-i="${i}">
          <rect x="${m.l + i * bw}" y="${m.t}" width="${bw}" height="${H - m.t - m.b}" fill="transparent"/>
          <rect x="${x}" y="${top}" width="${w}" height="${h}" rx="3" fill="${color}"/>`;
        if (i < bars.length - 1) {
          const nx = m.l + (i + 1) * bw + bw * 0.16;
          s += `<line x1="${x + w}" x2="${nx}" y1="${Y(b.y1)}" y2="${Y(b.y1)}" stroke="var(--dim)" stroke-width="1"/>`;
        }
        const lbl = b.kind === "total" ? fmt(b.value) : (b.value <= 0 ? "−" : "+") + fmt(Math.abs(b.value));
        s += `<text class="dlabel" x="${x + w / 2}" y="${top - 6}" text-anchor="middle">${lbl}</text>`;
        const words = b.label.split(" ");
        const lines = [];
        words.forEach(wd => { const L = lines[lines.length - 1]; if (L && (L + " " + wd).length <= Math.max(9, bw / 6.2)) lines[lines.length - 1] = L + " " + wd; else lines.push(wd); });
        lines.slice(0, 3).forEach((ln, k) => s += `<text class="axis" x="${x + w / 2}" y="${H - m.b + 15 + k * 12}" text-anchor="middle">${ln}</text>`);
        s += `</g>`;
      });
      s += `</svg>`;
      el.innerHTML = s;
      el.querySelectorAll(".wf").forEach(g => {
        const b = bars[+g.dataset.i];
        g.addEventListener("mousemove", evt => showTip(evt, b.label, b.tip || [{ name: "hodnota", value: fmt(b.value) }]));
        g.addEventListener("mouseleave", hideTip);
      });
    });
  }

  /** Sloupce (kumulovaný tok peněz) — záporné červeně, kladné zeleně. */
  function columns(el, opt) {
    mount(el, () => {
      const W = Math.max(el.clientWidth, 300), H = opt.height || 220;
      const m = { l: 60, r: 10, t: 14, b: 28 };
      const v = opt.values;
      let lo = Math.min(0, ...v), hi = Math.max(0, ...v);
      const yt = niceTicks(lo, hi, 5);
      lo = Math.min(lo, yt[0]); hi = Math.max(hi, yt[yt.length - 1]);
      const Y = x => m.t + (1 - (x - lo) / (hi - lo)) * (H - m.t - m.b);
      const bw = (W - m.l - m.r) / v.length;
      const fmt = opt.fmt || (x => x);
      let s = `<svg class="chart" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${opt.label || ""}">`;
      yt.forEach(t => s += `<line class="${t === 0 ? "zero" : "grid"}" x1="${m.l}" x2="${W - m.r}" y1="${Y(t)}" y2="${Y(t)}"/>
        <text class="axis" x="${m.l - 7}" y="${Y(t) + 3.5}" text-anchor="end">${fmt(t)}</text>`);
      v.forEach((x, i) => {
        const bx = m.l + i * bw + bw * 0.2, w = bw * 0.6;
        const top = Y(Math.max(x, 0)), bot = Y(Math.min(x, 0));
        s += `<g class="col" data-i="${i}"><rect x="${m.l + i * bw}" y="${m.t}" width="${bw}" height="${H - m.t - m.b}" fill="transparent"/>
          <rect x="${bx}" y="${top}" width="${w}" height="${Math.max(bot - top, 1.5)}" rx="3" fill="${x < 0 ? "var(--bad)" : "var(--ok)"}"/>
          <text class="axis" x="${bx + w / 2}" y="${H - m.b + 15}" text-anchor="middle">${opt.labels[i]}</text></g>`;
      });
      s += `</svg>`;
      el.innerHTML = s;
      el.querySelectorAll(".col").forEach(g => {
        const i = +g.dataset.i;
        g.addEventListener("mousemove", evt => showTip(evt, opt.tipTitle ? opt.tipTitle(i) : opt.labels[i],
          [{ name: opt.name || "hodnota", value: fmt(v[i], true) }]));
        g.addEventListener("mouseleave", hideTip);
      });
    });
  }

  return { line, waterfall, columns, showTip, hideTip };
})();
