"""Render the routing comparison as small-multiple bar charts (HTML).

Usage: python3 -I chart_page.py <runs-dir> <out.html> [--standalone]

Reads runs/<phase>/run<N>/summary.json for each configuration below. Each
panel is one metric: a bar per configuration (the pooled mean of its steps
across all runs) with one dot per RayJob (that run's mean). The documented KV
configuration carries the accent color; the others are context. A hairline
marks the round-robin tutorial's mean. Without --standalone the page follows
T3's inline-render rules (theme variables, no page background).
"""

import html
import json
import pathlib
import statistics
import sys

CONFIGS = [
    ("rr", "Round-robin, tutorial DGD", "baseline"),
    ("kv", "KV-aware, documented settings", "accent"),
    ("affinity2", "KV-aware + affinity, credit 3", "context"),
    ("affinity", "KV-aware + affinity, credit 16", "context"),
    ("control", "Round-robin on the KV engines", "context"),
]

METRICS = [
    # key, title, unit, per-step field, steps, scale, decimals, note
    ("hit", "Prefix-cache hit rate", "%", "prefix_cache_hit_rate", "all", 100.0, 0,
     "Share of prompt tokens the engines served from cache (Slime's rollout/prefix_cache_hit_rate), all 10 rollouts"),
    ("rollout", "Rollout time", "s", "rollout_time", "1-9", 1.0, 2,
     "Slime's perf/rollout_time, rollouts 1-9 (rollout 0 includes warm-up)"),
    ("step", "Training step time", "s", "step_time", "1-9", 1.0, 2,
     "Slime's perf/step_time: wait for rollout, train, sync weights; steps 1-9"),
    ("decode", "Longest sample's generation rate", "tok/s", "longest_sample_tokens_per_sec", "1-9", 1.0, 0,
     "Slime's perf/longest_sample_tokens_per_sec, rollouts 1-9. Every row but the first runs --page-size 16, which KV events need"),
]


def load(runs_dir: pathlib.Path, phase: str):
    runs = []
    for rd in sorted((runs_dir / phase).glob("run*/summary.json")):
        runs.append(json.loads(rd.read_text()))
    return runs


def values(run, field, steps):
    out = []
    for st in run["steps"]:
        if steps == "1-9" and st["step"] < 1:
            continue
        if st.get(field) is not None:
            out.append(st[field])
    return out


