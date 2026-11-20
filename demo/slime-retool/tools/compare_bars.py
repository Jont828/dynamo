"""Draw compare_runs.py's numbers as terminal bar charts: one run against the other.

Usage: python3 -I compare_bars.py <compare.json> [--part work|time|slowest|all]
                                  [--color auto|always|never] [--width N] [--labels A B]

Reads the JSON that tools/compare_runs.py writes with --out (runs/compare-kv-rr.json) and prints
bar charts in Unicode blocks. Python standard library only.
- Horizontal bars are half a text row tall (▄, ending in ▖ for a half cell), so a gap of
  background separates the two runs' bars: a precision of half a cell, about 1% of the track.
- Columns grow in eighths of a row (▁▂▃▄▅▆▇█) from a baseline, two cells wide, the two runs side
  by side. The baseline and the gridlines (▁, gray) sit at exactly the values they're labeled with;
  each label is printed just above its line.

Parts (--part; default all, the three in this order):
  work     what the router changed inside the engines, summed over the run: the prefix-cache hit
           rate, the prompt tokens prefilled (computed because they weren't cached), the follow-up
           turns that went back to the engine that served the trajectory's previous turn (with the
           1-in-N chance mark for N engines), and the prompt tokens prefilled in each step
  time     whether the run got faster: the time until half of a step's trajectories were done
           (mean per step), follow-up turns' time to first token (median), the rollout time summed
           over the steps, and the rollout time per step with each step's difference
  slowest  where each step's slowest trajectory, the one its rollout waits for, spent its time,
           summed over the steps: generating tokens, between turns (the sandbox runs the model's
           code), waiting for first tokens, and before its first turn

Every bar starts at zero and carries its value at the tip. The work and time charts' titles say
which way is better. Run A (the JSON's first name) is blue, #3987e5, and run B orange, #d95926:
the dark steps of the blue and orange that tools/compare_chart.py uses, which pass the dataviz
palette checks (lightness, chroma, color-blind separation, and contrast) on a dark terminal
(#0c0d0b). Text and values stay in the terminal's own colors; the baseline and the chance mark
are gray (ANSI 90), the gridlines a dimmer gray (#3a3e44).

--color auto (default) colors only a terminal and honors NO_COLOR; always forces the escapes
(24-bit color), for a capture; never drops them. Without color, every bar row still names its
run, and in a column chart run A's column is the left one of each pair.
--width caps the line width (default 108, at least 80): bar tracks shrink, titles wrap, and the
column charts' gaps narrow to fit.
--labels names the runs in the charts (default: KV -> KV-aware, RR -> Round-robin; any other
name as it is).
"""

import argparse
import json
import math
import os
import pathlib
import sys
import textwrap

COLORS = ((0x39, 0x87, 0xE5), (0xD9, 0x59, 0x26))   # run A blue, run B orange
LABELS = {"KV": "KV-aware", "RR": "Round-robin"}
EIGHTHS_V = " ▁▂▃▄▅▆▇█"                               # bottom-aligned eighths, 0-8
NICE = (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10)
MINUS = "−"
COL_W, PAIR_GAP = 2, 3                               # column width; cells between steps' pairs


class Paint:
    """ANSI escapes, or nothing when color is off."""

    def __init__(self, on):
        self.on = on

    def _sgr(self, code, text):
        return f"\x1b[{code}m{text}\x1b[0m" if self.on and text else text

    def run(self, i, text):
        r, g, b = COLORS[i]
        return self._sgr(f"38;2;{r};{g};{b}", text)

    def bold(self, text):
        return self._sgr("1", text)

    def gray(self, text):
        return self._sgr("90", text)

    def grid(self, text):
        return self._sgr("38;2;58;62;68", text)   # gridlines: #3a3e44, a step off the dark background


