"""Render compare_runs.py's JSON as one HTML page: summary tiles and per-step charts.

Usage: python -I compare_chart.py <compare.json> <out.html> ['{"labels": {"KV": "...", "RR": "..."}, "lede": "..."}']

Colors: the first run uses the host theme's --chart-1 (T3 Code's blue), falling back to
#1b4ed8; the second uses orange, #eb6834 on light backgrounds and #d95926 on dark ones,
picked from the background's luminance. Both pairs pass the dataviz validator's checks on
T3's light (#fcfcfc, #ffffff) and dark (#0a0a0a, #111111) surfaces.
"""

import json
import pathlib
import sys

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>KV vs round-robin</title>
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
.lkey { display: inline-block; width: 14px; height: 2px; border-radius: 1px; flex: none; transform: translateY(-3px); }
.legend { display: flex; gap: 16px; flex-wrap: wrap; font-size: 13px; color: var(--ink); }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
figure { margin: 0; display: flex; flex-direction: column; gap: 6px; }
figcaption { font-size: 14px; font-weight: 600; color: var(--ink); }
figcaption small { display: block; font-weight: 400; font-size: 12px; color: var(--ink-2); margin-top: 2px; line-height: 1.4; }
.chart { width: 100%; height: 232px; }
.chart.tall { height: 252px; }
svg { display: block; overflow: visible; }
svg text { font-family: inherit; font-size: 11px; fill: var(--ink-2); font-variant-numeric: tabular-nums; }
.hit { fill: transparent; outline: none; }
.hit:hover, .hit:focus { fill: var(--grid); fill-opacity: 0.45; }
#tip { position: fixed; pointer-events: none; display: none; z-index: 10; background: var(--popover, var(--surface)); color: var(--ink);
  border: 1px solid var(--grid); border-radius: 8px; padding: 8px 10px; font-size: 12px; line-height: 1.5; box-shadow: 0 4px 16px rgba(0,0,0,.12); max-width: 280px; }
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
</style>
</head>
<body>
<div class="viz">
  <p class="lede" id="lede"></p>
  <div class="tiles" id="tiles"></div>
  <div class="legend" id="legend"></div>
  <figure><figcaption>Trajectories finished, by time since their step's first request<small>All 2,560 trajectories of each run, pooled over the 10 steps. Overlapping lines mean the same speed.</small></figcaption><div class="chart tall" id="c-finished"></div></figure>
  <figure><figcaption>Rollout time per step<small>The rollout waits for its slowest trajectory, so one long response or a sandbox timeout decides it.</small></figcaption><div class="chart" id="c-rollout"></div></figure>
  <figure><figcaption>Prompt tokens prefilled per step<small>Prompt tokens the engines had to compute because their prefix cache didn't hold them.</small></figcaption><div class="chart" id="c-prefill"></div></figure>
  <details><summary>Table view</summary><div class="tablewrap" id="table"></div></details>
</div>
<div id="tip" role="tooltip"></div>
<script>
const DATA = __DATA__;
const NS = "http://www.w3.org/2000/svg";
const [NA, NB] = DATA.names;
const A = DATA[NA], B = DATA[NB];
const LABEL = DATA.labels || {[NA]: NA, [NB]: NB};
const steps = Object.keys(A.steps).filter(k => k in B.steps).map(Number).sort((x, y) => x - y);

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

const fmt = (x, nd = 0) => x == null ? "–" : x.toLocaleString("en-US", {minimumFractionDigits: nd, maximumFractionDigits: nd});
const pct = (x, nd = 1) => x == null ? "–" : (100 * x).toFixed(nd) + "%";
const sgn = (x, nd = 1) => (x >= 0 ? "+" : "−") + Math.abs(x).toFixed(nd);
const el = (tag, attrs = {}, parent) => { const e = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); if (parent) parent.appendChild(e); return e; };
const h = (tag, cls, parent, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; if (parent) parent.appendChild(e); return e; };
const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

