"""How much of each rollout an engine prefills before any of it is cached.

Usage: python3 -I first_batch.py <run-dir>...

For each engine log, splits the "Prefill batch" lines into rollouts (a gap of
more than 2 s starts a new one), skips the readiness probe's 1-token batches,
and reports, per rollout, the share of that engine's requests that went into
the rollout's first prefill batch. Those requests can't reuse a sibling's
prefix: nothing from the rollout is cached yet (weight syncs flush the cache).
Also prints the cached-token share of the later batches.
"""

import re
import statistics
import sys
from datetime import datetime
from pathlib import Path

LINE = re.compile(
    r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] Prefill batch, #new-seq: (\d+), #new-token: (\d+), #cached-token: (\d+)"
)
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def rollouts(path: Path):
    groups, last = [], None
    for raw in path.read_text(errors="replace").splitlines():
        m = LINE.search(ANSI.sub("", raw))
        if not m:
            continue
        t = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        seqs, new, cached = (int(x) for x in m.groups()[1:])
        if seqs == 1 and new + cached <= 8:  # readiness probe
            continue
        if last is None or (t - last).total_seconds() > 2:
            groups.append([])
        groups[-1].append((seqs, new, cached))
        last = t
    return groups


def main() -> None:
    big_share, big_hit, n_batches, uncached_req_share = [], [], [], []
    for d in sys.argv[1:]:
        for w in ("worker-0", "worker-1"):
            p = Path(d) / f"{w}.log"
            if not p.exists():
                continue
            for g in rollouts(p):
                total = sum(s for s, _, _ in g)
                if total < 8:
                    continue
                n_batches.append(len(g))
                big = max(g, key=lambda b: b[0])
                big_share.append(big[0] / total)
                big_hit.append(big[2] / (big[1] + big[2]))
                # Requests in batches that reused nothing at all.
                uncached_req_share.append(sum(s for s, _, c in g if c == 0) / total)
    print(
        f"rollout-engine pairs: {len(big_share)}; prefill batches per rollout: median "
        f"{statistics.median(n_batches):.0f}; the largest batch carries {100 * statistics.fmean(big_share):.0f}% "
        f"of the engine's requests (median {100 * statistics.median(big_share):.0f}%) with "
        f"{100 * statistics.fmean(big_hit):.0f}% of its prompt tokens cached; "
        f"{100 * statistics.fmean(uncached_req_share):.0f}% of requests sat in batches with no cached token"
    )


if __name__ == "__main__":
    main()
