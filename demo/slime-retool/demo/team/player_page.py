"""Write a self-contained HTML page that plays an asciicast, with its chapters.

Usage: python3 -I player_page.py <in.cast> <out.html> <caption> [--standalone] [--title=<page title>]

A copy of ../../tools/player_page.py with chapter navigation added. The cast is inlined
gzip-compressed and base64-encoded (the browser inflates it with DecompressionStream),
and asciinema-player 3.17.0 (the version the docs' TerminalDemo pins) loads from
jsdelivr. 3.17.0 reads asciicast v3 marker events ("m"): it draws them on the progress
bar, and [ and ] jump to the previous and next one. This page also lists the chapters
under the player: click one to jump to it (player.seek({marker: i})); the chapter
playing is highlighted. Chapter times are computed here with the player's idle-time
limit, so they match the player's clock.

The cast's embedded theme (the dark fern-code palette from apply-fern-code-theme.py)
colors the terminal. Without --standalone the page follows T3's inline-render rules: no
page background, and theme variables for the text around the player. With --standalone
it gets a doctype, a title, and its own background, so it opens in a browser.
"""

import base64
import gzip
import html
import json
import pathlib
import sys

PLAYER = "https://cdn.jsdelivr.net/npm/asciinema-player@3.17.0/dist/bundle"
IDLE_TIME_LIMIT = 1.5


def chapters(lines, idle):
    """[(seconds on the player's clock, label)] for each marker event in an asciicast v3 file."""
    out, shown = [], 0.0
    for line in lines[1:]:
        if not line.startswith("["):
            continue
        interval, code, data = json.loads(line)
        shown += min(interval, idle)
        if code == "m":
            out.append((round(shown, 2), data))
    return out


def main() -> None:
    cast_path, out_path, caption = sys.argv[1], sys.argv[2], sys.argv[3]
    standalone = "--standalone" in sys.argv[4:]
    title = next((a.split("=", 1)[1] for a in sys.argv[4:] if a.startswith("--title=")), "Slime ReTool team demo")
    cast = pathlib.Path(cast_path).read_text()
    lines = cast.splitlines()
    header = json.loads(lines[0])
    if header.get("version") != 3:
        sys.exit("player_page.py: expects asciicast v3 (asciinema 3.x); its markers become the chapters")
    term = header.get("term", {})
    cols = term.get("cols", header.get("width", 120))
    rows = term.get("rows", header.get("height", 32))
    bg = (term.get("theme") or {}).get("bg", "#0c0d0b")
    chaps = chapters(lines, IDLE_TIME_LIMIT)
    b64 = base64.b64encode(gzip.compress(cast.encode(), compresslevel=9, mtime=0)).decode()

    def fmt(s):
        return f"{int(s // 60)}:{int(s % 60):02d}"

    items = "\n".join(
        f'<li><button type="button" data-i="{i}" data-t="{t}"><span class="t">{fmt(t)}</span>'
        f"<span class=\"l\">{html.escape(label)}</span></button></li>"
        for i, (t, label) in enumerate(chaps)
    )
    page_style = (
        "body{margin:0;padding:24px;background:#f9f9f7;color:#0b0b0b;"
        "font-family:system-ui,-apple-system,'Segoe UI',sans-serif}"
        "@media (prefers-color-scheme: dark){body{background:#0d0d0d;color:#f0efec}}"
        if standalone
        else ""
    )
    doc = f"""{"<!doctype html>" if standalone else ""}
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{f"<title>{html.escape(title)}</title>" if standalone else ""}
<link rel="stylesheet" href="{PLAYER}/asciinema-player.css">
<style>
{page_style}
.demo-caption {{ font: 13px/1.4 var(--font-sans, system-ui, sans-serif); color: var(--muted-foreground, #898781); margin: 0 0 8px; }}
.demo-frame {{ border-radius: var(--radius, 10px); overflow: hidden; background: {bg}; padding: 10px 12px 4px; }}
.demo-frame .ap-player {{ border-radius: 0; }}
.demo-chapters {{ font: 13px/1.4 var(--font-sans, system-ui, sans-serif); margin: 12px 0 0; }}
.demo-chapters h2 {{ font-size: 13px; font-weight: 600; margin: 0 0 6px; }}
.demo-chapters h2 span {{ font-weight: 400; color: var(--muted-foreground, #898781); }}
.demo-chapters ol {{ list-style: none; margin: 0; padding: 0; columns: 2 280px; column-gap: 24px; }}
.demo-chapters li {{ break-inside: avoid; }}
.demo-chapters button {{ all: unset; box-sizing: border-box; cursor: pointer; display: flex; gap: 10px; width: 100%;
  padding: 3px 8px; border-left: 2px solid transparent; border-radius: 0 4px 4px 0; }}
.demo-chapters button:hover {{ background: color-mix(in srgb, currentColor 8%, transparent); }}
.demo-chapters button:focus-visible {{ outline: 2px solid #8cce37; outline-offset: 1px; }}
.demo-chapters button.current {{ border-left-color: #8cce37; font-weight: 600; }}
.demo-chapters .t {{ font-variant-numeric: tabular-nums; color: var(--muted-foreground, #898781); min-width: 3.2em; }}
</style>
</head>
<body>
<p class="demo-caption">{html.escape(caption)}</p>
<div class="demo-frame"><div id="demo"></div></div>
<nav class="demo-chapters" aria-label="Chapters">
<h2>Chapters <span>· click one to jump to it; [ and ] in the player step between them</span></h2>
<ol id="demo-chapters">
{items}
</ol>
</nav>
<script src="{PLAYER}/asciinema-player.min.js"></script>
<script>
const CAST_GZ_B64 = "{b64}";
(async () => {{
  const bytes = Uint8Array.from(atob(CAST_GZ_B64), (c) => c.charCodeAt(0));
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
  const cast = await new Response(stream).text();
  const player = AsciinemaPlayer.create({{ data: cast }}, document.getElementById("demo"), {{
    cols: {cols}, rows: {rows}, idleTimeLimit: {IDLE_TIME_LIMIT}, autoPlay: true, loop: true,
    fit: "width", controls: true, terminalFontSize: "small"
  }});
  const buttons = [...document.querySelectorAll("#demo-chapters button")];
  const times = buttons.map((b) => Number(b.dataset.t));
  let current = -1;
  const mark = (i) => {{
    if (i === current) return;
    current = i;
    buttons.forEach((b, k) => b.classList.toggle("current", k === i));
  }};
  const sync = () => {{
    const t = player.getCurrentTime();
    let i = -1;
    for (let k = 0; k < times.length; k++) if (times[k] <= t + 0.05) i = k;
    mark(i);
  }};
  player.addEventListener("marker", ({{ index }}) => mark(index));
  buttons.forEach((b, i) => b.addEventListener("click", async () => {{
    await player.seek({{ marker: i }});
    mark(i);
    player.play();
  }}));
  setInterval(sync, 500);
}})();
</script>
</body>
</html>
"""
    pathlib.Path(out_path).write_text(doc)
    print(f"{out_path}: {len(doc):,} bytes, {cols}x{rows}, {len(chaps)} chapters")


if __name__ == "__main__":
    main()