def nice_top(x):
    """The smallest 'nice' number (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8 times a power of 10) >= x."""
    if x <= 0:
        return 1
    mag = 10 ** math.floor(math.log10(x))
    return next(m * mag for m in NICE if m * mag >= x * (1 - 1e-12))


def signed(x, nd=0, unit=""):
    """+42, −35 (a real minus sign), and 0 for anything that rounds to zero."""
    s = f"{abs(x):,.{nd}f}"
    if float(s.replace(",", "")) == 0:
        return f"0{unit}"
    return f"{'+' if x > 0 else MINUS}{s}{unit}"


def hbar(value, top, width):
    """A half-row-tall bar for value/top of width cells, to the nearest half cell."""
    full, half = divmod(max(0, round(value / top * width * 2)), 2)
    return "▄" * full + ("▖" if half else "")


class Charts:
    def __init__(self, data, labels, paint, width):
        self.na, self.nb = data["names"]
        self.A, self.B = data[self.na], data[self.nb]
        self.labels = labels
        self.p = paint
        self.width = width
        self.steps = sorted(set(self.A["steps"]) & set(self.B["steps"]), key=int)
        self.lw = max(len(x) for x in labels)                # series label column
        engines = {len(self.A["per_engine_requests"]), len(self.B["per_engine_requests"])}
        if len(engines) != 1:
            sys.exit("compare_bars.py: the two runs used different numbers of engines")
        self.engines = engines.pop()

    # --- pieces -----------------------------------------------------------------------------

    def legend(self):
        a = self.p.run(0, "██") + " " + self.labels[0]
        b = self.p.run(1, "██") + " " + self.labels[1]
        return [f"  {a}   {b}"]

    def title(self, text, better=None, more=None):
        """The chart's title in bold, then which way is better (and more), wrapped to the width."""
        note = "; ".join(x for x in (f"{better} is better" if better else "", more) if x)
        lines = textwrap.wrap(text, self.width - 2)
        out = ["  " + self.p.bold(x) for x in lines]
        if note:
            note = f"({note})"
            if len(lines[-1]) + 2 + len(note) <= self.width - 2:
                out[-1] += "  " + note
            else:
                out += ["  " + x for x in textwrap.wrap(note, self.width - 2)]
        return out

    def pair(self, values, shown, top, track, note="", lead=""):
        """Two bars, run A over run B, on one zero-based scale. note follows run B's value."""
        rows = []
        for i, (v, s) in enumerate(zip(values, shown)):
            bar = hbar(v, top, track)
            tail = f"  {note}" if note and i == 1 else ""
            rows.append(f"  {lead}{self.labels[i]:<{self.lw}}  {self.p.run(i, bar)} {s}{tail}")
            lead = " " * len(lead)
        return rows

    def track(self, extra):
        """Cells left for a bar after the fixed columns and the widest value text (extra)."""
        return max(20, min(64, self.width - (2 + self.lw + 2 + 1 + extra)))

    def columns(self, title, a, b, top, rows, tick, diff=None, diff_label=""):
        """A column chart, a pair of columns per step (run A left), growing in eighths of a row.

        Every second row's bottom edge carries a line: the baseline (0, gray) at the bottom and
        gridlines (dimmer) above it, each labeled at its left, so a column that reaches a line
        has reached that value. Empty rows at the top are left out. diff: one text per step,
        printed under the step numbers.
        """
        n = len(self.steps)
        unit = top / rows
        lined = {r: tick(unit * (r - 1)) for r in range(1, rows + 1) if (r - 1) % 2 == 0}
        gw = max(len(x) for x in [*lined.values(), diff_label, "step"])
        pw = 2 * COL_W + 1
        gap = PAIR_GAP if 2 + gw + 2 + n * (pw + PAIR_GAP) - PAIR_GAP + 1 <= self.width else PAIR_GAP - 1
        out = list(title)
        eighths = [[round(v / unit * 8) for v in (a[k], b[k])] for k in range(n)]
        for r in range(rows, 0, -1):
            label = lined.get(r, "")
            line = ("base" if r == 1 else "grid") if label else None
            runs = []                                   # [kind, text]: kind 0 or 1 (a run), base, grid, None

            def add(kind, text):
                if runs and runs[-1][0] == kind:
                    runs[-1][1] += text
                else:
                    runs.append([kind, text])

            def space(cells):
                add(line, ("▁" if line else " ") * cells)

            space(1)
            for k in range(n):
                for i in (0, 1):
                    e = min(8, max(0, eighths[k][i] - 8 * (r - 1)))
                    if e:
                        add(i, EIGHTHS_V[e] * COL_W)
                    else:
                        space(COL_W)
                    if i == 0:
                        space(1)
                space(gap if k < n - 1 else 1)
            while runs and runs[-1][0] is None:         # no trailing blanks
                runs.pop()
            paint = {0: lambda t: self.p.run(0, t), 1: lambda t: self.p.run(1, t), "base": self.p.gray,
                     "grid": self.p.grid, None: lambda t: t}
            text = "".join(paint[kind](t) for kind, t in runs)
            row = f"  {label:>{gw}} {text}".rstrip()
            if len(out) > len(title) or row.strip():    # leave out empty rows at the top
                out.append(row)
        steps = "".join(f"{k:^{pw}}" + " " * gap for k in self.steps).rstrip()
        out.append(f"  {'step':>{gw}}  {steps}")
        if diff:
            row = "".join(f"{d:^{pw}}" + " " * gap for d in diff).rstrip()
            out.append(f"  {diff_label:>{gw}}  {row}")
        return out

    # --- the parts --------------------------------------------------------------------------

    def work(self):
        A, B = self.A, self.B
        out = self.legend() + [""]
        t = self.track(6)
        out += self.title("Prefix-cache hit rate: the share of prompt tokens found already cached", "higher")
        out += self.pair([A["hit_rate"], B["hit_rate"]], [f"{100 * A['hit_rate']:.1f}%", f"{100 * B['hit_rate']:.1f}%"],
                         1, t)
        out.append("")
        out += self.title("Prompt tokens prefilled: computed because they weren't cached", "lower")
        ca, cb = A["computed_tokens"], B["computed_tokens"]
        out += self.pair([ca, cb], [f"{ca / 1e6:.2f}M", f"{cb / 1e6:.2f}M"], nice_top(max(ca, cb)), self.track(15),
                         note=f"×{cb / ca:.2f}")
        out.append("")
        out += self.title("Follow-up turns sent back to the engine that served their previous turn", "higher")
        sa, sb = A["same_engine"], B["same_engine"]
        out += self.pair([sa, sb], [f"{100 * sa:.0f}%", f"{100 * sb:.0f}%"], 1, t)
        chance = 1 / self.engines
        at = 2 + self.lw + 2 + math.floor(chance * t)
        out.append(" " * at + self.p.gray("↑") + f" {100 * chance:.0f}%: chance alone, with {self.engines} engines")
        out.append("")
        pa = [A["steps"][k]["computed_tokens"] / 1e6 for k in self.steps]
        pb = [B["steps"][k]["computed_tokens"] / 1e6 for k in self.steps]
        top = nice_top(max(pa + pb))
        out += self.columns(self.title("Prompt tokens prefilled in each step, millions", "lower"), pa, pb, top, 6,
                            lambda v: f"{v:.1f}M" if v else "0")
        return out

    def time(self):
        A, B = self.A, self.B
        out = self.legend() + [""]
        half = lambda R: sum(R["steps"][k]["finished_by"]["p50"] for k in self.steps) / len(self.steps)  # noqa: E731
        ha, hb = half(A), half(B)
        out += self.title(f"Time until half of a step's trajectories were done, mean of the {len(self.steps)} steps",
                          "lower")
        out += self.pair([ha, hb], [f"{ha:.1f} s", f"{hb:.1f} s"], nice_top(max(ha, hb)), self.track(6))
        out.append("")
        ta, tb = A["latency"]["followup"]["ttft_p50_ms"], B["latency"]["followup"]["ttft_p50_ms"]
        out += self.title("Follow-up turns: time to first token, median", "lower")
        out += self.pair([ta, tb], [f"{ta:.0f} ms", f"{tb:.0f} ms"], nice_top(max(ta, tb)), self.track(6))
        out.append("")
        ra, rb = A["rollout_sum_s"], B["rollout_sum_s"]
        out += self.title(f"Rollout time, sum of the {len(self.steps)} steps", "lower")
        out += self.pair([ra, rb], [f"{ra:,.0f} s", f"{rb:,.0f} s"], nice_top(max(ra, rb)), self.track(16),
                         note=signed(100 * (rb / ra - 1), 1, "%"))
        out.append("")
        a = [A["steps"][k]["rollout_s"] for k in self.steps]
        b = [B["steps"][k]["rollout_s"] for k in self.steps]
        diff = [signed(y - x) for x, y in zip(a, b)]
        out += self.columns(self.title("Rollout time in each step, seconds", "lower",
                                       f"diff = {self.labels[1].lower()} minus {self.labels[0]}"),
                            a, b, nice_top(max(a + b)), 6, lambda v: f"{v:.0f}", diff, "diff")
        return out

    def slowest(self):
        parts = [("generating tokens", "decode_s"), ("between turns (sandbox)", "between_requests_s"),
                 ("waiting for first tokens", "ttft_s"), ("before its first turn", "wait_before_first_s")]
        sums = []
        for R in (self.A, self.B):
            crit = [R["steps"][k]["critical"] for k in self.steps]
            sums.append({key: sum(c[key] for c in crit) for _, key in parts})
            total = sum(c["span_s"] for c in crit)
            if abs(sum(sums[-1].values()) - total) > 0.05:
                sys.exit("compare_bars.py: the slowest trajectories' parts don't add up to their span")
        cw = max(len(name) for name, _ in parts)
        top = max(s[key] for s in sums for _, key in parts)
        t = max(20, min(64, self.width - (2 + cw + 2 + self.lw + 2 + 1 + 5)))
        out = self.legend() + [""]
        out += self.title(f"Where each step's slowest trajectory, the one its rollout waits for, spent its time "
                          f"(sum of {len(self.steps)} steps)")
        for j, (name, key) in enumerate(parts):
            if j:
                out.append("")
            out += self.pair([s[key] for s in sums], [f"{s[key]:,.0f} s" for s in sums], top, t,
                             lead=f"{name:<{cw}}  ")
        return out


def main():
    ap = argparse.ArgumentParser(description="Terminal bar charts of compare_runs.py's JSON.")
    ap.add_argument("compare_json", type=pathlib.Path)
    ap.add_argument("--part", choices=["work", "time", "slowest", "all"], default="all")
    ap.add_argument("--color", choices=["auto", "always", "never"], default="auto")
    ap.add_argument("--width", type=int, default=108)
    ap.add_argument("--labels", nargs=2, metavar=("A", "B"))
    args = ap.parse_args()
    if args.width < 80:
        ap.error("--width must be at least 80")
    data = json.loads(args.compare_json.read_text())
    labels = args.labels or [LABELS.get(n, n) for n in data["names"]]
    on = args.color == "always" or (args.color == "auto" and sys.stdout.isatty() and not os.environ.get("NO_COLOR"))
    charts = Charts(data, labels, Paint(on), args.width)
    parts = ["work", "time", "slowest"] if args.part == "all" else [args.part]
    out = []
    for i, part in enumerate(parts):
        if i:
            out.append("")
        out += getattr(charts, part)()
    print("\n".join(out))


if __name__ == "__main__":
    main()
