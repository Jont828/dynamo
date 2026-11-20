#!/usr/bin/env python3
"""Re-time a demo.sh asciicast (v3) for watching without pausing. Content is untouched.

- Long waits (startup, training steps) are capped at --cap seconds; the watcher lines keep the real
  elapsed time in their [+mm:ss] stamps.
- Narration, which demo.sh types a word at a time, is slowed to --word seconds per word.
- Before each screen clear, the screen is held long enough to read its narration.
- Output bursts taller than the screen (the results report) scroll in line by line, pausing before
  each "── section" header so the previous section can be read first.

Usage: python3 pace_cast.py IN.cast OUT.cast
"""

import argparse
import json
import re

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
CLEAR = "\x1b[2J"
WORD_RE = re.compile(r"^\S+ $")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("src")
    parser.add_argument("dst")
    parser.add_argument("--cap", type=float, default=3.0, help="longest ordinary pause, seconds")
    parser.add_argument("--word", type=float, default=0.17, help="seconds per narration word")
    parser.add_argument("--section-hold", type=float, default=5.0, help="pause before each report section")
    parser.add_argument("--end-hold", type=float, default=10.0, help="final frame hold")
    args = parser.parse_args()

    with open(args.src, encoding="utf-8") as fh:
        header = json.loads(fh.readline())
        events = [json.loads(line) for line in fh if line.startswith("[")]
    header.pop("idle_time_limit", None)
    rows = header["term"]["rows"]

    # Merge back-to-back output chunks into bursts, so long outputs can be re-split on line ends.
    merged = []
    for interval, code, data in events:
        if merged and code == "o" and merged[-1][1] == "o" and interval < 0.01 and not data.startswith("\x1b[3J"):
            merged[-1][2] += data
        else:
            merged.append([interval, code, data])

    out, words, screen_has_report, first_clear = [], 0, False, True
    for interval, code, data in merged:
        plain = ANSI_RE.sub("", data)
        if code != "o":
            out.append([interval, code, data])
            continue
        if CLEAR in data:
            if first_clear:
                first_clear = False
            else:
                hold = 10.0 if screen_has_report else min(14.0, max(5.0, 3.0 + 0.08 * words))
                interval = max(min(interval, args.cap), hold)
            out.append([interval, code, data])
            words, screen_has_report = 0, False
            continue
        if 0.05 <= interval <= 0.12 and WORD_RE.match(plain):
            words += 1
            out.append([args.word, code, data])
            continue
        interval = min(interval, args.cap)
        if data.count("\n") < rows - 6:
            out.append([interval, code, data])
            continue
        screen_has_report = True
        for idx, line in enumerate(re.split(r"(?<=\r\n)", data)):
            if not line:
                continue
            gap = interval if idx == 0 else 0.03
            if idx and ANSI_RE.sub("", line).startswith("── "):
                gap = args.section_hold
            out.append([gap, code, line])

    exit_events = [e for e in out if e[1] == "x"]
    out = [e for e in out if e[1] != "x"]
    out.append([args.end_hold, "o", ""])
    out.extend([[0.0, code, data] for _, code, data in exit_events])

    with open(args.dst, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(header) + "\n")
        for interval, code, data in out:
            fh.write(json.dumps([round(interval, 3), code, data], ensure_ascii=False) + "\n")
    total = sum(e[0] for e in out)
    print(f"{args.dst}: {len(out)} events, {int(total // 60)} min {int(total % 60):02d} s")


if __name__ == "__main__":
    main()
