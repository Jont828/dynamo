#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Dry-run the team replay; report each beat's duration and width.

Usage (run with the venv Python, -I):
  check.py                  build the full script (build.sh), dry-run it, report every beat
  check.py 03 04            dry-run just these beats (through run.sh), report them
  check.py --script FILE    dry-run a script that's already built
  check.py --no-build       dry-run slime-gsm8k-team-demo.sh as it is
  check.py 03 --cast tmp/03.cast
                            also write a synthetic cast (the requested timing, a marker per
                            chapter, the fern-code theme) that agg renders without recording

Options:
  --overhead SECONDS   added per nap for the wall-clock estimate (default 0.009: measured on
                       a real recording of beat 99: the fork of sleep plus the drawing between naps).
                       That is a recording with NAP_CATCHUP=0; lib.sh's default catch-up gives the
                       time back, so a recording lands within a few percent of `duration`
  --json FILE          also write the report as JSON
  --quiet              only the table, lines over 120 columns, and warnings

The dry run sets DRYRUN=1: nap prints an invisible token with the seconds it would
sleep, and the runner prints a token at each beat. A small terminal emulator (unbounded
width, cursor moves, erases, the alternate screen, scroll regions) replays the output,
so in-place redraws are measured as drawn. Widths count wide characters (emoji, ⏩, ✅)
as 2 columns and box drawing as 1, as agg and asciinema-player do.