document.getElementById("lede").textContent = DATA.lede || "";
const tiles = document.getElementById("tiles");
function tile(label, va, vb, delta) {
  const t = h("div", "tile", tiles);
  h("div", "label", t, label);
  for (const [cls, name, v] of [["k1", LABEL[NA], va], ["k2", LABEL[NB], vb]]) {
    const r = h("div", "row", t); h("span", "key " + cls, r); h("b", null, r, v); h("span", null, r, name);
  }
  h("div", "delta", t, delta);
}
const meanP50 = R => steps.reduce((s, k) => s + R.steps[k].finished_by.p50, 0) / steps.length;
tile("Prompt tokens prefilled", fmt(A.computed_tokens / 1e6, 2) + "M", fmt(B.computed_tokens / 1e6, 2) + "M",
  "×" + (B.computed_tokens / A.computed_tokens).toFixed(2) + " with " + LABEL[NB].toLowerCase());
tile("Prefix-cache hit rate", pct(A.hit_rate), pct(B.hit_rate), sgn(100 * (B.hit_rate - A.hit_rate)) + " points with " + LABEL[NB].toLowerCase());
tile("Follow-ups on their previous engine", pct(A.same_engine, 0), pct(B.same_engine, 0), "Chance alone: 1 in 4");
const ta = A.ttft_p50_ms, tb = B.ttft_p50_ms;
tile("Follow-up time to first token, median", fmt(ta, 1) + " ms", fmt(tb, 1) + " ms", sgn(tb - ta, 1) + " ms with " + LABEL[NB].toLowerCase());
tile("Half the trajectories finished, mean per step", fmt(meanP50(A), 1) + " s", fmt(meanP50(B), 1) + " s",
  sgn(meanP50(B) - meanP50(A), 1) + " s with " + LABEL[NB].toLowerCase());
tile("Rollout time, sum of " + steps.length + " steps", fmt(A.rollout_sum_s) + " s", fmt(B.rollout_sum_s) + " s",
  sgn(100 * (B.rollout_sum_s - A.rollout_sum_s) / A.rollout_sum_s) + "%, within step-to-step noise");

const legend = document.getElementById("legend");
for (const [cls, name] of [["k1", LABEL[NA]], ["k2", LABEL[NB]]]) { const s = h("span", null, legend); h("span", "key " + cls, s); h("span", null, s, name); }

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

function niceTicks(max, n = 4) {
  const raw = max / n, mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw);
  const top = Math.ceil(max / step - 1e-9) * step;
  const ticks = []; for (let v = 0; v <= top + 1e-9; v += step) ticks.push(+v.toFixed(10));
  return ticks;
}

function frame(host, height, yTicks, yFmt, xLabel) {
  const W = host.clientWidth, H = height;
  const m = {l: 46, r: 10, t: 8, b: 36};
  const svg = el("svg", {width: W, height: H, role: "img"});
  host.replaceChildren(svg);
  const pw = W - m.l - m.r, ph = H - m.t - m.b;
  const top = yTicks[yTicks.length - 1];
  const y = v => m.t + ph - (v / top) * ph;
  for (const v of yTicks) {
    el("line", {x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), stroke: v === 0 ? "var(--ink-2)" : "var(--grid)", "stroke-width": 1, "stroke-opacity": v === 0 ? 0.5 : 1}, svg);
    const t = el("text", {x: m.l - 6, y: y(v) + 4, "text-anchor": "end"}, svg); t.textContent = yFmt(v);
  }
  const t = el("text", {x: m.l + pw / 2, y: H - 4, "text-anchor": "middle"}, svg); t.textContent = xLabel;
  return {svg, W, H, m, pw, ph, y, top};
}

function columnPath(x, w, y0, y1, r = 4) {
  const hgt = y0 - y1; r = Math.min(r, w / 2, Math.max(hgt, 0));
  if (hgt <= 0) return "";
  return `M${x},${y0} V${y1 + r} Q${x},${y1} ${x + r},${y1} H${x + w - r} Q${x + w},${y1} ${x + w},${y1 + r} V${y0} Z`;
}

