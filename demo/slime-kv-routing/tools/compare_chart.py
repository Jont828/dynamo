"""Render compare_runs.py's JSON as one HTML page, with the ReTool comparison's cache numbers
alongside for contrast.

Usage: python3 -I compare_chart.py <compare.json> <out.html> [<retool-compare.json>] ['{"lede": "..."}']

The page compares the first two phases of compare.json (KV-aware and round-robin on the same
engines). The third phase, round-robin on the tutorial's default engines, appears in one tile and
in the table view. Times come from compare_runs.py's timing runs (runs 2 and 3), tokens from all
three runs.

Colors: KV uses the host theme's --chart-1 (T3 Code's blue), falling back to #1b4ed8; round-robin
uses orange, #eb6834 on light backgrounds and #d95926 on dark ones, picked from the background's
luminance. Both pairs pass the dataviz validator on T3's light (#fcfcfc) and dark (#0a0a0a)
surfaces, as in ../slime-retool/tools/compare_chart.py.
"""

import json
import pathlib
import statistics
import sys

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GSM8K: KV vs round-robin</title>
<style>
:root {
  --s1: var(--chart-1, #1b4ed8);
  --s2: #eb6834;
  --ink: var(--foreground, #27272a);
  --ink-2: var(--muted-foreground, #71717b);
  --grid: var(--border, #e4e4e7);
  --surface: var(--background, #fcfcfc);
  --tile: var(--muted, #fafafa);
}
:root.is-dark { --s2: #d95926; }
body { margin: 0; font-family: var(--font-sans, system-ui, -apple-system, "Segoe UI", sans-serif); color: var(--ink); }
.viz { display: flex; flex-direction: column; gap: 20px; }
.lede { margin: 0; font-size: 14px; line-height: 1.45; color: var(--ink-2); }
.tiles { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
@media (max-width: 560px) { .tiles { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.tile { background: var(--tile); border-radius: var(--radius, 10px); padding: 16px; display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.tile .label { font-size: 12px; color: var(--ink-2); line-height: 1.3; }
.tile .row { display: flex; flex-wrap: wrap; align-items: baseline; column-gap: 6px; font-size: 12px; color: var(--ink-2); min-width: 0; }
.tile .row b { font-size: 17px; font-weight: 600; color: var(--ink); white-space: nowrap; }
.tile .delta { font-size: 12px; color: var(--ink); line-height: 1.3; }
.key { display: inline-block; width: 10px; height: 10px; border-radius: 2px; flex: none; transform: translateY(1px); }
.k1 { background: var(--s1); } .k2 { background: var(--s2); }
.legend { display: flex; gap: 16px; flex-wrap: wrap; font-size: 13px; color: var(--ink); }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
figure { margin: 0; display: flex; flex-direction: column; gap: 6px; }
figcaption { font-size: 14px; font-weight: 600; color: var(--ink); }
figcaption small { display: block; font-weight: 400; font-size: 12px; color: var(--ink-2); margin-top: 2px; line-height: 1.4; }
.chart { width: 100%; height: 232px; }
.chart.tall { height: 252px; }
svg { display: block; overflow: visible; }
svg text { font-family: inherit; font-size: 11px; fill: var(--ink-2); font-variant-numeric: tabular-nums; }
svg text.val { fill: var(--ink); font-weight: 600; }
svg text.grp { fill: var(--ink); font-size: 12px; }
.hit { fill: transparent; outline: none; }
.hit:hover, .hit:focus { fill: var(--grid); fill-opacity: 0.45; }
#tip { position: fixed; pointer-events: none; display: none; z-index: 10; background: var(--popover, var(--surface)); color: var(--ink);
  border: 1px solid var(--grid); border-radius: 8px; padding: 8px 10px; font-size: 12px; line-height: 1.5; box-shadow: 0 4px 16px rgba(0,0,0,.12); max-width: 300px; }
#tip .t { color: var(--ink-2); margin-bottom: 2px; }
#tip .r { display: flex; align-items: center; gap: 6px; white-space: nowrap; }
#tip .r i { display: inline-block; width: 12px; height: 2px; border-radius: 1px; }
#tip .r b { font-weight: 600; }
details { font-size: 12px; }
summary { cursor: pointer; color: var(--ink-2); font-size: 13px; }
.tablewrap { overflow-x: auto; margin-top: 8px; }
table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
th, td { text-align: right; padding: 4px 6px; border-bottom: 1px solid var(--grid); white-space: nowrap; }
th:first-child, td:first-child { text-align: left; }
th { font-weight: 600; color: var(--ink); }
td { color: var(--ink); }
.tnote { margin: 6px 0 0; color: var(--ink-2); line-height: 1.4; }
</style>
</head>
<body>
<div class="viz">
  <p class="lede" id="lede"></p>
  <div class="tiles" id="tiles"></div>
  <div class="legend" id="legend"></div>
  <figure><figcaption>Share of prompt tokens served from the prefix cache<small>ReTool resends each trajectory's history every turn; GSM8K sends one short prompt per sample. Round-robin on the same engines in both.</small></figcaption><div class="chart" id="c-cache" style="height: 212px"></div></figure>
  <figure><figcaption>GSM8K: requests finished, by time since their rollout's first request<small>The 1,152 requests of steps 1–9 in runs 2 and 3, pooled. Overlapping lines mean the same speed.</small></figcaption><div class="chart tall" id="c-finished"></div></figure>
  <figure><figcaption>GSM8K: prompt tokens prefilled per step<small>Mean of three runs; the thin line spans the lowest and highest run. Every run sent the same prompts in each step.</small></figcaption><div class="chart" id="c-prefill"></div></figure>
  <figure><figcaption>GSM8K: rollout time per step<small>Mean of runs 2 and 3. The rollout waits for its longest response (up to 1,024 tokens), so one long answer decides it.</small></figcaption><div class="chart" id="c-rollout"></div></figure>
  <details><summary>Table view</summary><div class="tablewrap" id="table"></div><p class="tnote">Tokens: mean of all three runs. Times: mean of runs 2 and 3. Step 0's rollout includes Slime's start-up.</p></details>
</div>
<div id="tip" role="tooltip"></div>
<script>
const DATA = __DATA__;
const NS = "http://www.w3.org/2000/svg";
const A = DATA.A, B = DATA.B, T = DATA.T, R = DATA.retool;
const LA = DATA.labels.A, LB = DATA.labels.B;
const steps = Object.keys(A.steps).map(Number).sort((x, y) => x - y);

(function theme() {
  const bg = getComputedStyle(document.documentElement).getPropertyValue("--background").trim() || "#fcfcfc";
  const c = document.createElement("canvas").getContext("2d");
  c.fillStyle = bg; const hex = c.fillStyle;
  let lum = 1;
  if (/^#[0-9a-f]{6}$/i.test(hex)) {
    const ch = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255).map(v => v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
    lum = 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
  }
  if (lum < 0.2) document.documentElement.classList.add("is-dark");
})();

const mean = xs => xs.reduce((s, x) => s + x, 0) / xs.length;
const fmt = (x, nd = 0) => x == null ? "–" : x.toLocaleString("en-US", {minimumFractionDigits: nd, maximumFractionDigits: nd});
const pct = (x, nd = 1) => x == null ? "–" : (100 * x).toFixed(nd) + "%";
const sgn = (x, nd = 1) => (x >= 0 ? "+" : "−") + Math.abs(x).toFixed(nd);
const kTok = v => v === 0 ? "0" : (v / 1000).toFixed(v % 1000 ? 1 : 0) + "k";
const el = (tag, attrs = {}, parent) => { const e = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); if (parent) parent.appendChild(e); return e; };
const h = (tag, cls, parent, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; if (parent) parent.appendChild(e); return e; };
const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

document.getElementById("lede").textContent = DATA.lede || "";
const tiles = document.getElementById("tiles");
function tile(label, va, vb, delta) {
  const t = h("div", "tile", tiles);
  h("div", "label", t, label);
  for (const [cls, name, v] of [["k1", LA, va], ["k2", LB, vb]]) {
    const r = h("div", "row", t); h("span", "key " + cls, r); h("b", null, r, v); h("span", null, r, name);
  }
  h("div", "delta", t, delta);
}
tile("Prompt tokens prefilled per run", fmt(A.computed_per_run), fmt(B.computed_per_run),
  "×" + (B.computed_per_run / A.computed_per_run).toFixed(2) + " with round-robin. ReTool: ×" + (R.rr_computed / R.kv_computed).toFixed(2));
tile("Prompt tokens served from cache", pct(A.hit_rate), pct(B.hit_rate),
  "The KV router expected " + pct(A.router_expected_hit_rate, 0));
tile("Half the requests finished, mean per step", fmt(A.half_ms) + " ms", fmt(B.half_ms) + " ms",
  "Round-robin on the tutorial's page-size-1 engines: " + fmt(T.half_ms) + " ms");
tile("Time to first token, median", fmt(A.ttft_p50) + " ms", fmt(B.ttft_p50) + " ms",
  Math.round(A.ttft_p90) === Math.round(B.ttft_p90) ? "90th percentile: " + fmt(A.ttft_p90) + " ms in both"
    : "90th percentile: " + fmt(A.ttft_p90) + " and " + fmt(B.ttft_p90) + " ms");
tile("Rollout time, mean per step", fmt(A.rollout_s, 2) + " s", fmt(B.rollout_s, 2) + " s",
  sgn(100 * (B.rollout_s - A.rollout_s) / A.rollout_s) + "% with round-robin, within step-to-step noise");
tile("Ray job, submit to finish", fmt(A.ray_job_s, 1) + " s", fmt(B.ray_job_s, 1) + " s",
  sgn(B.ray_job_s - A.ray_job_s, 1) + " s with round-robin, within run-to-run noise");

const legend = document.getElementById("legend");
for (const [cls, name] of [["k1", LA], ["k2", LB]]) { const s = h("span", null, legend); h("span", "key " + cls, s); h("span", null, s, name); }

const tip = document.getElementById("tip");
function showTip(evt, title, rows) {
  tip.replaceChildren();
  h("div", "t", tip, title);
  for (const [color, value, label] of rows) {
    const r = h("div", "r", tip); const i = h("i", null, r); i.style.background = color; h("b", null, r, value); h("span", null, r, label);
  }
  tip.style.display = "block";
  let x, y;
  if (evt.clientX != null && evt.type !== "focus") { x = evt.clientX; y = evt.clientY; }
  else { const b = evt.target.getBoundingClientRect(); x = b.left + b.width / 2; y = b.top + 20; }
  const w = tip.offsetWidth, hh = tip.offsetHeight;
  tip.style.left = Math.max(4, Math.min(window.innerWidth - w - 4, x + 12)) + "px";
  tip.style.top = Math.max(4, Math.min(window.innerHeight - hh - 4, y + 12)) + "px";
}
const hideTip = () => { tip.style.display = "none"; };
const bindTip = (node, show) => {
  node.addEventListener("pointermove", show); node.addEventListener("focus", show);
  node.addEventListener("pointerleave", hideTip); node.addEventListener("blur", hideTip);
};

function niceTicks(max, n = 4) {
  const raw = max / n, mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw);
  const top = Math.ceil(max / step - 1e-9) * step;
  const ticks = []; for (let v = 0; v <= top + 1e-9; v += step) ticks.push(+v.toFixed(10));
  return ticks;
}

function frame(host, height, yTicks, yFmt, xLabel) {
  const W = host.clientWidth, H = height;
  const m = {l: 46, r: 10, t: 14, b: xLabel ? 36 : 24};
  const svg = el("svg", {width: W, height: H, role: "img"});
  host.replaceChildren(svg);
  const pw = W - m.l - m.r, ph = H - m.t - m.b;
  const top = yTicks[yTicks.length - 1];
  const y = v => m.t + ph - (v / top) * ph;
  for (const v of yTicks) {
    el("line", {x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), stroke: v === 0 ? "var(--ink-2)" : "var(--grid)", "stroke-width": 1, "stroke-opacity": v === 0 ? 0.5 : 1}, svg);
    const t = el("text", {x: m.l - 6, y: y(v) + 4, "text-anchor": "end"}, svg); t.textContent = yFmt(v);
  }
  if (xLabel) { const t = el("text", {x: m.l + pw / 2, y: H - 4, "text-anchor": "middle"}, svg); t.textContent = xLabel; }
  return {svg, W, H, m, pw, ph, y, top};
}

function columnPath(x, w, y0, y1, r = 4) {
  const hgt = y0 - y1; r = Math.min(r, w / 2, Math.max(hgt, 0));
  if (hgt <= 0) return "";
  return `M${x},${y0} V${y1 + r} Q${x},${y1} ${x + r},${y1} H${x + w - r} Q${x + w},${y1} ${x + w},${y1 + r} V${y0} Z`;
}

function cacheChart(id, height) {
  const host = document.getElementById(id);
  const groups = [
    {label: "ReTool (multi-turn)", a: R.kv_hit, b: R.rr_hit,
     ta: (R.kv_computed / 1e6).toFixed(2) + "M prefilled", tb: (R.rr_computed / 1e6).toFixed(2) + "M prefilled"},
    {label: "GSM8K (single-turn)", a: A.hit_rate, b: B.hit_rate,
     ta: fmt(A.computed_per_run) + " prefilled per run", tb: fmt(B.computed_per_run) + " prefilled per run"},
  ];
  const f = frame(host, height, [0, 0.25, 0.5, 0.75, 1], v => (v * 100) + "%", "");
  const band = f.pw / groups.length;
  const bw = Math.max(12, Math.min(44, (band * 0.5 - 2) / 2));
  groups.forEach((g, i) => {
    const cx = f.m.l + band * i + band / 2;
    const hit = el("rect", {x: f.m.l + band * i, y: f.m.t, width: band, height: f.ph, class: "hit", tabindex: 0,
      "aria-label": `${g.label}: ${LA} ${pct(g.a)}, ${LB} ${pct(g.b)} of prompt tokens served from cache`}, f.svg);
    [[g.a, "var(--s1)", cx - 1 - bw], [g.b, "var(--s2)", cx + 1]].forEach(([v, color, x]) => {
      el("path", {d: columnPath(x, bw, f.y(0), f.y(v)), fill: color, "pointer-events": "none"}, f.svg);
      const t = el("text", {x: x + bw / 2, y: f.y(v) - 5, "text-anchor": "middle", class: "val", "pointer-events": "none"}, f.svg);
      t.textContent = pct(v);
    });
    const t = el("text", {x: cx, y: f.m.t + f.ph + 16, "text-anchor": "middle", class: "grp"}, f.svg); t.textContent = g.label;
    bindTip(hit, e => showTip(e, g.label + ": served from cache", [[css("--s1"), pct(g.a), LA + ", " + g.ta], [css("--s2"), pct(g.b), LB + ", " + g.tb]]));
  });
}

function grouped(id, height, get, range, yFmt, tipFmt, label, extra) {
  const host = document.getElementById(id);
  const vals = steps.flatMap(k => [get(A.steps[k]), get(B.steps[k]), ...(range ? [...range(A.steps[k]), ...range(B.steps[k])] : [])]);
  const f = frame(host, height, niceTicks(Math.max(...vals)), yFmt, "Step");
  const band = f.pw / steps.length;
  const bw = Math.max(4, Math.min(24, (band * 0.72 - 2) / 2));
  steps.forEach((k, i) => {
    const cx = f.m.l + band * i + band / 2;
    const sa = A.steps[k], sb = B.steps[k];
    const hit = el("rect", {x: f.m.l + band * i, y: f.m.t, width: band, height: f.ph, class: "hit", tabindex: 0,
      "aria-label": `Step ${k}: ${LA} ${tipFmt(get(sa))}, ${LB} ${tipFmt(get(sb))}`}, f.svg);
    [[sa, "var(--s1)", cx - 1 - bw], [sb, "var(--s2)", cx + 1]].forEach(([s, color, x]) => {
      el("path", {d: columnPath(x, bw, f.y(0), f.y(get(s))), fill: color, "pointer-events": "none"}, f.svg);
      if (range) {
        const [lo, hi] = range(s), mx = x + bw / 2, cap = Math.min(4, bw / 2);
        el("path", {d: `M${mx},${f.y(lo)} V${f.y(hi)} M${mx - cap},${f.y(lo)} H${mx + cap} M${mx - cap},${f.y(hi)} H${mx + cap}`,
          stroke: "var(--ink)", "stroke-width": 1, "stroke-opacity": 0.7, fill: "none", "pointer-events": "none"}, f.svg);
      }
    });
    const t = el("text", {x: cx, y: f.m.t + f.ph + 14, "text-anchor": "middle"}, f.svg); t.textContent = k;
    bindTip(hit, e => showTip(e, `${label}, step ${k}`, [[css("--s1"), tipFmt(get(sa)), LA + (extra ? extra(sa) : "")],
                                                        [css("--s2"), tipFmt(get(sb)), LB + (extra ? extra(sb) : "")]]));
  });
}

function finished(id, height) {
  const host = document.getElementById(id);
  const ca = A.curve, cb = B.curve, bin = DATA.bin_ms;
  const n = Math.max(ca.length, cb.length);
  const at = (c, s) => s < c.length ? c[s] : 1;
  const f = frame(host, height, [0, 0.25, 0.5, 0.75, 1], v => (v * 100) + "%", "Milliseconds since the rollout's first request");
  const xt = niceTicks((n - 1) * bin, 5); const xmax = xt[xt.length - 1];
  const x = ms => f.m.l + (ms / xmax) * f.pw;
  for (const v of xt) { const t = el("text", {x: x(v), y: f.m.t + f.ph + 14, "text-anchor": "middle"}, f.svg); t.textContent = fmt(v); }
  for (const [c, color] of [[ca, "var(--s1)"], [cb, "var(--s2)"]]) {
    let d = ""; for (let s = 0; s < n; s++) d += (s ? "L" : "M") + x(s * bin).toFixed(1) + "," + f.y(at(c, s)).toFixed(1);
    el("path", {d, fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round"}, f.svg);
  }
  const cross = el("line", {y1: f.m.t, y2: f.m.t + f.ph, stroke: "var(--ink-2)", "stroke-width": 1, visibility: "hidden"}, f.svg);
  const dots = ["var(--s1)", "var(--s2)"].map(color => el("circle", {r: 4, fill: color, stroke: "var(--surface)", "stroke-width": 2, visibility: "hidden"}, f.svg));
  const hit = el("rect", {x: f.m.l, y: f.m.t, width: f.pw, height: f.ph, fill: "transparent", tabindex: 0,
    "aria-label": `Half the requests finished by ${A.p50_pooled} ms (${LA}) and ${B.p50_pooled} ms (${LB}); 90% by ${A.p90_pooled} ms and ${B.p90_pooled} ms`}, f.svg);
  let focusBin = Math.round(A.p50_pooled / bin);
  const place = (s, evt) => {
    s = Math.max(0, Math.min(n - 1, s)); focusBin = s;
    cross.setAttribute("x1", x(s * bin)); cross.setAttribute("x2", x(s * bin)); cross.setAttribute("visibility", "visible");
    [ca, cb].forEach((c, i) => { dots[i].setAttribute("cx", x(s * bin)); dots[i].setAttribute("cy", f.y(at(c, s))); dots[i].setAttribute("visibility", "visible"); });
    showTip(evt, `${fmt(s * bin)} ms after the rollout's first request`, [[css("--s1"), pct(at(ca, s)), LA + " finished"], [css("--s2"), pct(at(cb, s)), LB + " finished"]]);
  };
  hit.addEventListener("pointermove", e => { const r = hit.getBoundingClientRect(); place(Math.round((e.clientX - r.left) / r.width * xmax / bin), e); });
  hit.addEventListener("focus", e => place(focusBin, e));
  hit.addEventListener("keydown", e => { if (e.key === "ArrowRight" || e.key === "ArrowLeft") { place(focusBin + (e.key === "ArrowRight" ? 5 : -5), {type: "focus", target: hit}); e.preventDefault(); } });
  const hide = () => { cross.setAttribute("visibility", "hidden"); dots.forEach(d => d.setAttribute("visibility", "hidden")); hideTip(); };
  hit.addEventListener("pointerleave", hide); hit.addEventListener("blur", hide);
}

function table() {
  const LT = DATA.labels.T;
  const cols = [
    ["Step", k => k],
    [`Prefilled, ${LA}`, k => fmt(mean(A.steps[k].computed))], [`Prefilled, ${LB}`, k => fmt(mean(B.steps[k].computed))],
    [`Prefilled, ${LT}`, k => fmt(mean(T.steps[k].computed))],
    [`From cache, ${LA}`, k => pct(A.steps[k].hit_rate)], [`From cache, ${LB}`, k => pct(B.steps[k].hit_rate)],
    [`From cache, ${LT}`, k => pct(T.steps[k].hit_rate)],
    [`Half finished, ${LA}`, k => fmt(mean(A.steps[k].half)) + " ms"], [`Half finished, ${LB}`, k => fmt(mean(B.steps[k].half)) + " ms"],
    [`Half finished, ${LT}`, k => fmt(mean(T.steps[k].half)) + " ms"],
    [`90% finished, ${LA}`, k => fmt(mean(A.steps[k].p90)) + " ms"], [`90% finished, ${LB}`, k => fmt(mean(B.steps[k].p90)) + " ms"],
    [`Rollout, ${LA}`, k => fmt(mean(A.steps[k].rollout_s), 2) + " s"], [`Rollout, ${LB}`, k => fmt(mean(B.steps[k].rollout_s), 2) + " s"],
    [`Longest response, ${LA}`, k => fmt(mean(A.steps[k].longest))], [`Longest response, ${LB}`, k => fmt(mean(B.steps[k].longest))],
  ];
  const tbl = h("table"); const tr = h("tr", null, h("thead", null, tbl)); cols.forEach(([n]) => h("th", null, tr, n));
  const tb = h("tbody", null, tbl);
  for (const k of steps) { const r = h("tr", null, tb); cols.forEach(([, g]) => h("td", null, r, String(g(k)))); }
  document.getElementById("table").replaceChildren(tbl);
}

function draw() {
  cacheChart("c-cache", 212);
  finished("c-finished", 252);
  grouped("c-prefill", 232, s => mean(s.computed), s => [Math.min(...s.computed), Math.max(...s.computed)], kTok,
    v => fmt(v) + " tokens", "Prefilled", s => ` (runs: ${s.computed.map(v => fmt(v)).join(", ")})`);
  grouped("c-rollout", 232, s => mean(s.rollout_s), null, v => v + " s", v => fmt(v, 2) + " s", "Rollout",
    s => ` (longest response ${fmt(mean(s.longest))} tokens)`);
}
table();
draw();
let lastW = 0;
new ResizeObserver(() => { const w = document.getElementById("c-rollout").clientWidth; if (w && w !== lastW) { lastW = w; draw(); } }).observe(document.body);
</script>
</body>
</html>
"""


def pooled_quantile(curve, q, bin_ms):
    return bin_ms * next((s for s, v in enumerate(curve) if v >= q), len(curve) - 1)


def slim(p, bin_ms):
    """Only what the page reads."""
    curve = [round(v, 3) for v in p["finished_curve"]]
    return {
        "computed_per_run": statistics.fmean(p["computed_tokens_per_run"]),
        "hit_rate": p["hit_rate"],
        "router_expected_hit_rate": p["router_expected_hit_rate"],
        "half_ms": p["finished_by_mean_ms"]["p50"],
        "ttft_p50": p["ttft_ms_warm"]["p50"],
        "ttft_p90": p["ttft_ms_warm"]["p90"],
        "rollout_s": p["rollout_s_mean"],
        "ray_job_s": p["ray_job_s"],
        "curve": curve,
        "p50_pooled": pooled_quantile(curve, 0.5, bin_ms),
        "p90_pooled": pooled_quantile(curve, 0.9, bin_ms),
        "steps": {k: {"computed": v["computed_tokens"], "hit_rate": round(v["hit_rate"], 4),
                      "rollout_s": [round(x, 3) for x in v["rollout_s"]], "longest": v["response_len_max"],
                      "half": [round(x) for x in v["finished_p50_ms"]], "p90": [round(x) for x in v["finished_p90_ms"]]}
                  for k, v in p["steps"].items()},
    }


def main():
    full = json.loads(pathlib.Path(sys.argv[1]).read_text())
    na, nb, nt = full["names"][:3]
    bin_ms = 10
    data = {"labels": {"A": na, "B": nb, "T": nt}, "bin_ms": bin_ms,
            "A": slim(full["phases"][na], bin_ms), "B": slim(full["phases"][nb], bin_ms), "T": slim(full["phases"][nt], bin_ms)}
    for key in ("curve", "p50_pooled", "p90_pooled"):  # the page draws no curve for the third phase
        del data["T"][key]
    rest = sys.argv[3:]
    if rest and not rest[0].lstrip().startswith("{"):
        rt = json.loads(pathlib.Path(rest.pop(0)).read_text())
        ka, kb = rt["names"]
        data["retool"] = {"kv_hit": rt[ka]["hit_rate"], "rr_hit": rt[kb]["hit_rate"],
                          "kv_computed": rt[ka]["computed_tokens"], "rr_computed": rt[kb]["computed_tokens"]}
    if rest:
        data.update(json.loads(rest[0]))  # {"lede": "...", "labels": {...}}
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    pathlib.Path(sys.argv[2]).write_text(PAGE.replace("__DATA__", blob))


if __name__ == "__main__":
    main()
