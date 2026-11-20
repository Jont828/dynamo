"""Write a self-contained HTML page that plays an asciicast.

Usage: python3 -I player_page.py <in.cast> <out.html> <caption> [--standalone] [--title=<page title>]

The cast is inlined gzip-compressed and base64-encoded (the browser inflates
it with DecompressionStream), and asciinema-player 3.17.0 (the version that the docs'
TerminalDemo pins) loads from jsdelivr. The cast's embedded theme (the dark
fern-code palette from apply-fern-code-theme.py) colors the terminal. Without
--standalone the page follows T3's inline-render rules: no page background,
and theme variables for the text around the player. With --standalone it gets
a doctype, a title, and its own background, so it opens in a browser.
"""

import base64
import gzip
import html
import json
import pathlib
import sys

PLAYER = "https://cdn.jsdelivr.net/npm/asciinema-player@3.17.0/dist/bundle"


def main() -> None:
    cast_path, out_path, caption = sys.argv[1], sys.argv[2], sys.argv[3]
    standalone = "--standalone" in sys.argv[4:]
    title = next((a.split("=", 1)[1] for a in sys.argv[4:] if a.startswith("--title=")), "Slime ReTool demo")
    cast = pathlib.Path(cast_path).read_text()
    header = json.loads(cast.splitlines()[0])
    term = header.get("term", {})
    cols = term.get("cols", header.get("width", 100))
    rows = term.get("rows", header.get("height", 25))
    bg = (term.get("theme") or {}).get("bg", "#0c0d0b")
    b64 = base64.b64encode(gzip.compress(cast.encode(), compresslevel=9, mtime=0)).decode()
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
</style>
</head>
<body>
<p class="demo-caption">{html.escape(caption)}</p>
<div class="demo-frame"><div id="demo"></div></div>
<script src="{PLAYER}/asciinema-player.min.js"></script>
<script>
const CAST_GZ_B64 = "{b64}";
(async () => {{
  const bytes = Uint8Array.from(atob(CAST_GZ_B64), (c) => c.charCodeAt(0));
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
  const cast = await new Response(stream).text();
  AsciinemaPlayer.create({{ data: cast }}, document.getElementById("demo"), {{
    cols: {cols}, rows: {rows}, idleTimeLimit: 1.5, autoPlay: true, loop: true,
    fit: "width", controls: true, terminalFontSize: "small"
  }});
}})();
</script>
</body>
</html>
"""
    pathlib.Path(out_path).write_text(doc)
    print(f"{out_path}: {len(doc):,} bytes, {cols}x{rows}")


if __name__ == "__main__":
    main()