Exit status 1 if the dry run fails or a line is wider than 120 columns.
"""

import argparse
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import unicodedata

TEAM = pathlib.Path(__file__).resolve().parent
COLS, ROWS = 120, 32
FULL_SCRIPT = TEAM / "slime-gsm8k-team-demo.sh"

TOKEN_RE = re.compile(
    r"\x1b\[(?P<priv>[?>=]?)(?P<params>[0-9;]*)(?P<inter>[ -/]*)(?P<final>[@-~])"
    r"|\x1b\](?P<osc>[^\x07\x1b]*)(?:\x07|\x1b\\)"
    r"|\x1b(?P<esc>[ -~])"
    r"|(?P<ctl>[\x00-\x1a\x1c-\x1f\x7f])"
    r"|(?P<text>[^\x00-\x1f\x7f]+)",
    re.S,
)
OUR_OSC = re.compile(r"7770;(nap|beat|chapter);(.*)", re.S)


def char_width(ch: str) -> int:
    if ch in "‍︎️":
        return 0
    if unicodedata.category(ch) in ("Mn", "Me", "Cf"):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


class Beat:
    def __init__(self, nn):
        self.nn = nn
        self.secs = 0.0
        self.naps = 0
        self.widest = 0
        self.widest_text = ""
        self.over = []          # (width, text)
        self.chapters = []      # (title, start seconds in the whole run)
        self.warnings = []

    def warn(self, msg):
        if msg not in self.warnings:
            self.warnings.append(msg)


class Terminal:
    """Just enough of a VT100 to know what each row holds, with unbounded width."""

    def __init__(self):
        self.screens = {False: self._blank(), True: self._blank()}
        self.alt = False
        self.r = self.c = 0
        self.top, self.bot = 0, ROWS - 1
        self.saved = (0, 0)
        self.saved_1049 = (0, 0)
        self.cursor_on = True
        self.visit_max = 0
        self.beat = None

    @staticmethod
    def _blank():
        return [dict() for _ in range(ROWS)]

    @property
    def lines(self):
        return self.screens[self.alt]

    def row_text(self, r):
        line = self.lines[r]
        if not line:
            return ""
        return "".join(line.get(i, " ") for i in range(max(line) + 1)).rstrip()

    # A visit ends whenever the cursor leaves its row: measure what was drawn there.
    def leave_row(self):
        if self.visit_max and self.beat is not None:
            b = self.beat
            if self.visit_max > b.widest:
                b.widest, b.widest_text = self.visit_max, self.row_text(self.r)
            if self.visit_max > COLS:
                entry = (self.visit_max, self.row_text(self.r))
                if entry not in b.over:
                    b.over.append(entry)
        self.visit_max = 0

    def move(self, r=None, c=None):
        if r is not None and r != self.r:
            self.leave_row()
            if r < 0 and self.beat is not None:
                self.beat.warn("the cursor moved above the top row (a redraw taller than the screen?)")
            self.r = max(0, min(ROWS - 1, r))
        if c is not None:
            self.c = max(0, c)

    def scroll_up(self, n=1):
        lines = self.lines
        for _ in range(n):
            del lines[self.top]
            lines.insert(self.bot, dict())

    def scroll_down(self, n=1):
        lines = self.lines
        for _ in range(n):
            del lines[self.bot]
            lines.insert(self.top, dict())

    def linefeed(self):
        self.leave_row()
        if self.r == self.bot:
            self.scroll_up()
        elif self.r < ROWS - 1:
            self.r += 1

    def put_text(self, text):
        line = self.lines[self.r]
        for ch in text:
            w = char_width(ch)
            if ch in "️‍" and self.beat is not None:
                self.beat.warn(f"U+{ord(ch):04X} after {line.get(self.c - 1, '?')!r}: players disagree on its width; drop it")
            if w == 0:
                continue
            line[self.c] = ch
            if w == 2:
                line[self.c + 1] = ""
            self.c += w
            self.visit_max = max(self.visit_max, self.c)

    def erase_line(self, mode):
        line = self.lines[self.r]
        if mode == 0:
            for k in [k for k in line if k >= self.c]:
                del line[k]
        elif mode == 1:
            for k in [k for k in line if k <= self.c]:
                del line[k]
        else:
            line.clear()

    def erase_display(self, mode):
        if mode in (2, 3):
            if mode == 2:
                for line in self.lines:
                    line.clear()
            return
        self.erase_line(mode)
        rng = range(self.r + 1, ROWS) if mode == 0 else range(0, self.r)
        for r in rng:
            self.lines[r].clear()

    def csi(self, priv, params, final):
        nums = [int(p) if p else 0 for p in params.split(";")] if params else []
        n = nums[0] if nums and nums[0] else 1
        if priv == "?":
            for mode in nums:
                if mode == 25:
                    self.cursor_on = final == "h"
                elif mode in (47, 1047, 1049):
                    want = final == "h"
                    if want != self.alt:
                        self.leave_row()
                        if want and mode == 1049:
                            self.saved_1049 = (self.r, self.c)
                        self.alt = want
                        if want:
                            self.screens[True] = self._blank()
                        elif mode == 1049:
                            self.r, self.c = self.saved_1049
            return
        if final == "A":
            self.move(r=self.r - n)
        elif final == "B":
            self.move(r=self.r + n)
        elif final == "C":
            self.c += n
        elif final == "D":
            self.c = max(0, self.c - n)
        elif final == "E":
            self.move(r=self.r + n, c=0)
        elif final == "F":
            self.move(r=self.r - n, c=0)
        elif final == "G":
            self.c = n - 1
        elif final in "Hf":
            row = (nums[0] if nums and nums[0] else 1) - 1
            col = (nums[1] if len(nums) > 1 and nums[1] else 1) - 1
            self.move(r=row, c=col)
        elif final == "d":
            self.move(r=n - 1)
        elif final == "J":
            self.erase_display(nums[0] if nums else 0)
        elif final == "K":
            self.erase_line(nums[0] if nums else 0)
        elif final == "r":
            top = (nums[0] if nums and nums[0] else 1) - 1
            bot = (nums[1] if len(nums) > 1 and nums[1] else ROWS) - 1
            self.top, self.bot = top, bot
            self.move(r=0, c=0)
        elif final == "S":
            self.scroll_up(n)
        elif final == "T":
            self.scroll_down(n)
        elif final == "s":
            self.saved = (self.r, self.c)
        elif final == "u":
            self.move(r=self.saved[0], c=self.saved[1])
        # m (colors), L/M/P/@/X and the rest don't change what check.py measures.

    def esc(self, ch):
        if ch == "7":
            self.saved = (self.r, self.c)
        elif ch == "8":
            self.move(r=self.saved[0], c=self.saved[1])
        elif ch == "D":
            self.linefeed()
        elif ch == "E":
            self.linefeed()
            self.c = 0
        elif ch == "M":
            self.leave_row()
            if self.r == self.top:
                self.scroll_down()
            else:
                self.r = max(0, self.r - 1)
        elif ch == "c":
            self.__init__()

    def ctl(self, ch):
        if ch == "\n":          # the pty's onlcr turns \n into \r\n
            self.linefeed()
            self.c = 0
        elif ch == "\r":
            self.c = 0
        elif ch == "\b":
            self.c = max(0, self.c - 1)
        elif ch == "\t":
            self.c = (self.c // 8 + 1) * 8


def lint(paths):
    """Static checks on beat files: bash 4 features, raw sleep, echo -e, and similar traps."""
    rules = [
        (re.compile(r"\b(declare|local|typeset)\s+-[a-zA-Z]*A"), "associative arrays need bash 4"),
        (re.compile(r"\b(mapfile|readarray|coproc)\b"), "not in bash 3.2"),
        (re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*(,,|\^\^|,|\^)\}"), "case conversion needs bash 4"),
        (re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*@[QEPAa]\}"), "${x@Q} needs bash 4.4"),
        (re.compile(r"\b(declare|local)\s+-n\b"), "namerefs need bash 4.3"),
        (re.compile(r"\[\[\s+-v\s"), "[[ -v ]] needs bash 4.2"),
        (re.compile(r"&>>|\|&"), "needs bash 4"),
        (re.compile(r"(^|[;&|]\s*|\bthen\s+|\bdo\s+)sleep\s"), "use nap, not sleep (FAST, DRYRUN, and keepalives)"),
        (re.compile(r"\becho\s+-e\b"), "echo -e interprets backslashes in the text; use printf '%s\\n'"),
        (re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*[^\x00-\x7f]"), "bash 3.2 reads the next non-ASCII byte into the variable name; write ${name}"),
        (re.compile(r"'[^']*\\033\["), "a literal '\\033[' prints as text: use the palette variables, which hold real escapes"),
    ]
    out = []
    for path in paths:
        for n, line in enumerate(path.read_text().splitlines(), 1):
            code = line.split("#", 1)[0] if line.lstrip().startswith("#") else line
            if not code.strip():
                continue
            for rx, why in rules:
                if rx.search(code):
                    out.append(f"{path.relative_to(TEAM)}:{n}: {why}: {line.strip()[:90]}")
    return out


IP = r"(?<![\d.])10\.\d{1,3}\.\d{1,3}\.\d{1,3}(?![\d.]*\d)"
PARTIAL_IP = r"(?<![\d.])10\.\d{1,3}\.\d{1,3}\.?(?![\d.])"


def split_secrets(raw, plain):
    """IPs that tools/redact_cast.py would miss: the printed text holds them, but escape
    sequences or other text land between their characters in the output stream."""
    out = []
    shown, whole = re.findall(IP, plain), re.findall(IP, raw)
    for ip in sorted(set(shown)):
        if shown.count(ip) > whole.count(ip):
            out.append(f"{shown.count(ip) - whole.count(ip)} of {shown.count(ip)} printings of {ip} are split by "
                       "escape sequences; tools/redact_cast.py won't mask those")
    partial = [m.group(0) for m in re.finditer(PARTIAL_IP, plain)]
    if partial:
        out.append(f"10.x.y fragments with no 4th part (a split IP?): {sorted(set(partial))[:5]}")
    return out


def load_theme():
    spec = importlib.util.spec_from_file_location("fern_theme", TEAM.parent / "apply-fern-code-theme.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.THEME


def fmt_secs(s):
    m, sec = divmod(int(round(s)), 60)
    return f"{m}:{sec:02d}"


def run_dry(args):
    env = dict(os.environ, DRYRUN="1")
    env.pop("FAST", None)
    if args.script:
        cmd = ["/bin/bash", str(pathlib.Path(args.script).resolve())]
    elif args.beats:
        cmd = ["/bin/bash", str(TEAM / "run.sh"), *args.beats]
    else:
        if not args.no_build:
            b = subprocess.run(["/bin/bash", str(TEAM / "build.sh")], cwd=TEAM)
            if b.returncode:
                sys.exit("check.py: build.sh failed")
        cmd = ["/bin/bash", str(FULL_SCRIPT)]
    p = subprocess.run(cmd, env=env, cwd=TEAM, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    err = p.stderr.decode("utf-8", "replace")
    if p.returncode:
        sys.stderr.write(err)
        sys.exit(f"check.py: the dry run failed (exit {p.returncode}): {' '.join(cmd)}")
    return p.stdout.decode("utf-8", "replace"), err


def analyze(text, cast_path=None, title="Slime + Dynamo on AKS: GSM8K (dry run)"):
    term = Terminal()
    beats = {}
    order = []
    total = 0.0
    events, buf, pending = [], [], 0.0
    stray_esc = 0
    raw_parts, plain_parts = [], []

    def flush():
        nonlocal pending
        if buf:
            # A pty's onlcr turns \n into \r\n; the dry run's pipe didn't.
            data = re.sub(r"(?<!\r)\n", "\r\n", "".join(buf))
            events.append([round(pending, 6), "o", data])
            buf.clear()
            pending = 0.0

    for m in TOKEN_RE.finditer(text):
        g = m.lastgroup
        raw = m.group(0)
        if g == "osc":
            ours = OUR_OSC.fullmatch(m.group("osc"))
            if not ours:
                buf.append(raw)
                continue
            kind, val = ours.groups()
            if kind == "nap":
                s = float(val)
                total += s
                if term.beat is not None:
                    term.beat.secs += s
                    term.beat.naps += 1
                flush()
                pending += s
                while pending > 1.0:
                    events.append([1.0, "o", "\x1b[?25h" if term.cursor_on else "\x1b[?25l"])
                    pending -= 1.0
            elif kind == "beat":
                term.leave_row()
                if val not in beats:
                    beats[val] = Beat(val)
                    order.append(val)
                term.beat = beats[val]
            elif kind == "chapter":
                if term.beat is not None:
                    term.beat.chapters.append((val, total))
                flush()
                events.append([round(pending, 6), "m", val])
                pending = 0.0
            continue
        buf.append(raw)
        raw_parts.append(raw)
        if g == "text":
            plain_parts.append(raw)
            if "\\033[" in raw or "\\e[" in raw:
                stray_esc += 1
                if term.beat is not None:
                    term.beat.warn("printed a literal \\033[ (an escape written as text)")
            term.put_text(raw)
        elif g == "ctl":
            term.ctl(raw)
        elif g == "esc":
            term.esc(m.group("esc"))
        elif g == "final" or m.group("final"):
            term.csi(m.group("priv"), m.group("params"), m.group("final"))
    term.leave_row()
    flush()

    if cast_path:
        header = {
            "version": 3,
            "term": {"cols": COLS, "rows": ROWS, "theme": load_theme()},
            "idle_time_limit": 2,
            "title": title,
        }
        with open(cast_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(header) + "\n")
            for e in events:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
    split = split_secrets("".join(raw_parts), "".join(plain_parts))
    return [beats[nn] for nn in order], total, split


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("beats", nargs="*", help="beat numbers (default: the full build)")
    ap.add_argument("--script", help="dry-run this built script")
    ap.add_argument("--no-build", action="store_true", help="don't rebuild before a full check")
    ap.add_argument("--cast", help="write a synthetic cast of the dry run here")
    ap.add_argument("--overhead", type=float, default=0.009, help="seconds added per nap for the wall-clock estimate")
    ap.add_argument("--json", help="write the report as JSON here")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    args.beats = [f"{int(b):02d}" for b in args.beats]

    text, err = run_dry(args)
    if err.strip() and not args.quiet:
        sys.stderr.write(err)
    beats, total, split = analyze(text, args.cast)

    beat_files = []
    for b in beats:
        beat_files += sorted(TEAM.glob(f"beats/{b.nn}_*.sh"))
    lints = lint(beat_files)

    print(f"{'beat':<5}{'duration':>9}{'~wall':>9}{'naps':>7}{'widest':>8}{'>120':>6}  chapter")
    over_total = 0
    wall_total = 0.0
    for b in beats:
        wall = b.secs + b.naps * args.overhead
        wall_total += wall
        over_total += len(b.over)
        chap = b.chapters[0][0] if b.chapters else "(no chapter marker)"
        print(f"{b.nn:<5}{b.secs:>8.1f}s{wall:>8.1f}s{b.naps:>7}{b.widest:>8}{len(b.over):>6}  {chap}")
        for title, start in b.chapters[1:]:
            print(f"{'':<46}+ {title} (at {fmt_secs(start)})")
    print(f"{'total':<5}{fmt_secs(total):>9}{fmt_secs(wall_total):>9}")

    if not args.quiet:
        print()
        print("chapters (start, at the requested timing):")
        for b in beats:
            for title, start in b.chapters:
                print(f"  {fmt_secs(start):>6}  {b.nn}  {title}")
        for b in beats:
            if b.widest_text:
                print(f"beat {b.nn} widest ({b.widest}): {b.widest_text[:130]}")

    if over_total:
        print(f"\nLINES WIDER THAN {COLS} COLUMNS ({over_total}):")
        for b in beats:
            for w, t in b.over:
                print(f"  beat {b.nn}, {w} columns: {t}")
    warns = [(b.nn, w) for b in beats for w in b.warnings]
    for w in split:
        warns.append(("--", w))
    if not any(b.chapters for b in beats):
        warns.append(("--", "no chapter markers at all"))
    for b in beats:
        if not b.chapters:
            warns.append((b.nn, "no chapter marker: call chapter \"Title\" first in the beat"))
    if warns or lints:
        print("\nwarnings:")
        for nn, w in warns:
            print(f"  beat {nn}: {w}")
        for w in lints:
            print(f"  {w}")
    if args.cast:
        print(f"\nwrote {args.cast} (synthetic: requested timing; render it with agg)")
    if args.json:
        report = {
            "total_secs": round(total, 1),
            "beats": [
                {
                    "nn": b.nn, "secs": round(b.secs, 1), "naps": b.naps, "widest": b.widest,
                    "widest_text": b.widest_text, "over": [{"width": w, "text": t} for w, t in b.over],
                    "chapters": [{"title": t, "start": round(s, 1)} for t, s in b.chapters],
                    "warnings": b.warnings,
                }
                for b in beats
            ],
            "lint": lints,
        }
        pathlib.Path(args.json).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    sys.exit(1 if over_total else 0)


if __name__ == "__main__":
    main()
