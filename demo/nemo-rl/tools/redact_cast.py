#!/usr/bin/env python3
"""Make a shareable copy of an asciicast: mask in-cluster IPs and the AKS node-pool ID.

Replacements keep the same length, so tables stay aligned; timing and event count are unchanged.
Typed commands arrive one character per event, so the masking runs on the joined output stream.

Usage: python3 redact_cast.py IN.cast OUT.cast
"""

import json
import re
import sys

# No \b: after an ANSI color code ("\x1b[2m10.244…") there is no word boundary before the IP.
IP_RE = re.compile(r"(?<![\d.])10\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\d.]*\d)")
POOL_RE = re.compile(r"(aks-[a-z0-9]+-)(\d{8})(-vmss)")
CHECKS = {
    "IPv4 address": r"(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d.]*\d)",
    "AKS node-pool ID": r"aks-[a-z0-9]+-\d{8}-",
    "Hugging Face token": r"hf_[A-Za-z0-9]{20,}",
    "GitHub token": r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_",
    "API key": r"sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}",
    "private key": r"BEGIN [A-Z ]*PRIVATE KEY",
    # Kubernetes object UIDs in PV names (pvc-<uid>) are random and not sensitive.
    "GUID": r"(?<!pvc-)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    "email": r"[\w.+-]+@[\w-]+\.[\w.-]+",
    "home directory": r"/Users/\w+|/home/\w+",
}


def mask_ip(match):
    return "10." + ".".join("x" * len(part) for part in match.groups())


def main():
    src, dst = sys.argv[1], sys.argv[2]
    with open(src, encoding="utf-8") as fh:
        header = fh.readline()
        events = [json.loads(line) for line in fh if line.startswith("[")]
    joined = "".join(e[2] for e in events)
    masked = POOL_RE.sub(lambda m: m[1] + "X" * len(m[2]) + m[3], IP_RE.sub(mask_ip, joined))
    assert len(masked) == len(joined)
    pos = 0
    with open(dst, "w", encoding="utf-8") as fh:
        fh.write(header if header.endswith("\n") else header + "\n")
        for interval, code, data in events:
            fh.write(json.dumps([interval, code, masked[pos:pos + len(data)]], ensure_ascii=False) + "\n")
            pos += len(data)

    plain = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", masked)
    env = json.loads(header).get("env", {})
    print(f"{dst}: {len(events)} events; header env: {env}")
    leftover = False
    for name, pattern in CHECKS.items():
        hits = sorted(set(re.findall(pattern, plain)))
        if hits:
            leftover = True
            print(f"  REVIEW {name}: {hits[:5]}")
    print("  no remaining matches" if not leftover else "  review the matches above before sharing")
    sys.exit(1 if leftover else 0)


if __name__ == "__main__":
    main()
