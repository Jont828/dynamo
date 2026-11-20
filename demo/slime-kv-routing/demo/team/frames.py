#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Render chosen frames of a cast as PNG files, to look at them.

Usage (venv Python, -I):
  frames.py CAST OUTDIR [--at SECONDS ...] [--when TEXT[@DELAY] ...] [--markers]

Renders CAST once with agg (--font-size 16 --fps-cap 15 --idle-time-limit 2, as
the docs' GIF is made) to OUTDIR/<cast name>.gif, then extracts frames with ffmpeg:
  --at 12.5            the frame at 12.5 s of the cast
  --when 'written'     the frame 0.3 s after the output first contains "written"
  --when 'written@2'   ... 2 s after (use it to see a callout or a card fully drawn)
  --when 'written#3'   ... after its 3rd appearance (combine: 'text#3@1.5')
  --markers            the frame 1 s after each marker (chapter)
Prints each PNG's path with its time. Times are cast times, mapped through agg's
idle-time limit so they match the GIF.
"""

import argparse
import json
import pathlib
import re
import subprocess
import sys

IDLE = 2.0


def load(cast):
    lines = pathlib.Path(cast).read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    events = [json.loads(line) for line in lines[1:] if line.startswith("[")]
    if header.get("version") != 3:
        sys.exit("frames.py: expects an asciicast v3 file (asciinema 3.x writes v3)")
    t = g = 0.0
    out = []  # (cast time, gif time, code, data)
    for interval, code, data in events:
        t += interval
        g += min(interval, IDLE)
        out.append((t, g, code, data))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cast")
    ap.add_argument("outdir")
    ap.add_argument("--at", type=float, action="append", default=[])
    ap.add_argument("--when", action="append", default=[])
    ap.add_argument("--markers", action="store_true")
    ap.add_argument("--no-render", action="store_true", help="reuse OUTDIR/<name>.gif")
    args = ap.parse_args()

    events = load(args.cast)
    out = pathlib.Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)
    stem = pathlib.Path(args.cast).stem
    gif = out / f"{stem}.gif"
    if not args.no_render or not gif.exists():
        r = subprocess.run(["agg", "--font-size", "16", "--fps-cap", "15", "--idle-time-limit", str(int(IDLE)),
                            args.cast, str(gif)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if r.returncode:
            sys.exit(f"frames.py: agg failed: {r.stderr.decode()[-500:]}")

    # Output stream with each character's event time, to find text.
    stream, times = [], []
    for t, g, code, data in events:
        if code == "o":
            plain = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07", "", data)
            stream.append(plain)
            times.extend([g] * len(plain))
    text = "".join(stream)

    shots = []  # (gif time, label)
    for a in args.at:
        g = 0.0
        for t, gt, _, _ in events:
            if t <= a:
                g = gt + (a - t)
        shots.append((g, f"at{a:g}"))
    for spec in args.when:
        m = re.fullmatch(r"(.*?)(?:#(\d+))?(?:@([0-9.]+))?", spec, re.S)
        needle, nth, delay = m.group(1), int(m.group(2) or 1), float(m.group(3) or 0.3)
        pos = -1
        for _ in range(nth):
            pos = text.find(needle, pos + 1)
            if pos < 0:
                break
        if pos < 0:
            print(f"frames.py: {needle!r} (#{nth}) not in the output", file=sys.stderr)
            continue
        end = pos + len(needle) - 1
        slug = re.sub(r"[^A-Za-z0-9]+", "-", needle).strip("-")[:30] or "text"
        shots.append((times[end] + delay, f"when-{slug}" + (f"-{nth}" if nth > 1 else "")))
    if args.markers:
        for k, (t, g, code, data) in enumerate([e for e in events if e[2] == "m"]):
            slug = re.sub(r"[^A-Za-z0-9]+", "-", data).strip("-")[:30]
            shots.append((g + 1.0, f"marker{k:02d}-{slug}"))

    for g, label in shots:
        png = out / f"{stem}-{label}.png"
        # agg writes a frame only when the screen changes, and -ss would return the next
        # change after g. Decode up to g instead and keep the last frame: what shows at g.
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-t", f"{max(g, 0.05):.3f}", "-i", str(gif),
                            "-update", "1", str(png)])
        if r.returncode == 0 and png.exists():
            print(f"{png}  (gif {g:.1f}s)")
        else:
            print(f"frames.py: no frame at {g:.2f}s for {label}", file=sys.stderr)


if __name__ == "__main__":
    main()