def main() -> None:
    runs_dir = pathlib.Path(sys.argv[1])
    out_path = pathlib.Path(sys.argv[2])
    standalone = "--standalone" in sys.argv[3:]
    data = {}
    for phase, label, role in CONFIGS:
        runs = load(runs_dir, phase)
        if not runs:
            continue
        entry = {"label": label, "role": role, "runs": len(runs), "metrics": {}}
        for key, _title, _unit, field, steps, scale, _dec, _note in METRICS:
            pooled = [v * scale for r in runs for v in values(r, field, steps)]
            per_run = [statistics.fmean(values(r, field, steps)) * scale for r in runs]
            entry["metrics"][key] = {
                "mean": round(statistics.fmean(pooled), 6),
                "sd": round(statistics.stdev(pooled) if len(pooled) > 1 else 0.0, 6),
                "n": len(pooled),
                "runs": [round(v, 6) for v in per_run],
            }
        entry["ray_job"] = [r.get("ray_job_secs") for r in runs]
        entry["split"] = [sorted(r["routing"]["per_worker"].values(), reverse=True) for r in runs]
        # A per-engine count that isn't a multiple of 8 proves the rollout split at
        # least one prompt's 8 samples. Round-robin splits every prompt 4/4.
        entry["split_rollouts"] = sum(
            1 for r in runs for b in r["routing"]["bursts"] if any(v % 8 for v in b.values())
        )
        entry["bursts"] = sum(len(r["routing"]["bursts"]) for r in runs)
        entry["round_robin"] = all("round-robin" in r["routing"]["modes"] for r in runs)
        data[phase] = entry
    metrics_js = [
        {"key": k, "title": t, "unit": u, "dec": d, "note": n} for k, t, u, _f, _s, _sc, d, n in METRICS
    ]
    page_bg = (
        "body{margin:0;padding:24px;background:#fcfcfb;color:#0b0b0b;font-family:system-ui,-apple-system,'Segoe UI',sans-serif}"
        ":root{--foreground:#0b0b0b;--muted-foreground:#52514e;--border:#e1e0d9;--chart-1:#2a78d6;--popover:#ffffff;--popover-foreground:#0b0b0b;--background:#fcfcfb}"
        "@media (prefers-color-scheme: dark){body{background:#1a1a19;color:#f0efec}"
        ":root{--foreground:#f0efec;--muted-foreground:#c3c2b7;--border:#2c2c2a;--chart-1:#3987e5;--popover:#262624;--popover-foreground:#f0efec;--background:#1a1a19}}"
        if standalone
        else ""
    )
    doc = f"""{"<!doctype html>" if standalone else ""}
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{"<title>Slime routing comparison</title>" if standalone else ""}
<style>
{page_bg}
.viz {{ font-family: var(--font-sans, system-ui, sans-serif); color: var(--foreground); }}
.viz h3 {{ font-size: 14px; font-weight: 600; margin: 18px 0 2px; }}
.viz h3:first-child {{ margin-top: 0; }}
.viz .note {{ font-size: 12px; color: var(--muted-foreground); margin: 0 0 6px; }}
.viz .lede {{ font-size: 13px; color: var(--muted-foreground); margin: 0 0 12px; }}
.viz svg {{ display: block; width: 100%; height: auto; overflow: visible; }}
.viz .row-label {{ font-size: 12px; fill: var(--foreground); }}
.viz .row-label.dim {{ fill: var(--muted-foreground); }}
.viz .value {{ font-size: 12px; fill: var(--foreground); font-variant-numeric: tabular-nums; }}
.viz .tick {{ font-size: 11px; fill: var(--muted-foreground); font-variant-numeric: tabular-nums; }}
.viz .ref-label {{ font-size: 11px; fill: var(--muted-foreground); }}
.viz .grid {{ stroke: var(--border); stroke-width: 1; }}
.viz .ref {{ stroke: var(--muted-foreground); stroke-width: 1; }}
.viz .bar.accent {{ fill: var(--chart-1); }}
.viz .bar.baseline {{ fill: var(--muted-foreground); opacity: .85; }}
.viz .bar.context {{ fill: var(--muted-foreground); opacity: .38; }}
.viz .dot {{ fill: var(--foreground); stroke: var(--background, #fff); stroke-width: 2; }}
.viz .hit {{ fill: transparent; cursor: default; }}
.viz .row:hover .bar, .viz .row:focus .bar {{ opacity: 1; }}
.viz .tip {{ position: fixed; pointer-events: none; background: var(--popover); color: var(--popover-foreground, var(--foreground));
  border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; font-size: 12px; line-height: 1.45;
  box-shadow: 0 4px 16px rgba(0,0,0,.18); display: none; max-width: 320px; z-index: 10; }}
.viz details {{ margin-top: 16px; font-size: 12px; }}
.viz summary {{ cursor: pointer; color: var(--muted-foreground); }}
.viz table {{ border-collapse: collapse; margin-top: 8px; width: 100%; font-variant-numeric: tabular-nums; }}
.viz th, .viz td {{ text-align: left; padding: 4px 8px 4px 0; border-bottom: 1px solid var(--border); vertical-align: top; }}
.viz th {{ font-weight: 600; }}
</style>
</head>
<body>
<div class="viz" id="viz">
<p class="lede">Slime GRPO, Qwen3-0.6B on GSM8K, 10 steps, 2 SGLang engines + 1 trainer on one H100 node.
Bars: mean over every step of three RayJobs per configuration. Dots: each RayJob's mean.</p>
</div>
<div class="tip" id="tip" role="tooltip"></div>
<script>
const DATA = {json.dumps(data)};
const METRICS = {json.dumps(metrics_js)};
const ORDER = {json.dumps([c[0] for c in CONFIGS])};
const NS = "http://www.w3.org/2000/svg";
const root = document.getElementById("viz");
const tip = document.getElementById("tip");
function el(tag, attrs, parent) {{
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs || {{}})) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}}
function fmt(v, dec, unit) {{ return v.toFixed(dec) + (unit === "%" ? "%" : " " + unit); }}
function tickFmt(v, unit) {{ return unit === "%" || v >= 10 ? v.toFixed(0) : v.toFixed(1); }}
function niceMax(v) {{
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}}
const phases = ORDER.filter((p) => DATA[p]);
const W = 680, LABEL_W = 220, RIGHT = 70, ROW_H = 30, BAR_H = 14, TOP = 18, AXIS_H = 22;
for (const m of METRICS) {{
  const h3 = document.createElement("h3"); h3.textContent = m.title + " (" + m.unit + ")"; root.appendChild(h3);
  const note = document.createElement("p"); note.className = "note"; note.textContent = m.note; root.appendChild(note);
  const H = TOP + phases.length * ROW_H + AXIS_H;
  const svg = el("svg", {{ viewBox: `0 0 ${{W}} ${{H}}`, role: "img", "aria-label": m.title }}, root);
  let vmax = 0;
  for (const p of phases) {{ const d = DATA[p].metrics[m.key]; vmax = Math.max(vmax, d.mean, ...d.runs); }}
  const xmax = niceMax(vmax * 1.08);
  const x = (v) => LABEL_W + (v / xmax) * (W - LABEL_W - RIGHT);
  for (let i = 0; i <= 4; i++) {{
    const v = (xmax * i) / 4, gx = x(v);
    el("line", {{ x1: gx, x2: gx, y1: TOP - 6, y2: H - AXIS_H + 2, class: "grid" }}, svg);
    const t = el("text", {{ x: gx, y: H - 6, "text-anchor": "middle", class: "tick" }}, svg);
    t.textContent = tickFmt(v, m.unit);
  }}
  if (DATA.rr) {{
    const rx = x(DATA.rr.metrics[m.key].mean);
    el("line", {{ x1: rx, x2: rx, y1: TOP - 10, y2: H - AXIS_H + 2, class: "ref" }}, svg);
    const t = el("text", {{ x: rx + 4, y: TOP - 4, class: "ref-label" }}, svg);
    t.textContent = "round-robin tutorial";
  }}
  phases.forEach((p, i) => {{
    const d = DATA[p], md = d.metrics[m.key];
    const cy = TOP + i * ROW_H + ROW_H / 2;
    const g = el("g", {{ class: "row", tabindex: 0 }}, svg);
    const lab = el("text", {{ x: 0, y: cy + 4, class: "row-label" + (d.role === "context" ? " dim" : "") }}, g);
    lab.textContent = d.label;
    const x0 = x(0), x1 = x(md.mean), r = 4;
    const path = `M${{x0}},${{cy - BAR_H / 2}} H${{x1 - r}} Q${{x1}},${{cy - BAR_H / 2}} ${{x1}},${{cy - BAR_H / 2 + r}} V${{cy + BAR_H / 2 - r}} Q${{x1}},${{cy + BAR_H / 2}} ${{x1 - r}},${{cy + BAR_H / 2}} H${{x0}} Z`;
    el("path", {{ d: path, class: "bar " + d.role }}, g);
    for (const v of md.runs) el("circle", {{ cx: x(v), cy: cy, r: 4, class: "dot" }}, g);
    const val = el("text", {{ x: Math.max(x1, ...md.runs.map(x)) + 8, y: cy + 4, class: "value" }}, g);
    val.textContent = fmt(md.mean, m.dec, m.unit);
    el("rect", {{ x: 0, y: cy - ROW_H / 2, width: W, height: ROW_H, class: "hit" }}, g);
    const show = (ev) => {{
      const runs = md.runs.map((v) => fmt(v, m.dec, m.unit)).join(", ");
      tip.innerHTML = `<b>${{d.label}}</b><br>${{m.title}}: ${{fmt(md.mean, m.dec, m.unit)}} ± ${{md.sd.toFixed(m.dec)}} (n=${{md.n}} steps)<br>Per RayJob: ${{runs}}`;
      tip.style.display = "block";
      const bx = ev.clientX ?? (g.getBoundingClientRect().left + 40), by = ev.clientY ?? g.getBoundingClientRect().top;
      tip.style.left = Math.min(bx + 14, window.innerWidth - 330) + "px"; tip.style.top = (by + 14) + "px";
    }};
    g.addEventListener("mousemove", show); g.addEventListener("focus", show);
    g.addEventListener("mouseleave", () => (tip.style.display = "none")); g.addEventListener("blur", () => (tip.style.display = "none"));
  }});
}}
const det = document.createElement("details");
let rows = phases.map((p) => {{
  const d = DATA[p];
  const cells = METRICS.map((m) => {{ const md = d.metrics[m.key]; return `<td>${{fmt(md.mean, m.dec, m.unit)}} ± ${{md.sd.toFixed(m.dec)}}<br><span style="color:var(--muted-foreground)">${{md.runs.map((v) => v.toFixed(m.dec)).join(" / ")}}</span></td>`; }}).join("");
  const job = d.ray_job.map((v) => v == null ? "–" : v.toFixed(0)).join(" / ");
  const split = d.split.map((s) => s.join("/")).join(", ");
  return `<tr><td>${{d.label}}</td>${{cells}}<td>${{job}}</td><td>${{split}}<br><span style="color:var(--muted-foreground)">${{d.round_robin ? "round-robin splits every prompt's 8 samples 4/4" : `${{d.split_rollouts}} of ${{d.bursts}} rollouts split at least one prompt across engines`}}</span></td></tr>`;
}}).join("");
det.innerHTML = `<summary>Table view</summary><table><thead><tr><th>Configuration</th>${{METRICS.map((m) => `<th>${{m.title}}</th>`).join("")}}<th>Ray job (s)</th><th>Requests per engine</th></tr></thead><tbody>${{rows}}</tbody></table>`;
root.appendChild(det);
</script>
</body>
</html>
"""
    out_path.write_text(doc)
    print(f"{out_path}: {len(doc):,} bytes; phases: {', '.join(data)}")


if __name__ == "__main__":
    main()