function grouped(id, height, get, yFmt, tipFmt, label) {
  const host = document.getElementById(id);
  const vals = steps.flatMap(k => [get(A.steps[k]), get(B.steps[k])]).filter(v => v != null);
  const f = frame(host, height, niceTicks(Math.max(...vals)), yFmt, "Step");
  const band = f.pw / steps.length;
  const bw = Math.max(4, Math.min(24, (band * 0.72 - 2) / 2));
  steps.forEach((k, i) => {
    const cx = f.m.l + band * i + band / 2;
    const hit = el("rect", {x: f.m.l + band * i, y: f.m.t, width: band, height: f.ph, class: "hit", tabindex: 0,
      "aria-label": `Step ${k}: ${LABEL[NA]} ${tipFmt(get(A.steps[k]))}, ${LABEL[NB]} ${tipFmt(get(B.steps[k]))}`}, f.svg);
    [[A, "var(--s1)", cx - 1 - bw], [B, "var(--s2)", cx + 1]].forEach(([R, color, x]) => {
      const v = get(R.steps[k]); if (v == null) return;
      el("path", {d: columnPath(x, bw, f.y(0), f.y(v)), fill: color, "pointer-events": "none"}, f.svg);
    });
    const t = el("text", {x: cx, y: f.m.t + f.ph + 14, "text-anchor": "middle"}, f.svg); t.textContent = k;
    const show = e => showTip(e, `${label}, step ${k}`, [[css("--s1"), tipFmt(get(A.steps[k])), LABEL[NA]], [css("--s2"), tipFmt(get(B.steps[k])), LABEL[NB]]]);
    hit.addEventListener("pointermove", show); hit.addEventListener("focus", show);
    hit.addEventListener("pointerleave", hideTip); hit.addEventListener("blur", hideTip);
  });
}

