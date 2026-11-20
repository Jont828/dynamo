#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Turn the replay's in-band chapter tokens into asciicast markers.

Usage: cast_markers.py IN.cast OUT.cast      (IN and OUT may be the same file)
       cast_markers.py --list CAST           print the markers and their times

lib.sh's `chapter "Title"` prints an invisible OSC token, ESC ] 7770;chapter;Title BEL,
and the beat runner prints ESC ] 7770;beat;NN BEL. asciinema records them as output.
This script removes every 7770 token from the output events and puts an asciicast v3
marker event, [interval, "m", "Title"], where each chapter token was. Tokens split
across two output events (a pty read boundary) are handled. Timing is unchanged:
an event that held only a token passes its interval on to the next event.

asciinema 3.x writes asciicast v3: events are [interval since the previous event,
code, data], and a marker is code "m" with its label as data. asciinema-player (3.x)
shows markers on the progress bar and as chapters; agg ignores them.
"""

import json
import re
import sys

TOKEN = re.compile(r"\x1b\]7770;([a-z]+);([^\x07]*)\x07")


def load(path):
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    header = json.loads(lines[0])
    if header.get("version") != 3:
        sys.exit(f"cast_markers.py: {path} is asciicast v{header.get('version')}; expected v3 (asciinema 3.x)")
    events = [json.loads(line) for line in lines[1:] if line.startswith("[")]
    return header, events


def convert(events):
    o_data = [e[2] for e in events if e[1] == "o"]
    joined = "".join(o_data)
    spans = [(m.start(), m.end(), m.group(1), m.group(2)) for m in TOKEN.finditer(joined)]
    out, carry, pos, si = [], 0.0, 0, 0
    for dt, code, data in events:
        if code != "o":
            out.append([round(dt + carry, 6), code, data])
            carry = 0.0
            continue
        start, end = pos, pos + len(data)
        pos = end
        pieces, cur = [], start
        while si < len(spans) and spans[si][0] < end:
            s, e, kind, val = spans[si]
            if s > cur:
                pieces.append(("o", joined[cur:s]))
            if s >= start and kind == "chapter":
                pieces.append(("m", val))
            if e <= end:
                cur = max(cur, e)
                si += 1
            else:
                cur = end
                break
        if cur < end:
            pieces.append(("o", joined[cur:end]))
        if not pieces:
            carry += dt
            continue
        for k, (c, payload) in enumerate(pieces):
            out.append([round(dt + carry, 6) if k == 0 else 0.0, c, payload])
        carry = 0.0
    return out


def markers(events, idle=None):
    t, shown, res = 0.0, 0.0, []
    for dt, code, data in events:
        t += dt
        shown += min(dt, idle) if idle else dt
        if code == "m":
            res.append((t, shown, data))
    return res


def fmt(s):
    m, sec = divmod(s, 60)
    return f"{int(m)}:{sec:05.2f}"


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--list":
        header, events = load(sys.argv[2])
        for t, shown, label in markers(events, header.get("idle_time_limit")):
            print(f"{fmt(t)}  {label}")
        return
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    header, events = load(sys.argv[1])
    out = convert(events)
    left = sum(1 for e in out if e[1] == "o" and "\x1b]7770;" in e[2])
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        f.write(json.dumps(header, ensure_ascii=False) + "\n")
        for e in out:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    ms = markers(out)
    total = sum(e[0] for e in out)
    print(f"{sys.argv[2]}: {len(out)} events, {len(ms)} markers, {fmt(total)}")
    for t, _, label in ms:
        print(f"  {fmt(t)}  {label}")
    if left:
        sys.exit(f"cast_markers.py: {left} events still hold a 7770 token")


if __name__ == "__main__":
    main()