function finished(id, height) {
  const host = document.getElementById(id);
  const ca = A.finished_curve, cb = B.finished_curve;
  const n = Math.max(ca.length, cb.length);
  const at = (c, s) => s < c.length ? c[s] : 1;
  const f = frame(host, height, [0, 0.25, 0.5, 0.75, 1], v => (v * 100) + "%", "Seconds since the step's first request");
  const xt = niceTicks(n - 1, 5); const xmax = xt[xt.length - 1];
  const x = s => f.m.l + (s / xmax) * f.pw;
  for (const v of xt) { const t = el("text", {x: x(v), y: f.m.t + f.ph + 14, "text-anchor": "middle"}, f.svg); t.textContent = v; }
  for (const [c, color] of [[ca, "var(--s1)"], [cb, "var(--s2)"]]) {
    let d = ""; for (let s = 0; s < n; s++) d += (s ? "L" : "M") + x(s).toFixed(1) + "," + f.y(at(c, s)).toFixed(1);
    el("path", {d, fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round"}, f.svg);
  }
  const cross = el("line", {y1: f.m.t, y2: f.m.t + f.ph, stroke: "var(--ink-2)", "stroke-width": 1, visibility: "hidden"}, f.svg);
  const dots = [["var(--s1)", ca], ["var(--s2)", cb]].map(([color]) => el("circle", {r: 4, fill: color, stroke: "var(--surface)", "stroke-width": 2, visibility: "hidden"}, f.svg));
  const hit = el("rect", {x: f.m.l, y: f.m.t, width: f.pw, height: f.ph, fill: "transparent", tabindex: 0,
    "aria-label": `Half finished by ${A.p50_pooled} s (${LABEL[NA]}) and ${B.p50_pooled} s (${LABEL[NB]}); 90% by ${A.p90_pooled} s and ${B.p90_pooled} s`}, f.svg);
  let focusSec = Math.round(A.p50_pooled);
  const place = (s, evt) => {
    s = Math.max(0, Math.min(n - 1, s));
    cross.setAttribute("x1", x(s)); cross.setAttribute("x2", x(s)); cross.setAttribute("visibility", "visible");
    [ca, cb].forEach((c, i) => { dots[i].setAttribute("cx", x(s)); dots[i].setAttribute("cy", f.y(at(c, s))); dots[i].setAttribute("visibility", "visible"); });
    showTip(evt, `${s} s after the step's first request`, [[css("--s1"), pct(at(ca, s)), LABEL[NA] + " finished"], [css("--s2"), pct(at(cb, s)), LABEL[NB] + " finished"]]);
  };
  hit.addEventListener("pointermove", e => { const r = hit.getBoundingClientRect(); place(Math.round((e.clientX - r.left) / r.width * xmax), e); });
  hit.addEventListener("focus", e => place(focusSec, e));
  hit.addEventListener("keydown", e => { if (e.key === "ArrowRight" || e.key === "ArrowLeft") { focusSec += e.key === "ArrowRight" ? 1 : -1; place(focusSec, {type: "focus", target: hit}); e.preventDefault(); } });
  const hide = () => { cross.setAttribute("visibility", "hidden"); dots.forEach(d => d.setAttribute("visibility", "hidden")); hideTip(); };
  hit.addEventListener("pointerleave", hide); hit.addEventListener("blur", hide);
}

function table() {
  const cols = [
    ["Step", k => k],
    [`Rollout, ${LABEL[NA]}`, k => fmt(A.steps[k].rollout_s) + " s"], [`Rollout, ${LABEL[NB]}`, k => fmt(B.steps[k].rollout_s) + " s"],
    [`Half finished, ${LABEL[NA]}`, k => fmt(A.steps[k].finished_by.p50, 1) + " s"], [`Half finished, ${LABEL[NB]}`, k => fmt(B.steps[k].finished_by.p50, 1) + " s"],
    [`90% finished, ${LABEL[NA]}`, k => fmt(A.steps[k].finished_by.p90, 1) + " s"], [`90% finished, ${LABEL[NB]}`, k => fmt(B.steps[k].finished_by.p90, 1) + " s"],
    [`Longest response, ${LABEL[NA]}`, k => fmt(A.steps[k].response_len_max)], [`Longest response, ${LABEL[NB]}`, k => fmt(B.steps[k].response_len_max)],
    [`Prefilled, ${LABEL[NA]}`, k => fmt(A.steps[k].computed_tokens / 1e6, 2) + "M"], [`Prefilled, ${LABEL[NB]}`, k => fmt(B.steps[k].computed_tokens / 1e6, 2) + "M"],
    [`Hit rate, ${LABEL[NA]}`, k => pct(A.steps[k].hit_rate)], [`Hit rate, ${LABEL[NB]}`, k => pct(B.steps[k].hit_rate)],
  ];
  const tbl = h("table"); const tr = h("tr", null, h("thead", null, tbl)); cols.forEach(([n]) => h("th", null, tr, n));
  const tb = h("tbody", null, tbl);
  for (const k of steps) { const r = h("tr", null, tb); cols.forEach(([, g]) => h("td", null, r, String(g(k)))); }
  document.getElementById("table").replaceChildren(tbl);
}

function draw() {
  finished("c-finished", 252);
  grouped("c-rollout", 232, s => s.rollout_s, v => v + " s", v => fmt(v, 1) + " s", "Rollout");
  grouped("c-prefill", 232, s => s.computed_tokens / 1e6, v => v === 0 ? "0" : (v < 1 ? v.toFixed(2).replace(/0$/, "") : v) + "M", v => fmt(v, 2) + "M tokens", "Prefilled");
}
table();
draw();
let lastW = 0;
new ResizeObserver(() => { const w = document.getElementById("c-rollout").clientWidth; if (w && w !== lastW) { lastW = w; draw(); } }).observe(document.body);
</script>
</body>
</html>
"""

STEP_KEYS = ["rollout_s", "response_len_max", "computed_tokens", "hit_rate"]


def pooled_quantile(curve, q):
    return next((s for s, v in enumerate(curve) if v >= q), len(curve) - 1)


def slim(run):
    """Only what the page reads."""
    curve = run["finished_curve"]
    return {
        "rollout_sum_s": run["rollout_sum_s"],
        "computed_tokens": run["computed_tokens"],
        "hit_rate": run["hit_rate"],
        "same_engine": run["same_engine"],
        "ttft_p50_ms": round(run["latency"]["followup"]["ttft_p50_ms"], 1),
        "finished_curve": [round(v, 4) for v in curve],
        "p50_pooled": pooled_quantile(curve, 0.5),
        "p90_pooled": pooled_quantile(curve, 0.9),
        "steps": {k: {**{key: (round(v[key], 4) if isinstance(v[key], float) else v[key]) for key in STEP_KEYS},
                      "finished_by": {q: round(x, 2) for q, x in v["finished_by"].items()}}
                  for k, v in run["steps"].items()},
    }


def main():
    full = json.loads(pathlib.Path(sys.argv[1]).read_text())
    na, nb = full["names"]
    data = {"names": [na, nb], na: slim(full[na]), nb: slim(full[nb])}
    if len(sys.argv) > 3:
        data.update(json.loads(sys.argv[3]))  # {"labels": {...}, "lede": "..."}
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    pathlib.Path(sys.argv[2]).write_text(PAGE.replace("__DATA__", blob))


if __name__ == "__main__":
    main()
