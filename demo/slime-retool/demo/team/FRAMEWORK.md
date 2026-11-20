Status: READY

# Team demo framework: the API for beat authors

READY since 2026-11-18 22:00 (local). lib.sh, run.sh, build.sh, check.py, frames.py, and the vim
emulator are tested on `beats/99_frameworktest.sh` (look at it for a working example of every
helper). Since 22:15, record.sh (with chapter markers) and the team player page work too; see
[Record and share](#record-and-share). Changes after READY are listed in
[Changelog since READY](#changelog-since-ready).

`$PY` below is any Python 3.10+ with PyYAML; the scripts default to `PY=python3`.
Run every command from `demo/team/`.

## Quick start

1. Write `fill/NN_slug.py` (template in [Fill scripts](#fill-scripts)). It writes
   `build/NN_slug.data.sh`. Every variable starts with your slug in capitals: `STORAGE_*` for
   `03_storage`.
2. Write `beats/NN_slug.sh`. It defines `beat_NN` and nothing else at top level except functions named
   `slug_*` (and, in a few beats, a `SLUG_TITLE` variable). The function starts with
   `beat_header EMOJI "Title" [SUBTITLE...]`: the chapter marker, a clean screen, and the header.
3. `./run.sh NN`: runs your beat (and reruns your fill script if it changed). `FAST=1 ./run.sh NN`
   skips every pause.
4. `$PY -I check.py NN`: duration at real timing, widest line, every line over 120 columns, and lint.
5. Look at it: `$PY -I check.py NN --cast tmp/NN.cast`, then
   `$PY -I frames.py tmp/NN.cast tmp/fNN --when 'some text@1' --markers`, then Read the PNGs.

Name drafts anything but `NN_slug.sh` (for example `NN_slug.sh.draft`): run.sh and build.sh only
pick up `beats/[0-9][0-9]_*.sh`. A beat or data file that doesn't parse stops `run.sh` only when that
beat is selected; otherwise it's skipped with a warning.

## Conventions

- **Names.** Data variables: `SLUG_*` (fill scripts enforce it). Helpers in a beat file: `slug_*`.
  Locals: `local`. Don't redefine lib.sh names.
- **Time.** Use `nap SECONDS` (or a helper that naps), never `sleep`: `FAST=1` skips naps,
  `DRYRUN=1` counts them, and long naps keep the recording alive past idle-time limits.
- **Printing.** Palette variables hold real escape characters. Print with `printf '%s\n' "..."` or
  `el`, never `echo -e` or `printf '%b'` (they turn `\boxed` into a backspace). Never write `'\033['`
  yourself; use the palette variables.
- **Width.** Every line fits in 120 columns: narration lines in 118 (after `# `). Wide characters
  (emoji, ⏩, ✅) count 2 columns; box drawing, ▶, ✓, and → count 1. Don't use emoji with U+FE0F
  (⚠️): players disagree on their width. Use `narrate_wrap` or `card` for long text; they wrap for you.
- **Narrate before every distinct command** (BRIEF §8), in one plain line.
- **Real data only.** Every number and output line comes from `build/NN_slug.data.sh`, which the fill
  script reads from the kit. Lines typed in vim must be real lines of the file: the fill script calls
  `check_typed()`.
- **Deterministic.** No `$RANDOM`, no `date`, no reading files at run time: the built script is
  standalone.
- **Each beat starts with `beat_header EMOJI "Title" [SUBTITLE...]`.** It calls `chapter "Title"` (the
  chapter in the player, named like the header), clears the screen, and pins the header to the top row,
  so a viewer who jumps to a chapter lands on a clean screen. Titles are Title Case; steps are
  "Step N: ..." (1 cluster, 2 storage, 3 serving, 4 Slime code, 5 RayJob, 6 loop); the problem,
  trajectory, comparison, and closing beats aren't numbered.
- **bash 3.2** (macOS `/bin/bash`), with `set -euo pipefail`. See [Pitfalls](#pitfalls-bash-32-set--eu).

## The files

| File | What it does |
|---|---|
| `lib.sh` | every helper below; sourced by run.sh, concatenated into the built script |
| `run.sh [NN ...]` | runs beats (all but 99 by default, in NN order). Refills stale data first (`REFILL=1` forces it) |
| `build.sh [-o OUT] [NN ...]` | runs the fill scripts (stops at the first failure and names it), then writes the standalone `slime-retool-team-demo.sh` (or `tmp/build-NN.sh` for a subset) |
| `check.py [NN ...] [--cast F] [--script F] [--json F]` | dry run: per-beat duration, widest line, lines > 120, chapters, lint |
| `frames.py CAST OUTDIR [--at S] [--when TEXT[#N][@DELAY]] [--markers]` | renders a cast with agg and extracts PNG frames |
| `record.sh [-o OUT] [--no-build] [--gif] [--mp4] [NN ...]` | records 120x32 with asciinema (real time), turns chapter tokens into markers, embeds the fern-code theme |
| `cast_markers.py IN OUT`, `cast_markers.py --list CAST` | record.sh's marker step; `--list` prints a cast's chapters and times |
| `player_page.py CAST OUT.html CAPTION [--standalone]` | a page that plays the cast, with a clickable chapter list |
| `fill/_common.py` | helpers for fill scripts: `Data`, `check_typed`, `manifest_lines`, ... |
| `beats/99_frameworktest.sh`, `fill/99_frameworktest.py` | the framework's test beat: an example of every helper. Runs only when named |

## Knobs

All are environment variables with these defaults (seconds unless noted), set at the top of lib.sh.
Use them in beats rather than literal numbers where one fits.

| Knob | Default | Used by |
|---|---|---|
| `TIME_SCALE` | 100 | percent applied to every nap (the integrator's global speed control) |
| `NAP_CATCHUP` | 1 | when the wall clock falls behind the planned time, later naps give some back (see `nap`) |
| `NAR_LEAD` | 0.3 | pause before each narration line |
| `NAR_WPM` | 240 | reading speed for narration, cards, and header subtitles (they stay on screen) |
| `CALLOUT_WPM` | 220 | reading speed for vim callouts, which vanish when done |
| `NAR_FLOOR` | 1.0 | shortest hold after a narration line |
| `NAR_W` | 116 | `narrate_wrap` width |
| `HEADER_HOLD` | 0.8 | after `step_header` (plus its subtitles' reading time) |
| `CARD_LEAD`, `CARD_FLOOR` | 0.4, 1.5 | before a card; shortest hold per card paragraph |
| `CARD_W`, `CARD_INDENT` | 114, 2 | card width (borders included) and left margin |
| `RULE_W` | 100 | step header rule width |
| `CHAR_SPEED` | 0.011 | per character of a typed shell command |
| `TYPE_GROUP` | 4 | shell typing: characters drawn per nap (nap = `CHAR_SPEED` × 4: same speed, fewer sleeps) |
| `PROMPT_PAUSE`, `CMD_PAUSE` | 0.5, 0.6 | before typing a command; after it |
| `PAUSE_SHORT`, `PAUSE_MED`, `PAUSE_LONG` | 1.0, 1.2, 1.8 | general pauses (v1's) |
| `FF_TICK` | 0.04 | per `ff_run` frame |
| `VIM_CHAR` | 0.03 | per character at normal typing speed |
| `VIM_GROUP` | 2 | vim typing: ticks drawn per nap (nap = `VIM_CHAR` × 2: same speed, fewer sleeps) |
| `VIM_FAST` | 8 | characters per tick at fast speed ("⏩ typing ×8") |
| `VIM_COLLAPSE_X` | 40 | characters per tick in `vim_collapse` |
| `VIM_EOL_PAUSE` | 0.15 | before each Enter at normal speed |
| `VIM_OPEN_HOLD`, `VIM_KEY_PAUSE`, `VIM_KEY_CHAR` | 1.0, 0.5, 0.12 | on the empty buffer; around i/Esc/Enter; per `:wq` key |
| `VIM_WRITTEN_HOLD` | 1.0 | on `"FILE" [New] NL, NB written` |
| `VIM_FOLD`, `VIM_FOLD_PAUSE` | 1, 0.5 | `vim_collapse` folds afterwards (0: keeps the lines unfolded) |
| `VIM_RULER_LIVE` | 0 | 1: the ruler's column follows every keystroke. Leave it 0: then each typed line reaches the cast unbroken, which `tools/redact_cast.py` needs to mask the IPs in it |
| `CALLOUT_LEAD`, `CALLOUT_FLOOR` | 0.3, 2.5 | before a callout; its shortest hold |
| `NAP_CHUNK` | 1 | longest silent sleep before an invisible keepalive |
| `COLS`, `ROWS` | 120, 32 | screen size (don't change) |

Reading holds: `max(FLOOR, words × 60 / WPM)`, where words are runs of non-blanks (padding and double
spaces don't count). At 240 wpm a 15-word line holds 3.75 s; a callout uses `CALLOUT_WPM`.

## API

Signatures use `ARG...` for one or more arguments. "Holds" means the helper naps for reading time.

### Time

- `nap SECONDS`: the only sleep. `nap "$PAUSE_SHORT"`. With `NAP_CATCHUP=1` (the default) it adds up
  the requested time; when `$SECONDS` shows the run more than 2 s behind it, the next naps give time back
  (a hold of 0.2 s or more up to 40% of itself, a typing or animation tick up to 60%). Each nap forks
  `sleep`, so a loaded machine runs late without it: the results agent's take ran 25% long at a load
  average of 22. With it, beat 08 recorded +2.3% at a load average of 16-20.
- `pause [SECONDS]` (default 2), `hold SECONDS`: v1 names for `nap`.
- `read_hold FLOOR TEXT...`: nap for TEXT's reading time at `NAR_WPM`, at least FLOOR. Use after
  printing something the viewer must read (real output, a table): `read_hold 2 "$STORAGE_SUMMARY"`.
- `read_hold_at WPM FLOOR TEXT...`: the same at another pace (`vim_callout` uses `CALLOUT_WPM`).

### Narration, headers, cards

- `chapter "Title"`: an invisible marker; record.sh turns it into a chapter in the player.
  `chapter "Step 2: Stage the model and data on Azure Managed Lustre"`.
- `step_header EMOJI TITLE [SUBTITLE...]`: two blank lines, ━ rules, the bold title, optional
  subtitle lines (narration color), then a hold. `step_header "📦" "Step 2: Stage the model and data" "Azure Managed Lustre, mounted by every Pod"`.
- `step_header_bars EMOJI TITLE [SUBTITLE...]`: the same block with no blank lines before and no
  hold, to re-pin a header after `clear_screen`.
- `beat_header EMOJI TITLE [SUBTITLE...]`: `chapter TITLE`, `clear_screen`, `step_header_bars`, then
  step_header's hold. Every beat opens with it.
- `narrate LINE...`: each line as `# LINE` in the narration color, after `NAR_LEAD`, held for its
  reading time. Inline color: `narrate "${NUM}${BOLD}8 GPUs${NC}${NAR} on one node"` (re-apply
  `${NAR}` after `${NC}`).
- `narrate_wrap PARAGRAPH...`: word-wraps each paragraph to `NAR_W` columns, then narrates the lines.
- `card TITLE PARAGRAPH...`: a boxed explainer (`CARD_W` wide). Each paragraph is word-wrapped and
  appears after the previous one's reading time; `""` is a blank line. Inline colors work (no need
  to re-apply anything after `${NC}`).
  ```bash
  card "🧮 ReTool in one turn" \
       "The model writes Python inside <tool_call>...</tool_call> and stops." \
       "" \
       "A sandbox runs it and appends the output: ${NUM}3162.2776601683795${NC}."
  ```
- `stream_lines DELAY LINE...`: prints lines DELAY seconds apart, like a followed log.
- `clear_screen`: clears the screen and scrollback (a hard cut).

### Shell commands (v1's)

- `show_command 'CMD' [COMMENT]`: prompt `❯`, pause, types the highlighted command, pause.
  Single-quote it so `$NAMESPACE` stays literal: `show_command 'kubectl get pods -n $NAMESPACE'`.
- `show_command_lines 'LINE1 \' '    LINE2'`: a command typed over several lines.
- `print_command 'CMD' [COMMENT]`: the same prompt line, printed at once (to re-pin after a cut).
- Output: `printf '%s\n' "$SLUG_OUT"` (then `nap`/`read_hold`), or `stream_lines`.

### Fast-forwarded waits and in-place redraws

- `ff_run RENDER FROM TO [TICK] [STEP]`: calls `RENDER t` for t = FROM, FROM+STEP, ..., TO and draws
  each frame in place, TICK apart (default `FF_TICK`). RENDER adds lines with `fl`; the frame may
  change height. Only changed lines are rewritten. RENDER may set `FF_TICK_NOW` to change the pause
  after its frame (for example, slower around an event).
- `fl LINE`: add a line to the frame being rendered.
- `ff_elapsed SECONDS`: add v1's `  ⏩ fast-forwarding · 1m49s elapsed` line.
- `frame_replace LINE...`: replace the last frame with these lines (the wait's final output) and
  clear the rest of it.
  ```bash
  storage_render() {  # t: seconds since the Job started
      local t="$1"
      if (( t < STORAGE_DL_T )); then fl "   ${STATUS}▸ Downloading...${NC}"; else fl "   ${STATUS}▸ Converting...${NC}"; fi
      ff_elapsed "$t"
  }
  show_command 'kubectl wait -n $NAMESPACE --for=condition=complete job/slime-retool-setup'
  ff_run storage_render 0 "$STORAGE_SETUP_SECS"
  frame_replace "job.batch/slime-retool-setup condition met"
  ```
- `frame_draw`, `frame_reset`, `FRAME=()`: the parts of `ff_run`, for your own loops.
- v1's low-level pair: `move_up N` and `el LINE` (erase the line, print LINE and a newline).
- `fmt_age SECONDS` → `AGE_STR` (kubectl's AGE format: `109s`, `29m14s`).
- `padr VAR WIDTH TEXT`: TEXT padded to WIDTH display columns, escapes and wide characters counted
  right (bash's `printf %-10s` pads by bytes). `padr c1 28 "$text"; el "  ${c1}${next}"`.
- `dwidth TEXT` → `_W`: display width, escapes ignored.

### Syntax highlighting

`hl_line LANG TEXT` (LANG: `yaml`, `python`, `bash`, `text`) fills `SEG_TXT`/`SEG_COL`;
`print_segments` prints them, `type_segments [CHUNK]` types them. `hl_reset LANG` clears the state
that carries across lines (YAML block scalars, Python parentheses and triple quotes, bash quotes).
`hl_bash`, `hl_python`, and `hl_yaml` are the per-language functions (v1's, plus `hl_yaml`, and
comments, numbers, and multi-line state in `hl_python`). The vim emulator calls them for you.

### Vim emulator

Opens on the alternate screen, so `vim_close` brings back the shell screen exactly as it was.
Layout: 30 text rows, a reverse-video status line (file name, `[+]`, vim's ruler `line,col` and
`All`/`Top`/`Bot`/`NN%`), and the command line (`-- INSERT --`, messages). Each row has a 2-column
sign column and a 4-column line number, so text is 114 columns wide; longer lines soft-wrap.

- `vim_open FILE [LANG]`: types `vim FILE` at the prompt, shows the empty buffer (`~` rows,
  `"FILE" [New]`), then `-- INSERT --`. LANG defaults from the extension (`.yaml`, `.py`, `.sh`).
  Pass the path as the viewer should see it: `vim_open manifests/storage.yaml`.
- `vim_type normal LINE...`: types character by character (`VIM_CHAR` each). Leading spaces
  appear at once, like autoindent.
- `vim_type fast LINE...`: `VIM_FAST` characters per tick, with `⏩ typing ×8` on the command line.
- `vim_collapse NOTE LINE...`: types the lines at `VIM_COLLAPSE_X` per tick with
  `⏩ typing ×40 · NOTE` on the command line, then folds them into one row,
  `+-- 180 lines: NOTE -----`. The buffer still holds every line (the written size is right).
  `vim_collapse "worker-1..3: same as worker-0" "${SERVE_DGD_LINES[@]:94:180}"`.
- `vim_collapse_begin NOTE`, `vim_type collapse LINE...`, `vim_collapse_end`: the same in parts, so
  `vim_fold_in` can add lines in between; everything added in between becomes the one fold.
- `vim_fold_in NOTE LINE...` (2 lines or more): adds the lines as a closed fold, `+--  2 lines: NOTE`,
  without drawing their text. The buffer and the written size stay the real file, but the text never
  reaches the cast. Beat 04 uses it for each engine's `--host 0.0.0.0` and its sidecar's `127.0.0.1`
  endpoint: not secrets, but `tools/redact_cast.py` flags every IPv4 address it can't mask, and the
  public cast must pass it with no remaining matches.
- `vim_callout FROM TO PARAGRAPH...`: pauses and explains. Marks buffer lines FROM..TO (1-based)
  with ▶ and bold numbers, scrolls if needed so they're visible above the box, shows the paragraphs
  (word-wrapped, inline colors allowed) in a box above the status line for their reading time, then
  removes the box and returns to where typing continues. The buffer doesn't change. A folded line
  marks the fold row. Box height: 2 + the wrapped lines; keep it to 2–5 lines so the marked lines fit.
- `vim_note PARAGRAPH...`: a callout that marks no lines.
- `vim_find TEXT` → `VIM_FOUND`: the 1-based number of the first buffer line containing TEXT (0 if
  none). `vim_find 'driver: azurelustre'; vim_callout "$VIM_FOUND" "$VIM_FOUND" "..."`.
- `VIM_N`: the number of lines typed so far: `vim_callout $(( VIM_N - 2 )) "$VIM_N" "..."`.
- `vim_close`: Esc, types `:wq`, shows `"FILE" [New] 42L, 869B written`, and returns to the shell
  screen. The next `show_command` prints the next prompt.

Line numbers in callouts are buffer numbers. If the fill script drops the file's header comment,
they differ from the file's own numbers: compute them in the fill script with `find_line()` on the
same list you type, or use `vim_find` at run time.

```bash
storage_vim() {
    local -a L=("${STORAGE_YAML[@]}")
    narrate "Write the storage manifest:"
    vim_open manifests/storage.yaml
    vim_type normal "${L[@]:0:5}"                       # the Namespace, at typing speed
    vim_type fast "${L[@]:5:23}"                        # the PV, fast
    vim_callout "$STORAGE_CSI_FROM" "$STORAGE_CSI_TO" \
        "The csi block points the PV at ${BOLD}Azure Managed Lustre${NC}: ..."
    vim_collapse "PersistentVolumeClaim slime-data" "${L[@]:28}"
    vim_close
}
```

### Palette (v1's roles)

`RULE PROP STR CMD FUNC DECL OP NAR DIM NUM ARG PARAM CALLARG QUOTE PUNCT PROMPT BOLD NC`, plus
`OK` (= STR, success marks) and `STATUS` (= NUM, in-progress status). Use `NUM`+`BOLD` for numbers
called out in narration, `OK` for ✓/✅, `DIM` for progress notes, `NAR` for comments. Each variable
holds a real escape sequence that resets the attributes first. v1's table formats `CRD_FMT`,
`POD_FMT`, `DGD_FMT` are defined too.

## Fill scripts

`fill/NN_slug.py` runs as `$PY -I fill/NN_slug.py` (build.sh and run.sh run it for you) and writes
`build/NN_slug.data.sh` through `fill/_common.py`:

```python
"""Data for beats/03_storage.sh: ... (list the kit files it reads)."""
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))   # -I leaves it off sys.path
from _common import RUNS, Data, check_typed, find_line, manifest_lines, secs, load_yaml

d = Data(__file__)                         # checks the file name; prefix STORAGE_
d.source("manifests/storage.yaml", "runs/setup/storage-apply.txt")
lines = manifest_lines("manifests/storage.yaml")   # without the kit-internal header comment
check_typed(lines, "manifests/storage.yaml")       # every typed line is real, in order
d.array("STORAGE_YAML", lines)
d.set("STORAGE_CSI_FROM", find_line(lines, "  csi:"))     # a 1-based buffer line number
d.set("STORAGE_APPLY_OUT", (RUNS / "setup" / "storage-apply.txt").read_text().rstrip("\n"))
d.write()
```

`_common.py`:

| Name | What it does |
|---|---|
| `Data(__file__)` | `.set(NAME, value)` (int → bare, usable in `(( ))`; str → quoted), `.array(NAME, values)`, `.source(paths...)`, `.write()` (atomic). Names must start with `SLUG_` |
| `q(value)`, `arr(values)` | v1's bash quoting |
| `KIT`, `RUNS`, `MANIFESTS`, `FUNCTIONS`, `TEAM`, `BUILD` | paths |
| `kit_path(rel)`, `read_lines(rel)`, `load_yaml(rel)`, `load_json(rel)` | read kit files (paths relative to the kit root) |
| `manifest_lines(rel, header=False)` | a file's lines without its leading comment block |
| `strip_header(lines)` | the same, on a list |
| `check_typed(lines, rel)` | exits with a message unless every line is a real line of the file, in order (lines may be skipped, not changed) |
| `check_in_file(text, rel)` | exits unless text appears verbatim in the file |
| `find_line(lines, needle, start=1)` | 1-based number of the first line containing needle |
| `fmt_int(n)`, `secs(iso_a, iso_b)`, `iso(s)` | `17,398`; whole seconds between ISO times |
| `max_width(lines, limit=120)` | exits if a plain line is wider than limit |

A fill script that fails stops build.sh with its name. Fill scripts must be deterministic.

## Test a beat

```bash
./run.sh 03                                   # watch it in your terminal (120x32 or larger)
FAST=1 ./run.sh 03 > /dev/null                # does it run to the end?
$PY -I check.py 03                            # durations, widths, lint; exit 1 if a line > 120
$PY -I check.py 03 04 05                      # several beats, in this order
$PY -I check.py 03 --cast tmp/03.cast         # + a synthetic cast at the requested timing
$PY -I frames.py tmp/03.cast tmp/f03 --markers --when 'condition met@1' --when 'written#2@0.2' --at 42
```

`check.py` columns: `duration` is the sum of requested naps at real timing; `~wall` adds 9 ms per nap
(measured: beat 99 recorded in 1:52 against 1:44 requested), so it's closer to a real recording;
`widest` is the widest drawn line, with
in-place redraws, the vim screen, and wide characters accounted for; `>120` counts lines that would
wrap. It also lists chapter start times and lint warnings (bash 4 features, `sleep`, `echo -e`,
`$name` before a non-ASCII character, literal `\033[`), and warns when a `10.x.x.x` address is split
by escape sequences in the output (tools/redact_cast.py couldn't mask it).

`frames.py` renders with agg (`--font-size 16 --fps-cap 15 --idle-time-limit 2`) and writes one PNG
per `--at SECONDS`, `--when TEXT[#N][@DELAY]` (DELAY seconds after the Nth time TEXT is drawn;
default 0.3 s), or marker (`--markers`, 1 s after each chapter starts). Read the PNGs to check
alignment, colors, and wrapping. The synthetic cast skips real time, so it takes seconds.

A full dry run of the built script: `$PY -I check.py` (runs build.sh first).

## Record and share

```bash
./record.sh 03 04                    # real-time recording of run.sh 03 04 -> tmp/rec-03-04.cast
./record.sh --gif --mp4              # build.sh, then the whole replay -> slime-retool-team-demo.{cast,gif,mp4}
$PY -I cast_markers.py --list slime-retool-team-demo.cast        # its chapters and times
$PY -I ../../tools/redact_cast.py slime-retool-team-demo.cast slime-retool-team-demo.public.cast
$PY -I player_page.py slime-retool-team-demo.public.cast slime-retool-team-demo.standalone.html \
    "Slime ReTool + Dynamo on AKS" --standalone --title="Slime ReTool team demo"
```

- record.sh runs `asciinema rec --headless` (when there's no TTY) at `--window-size 120x32`
  with `--idle-time-limit 2`, from `demo/team/`, with `FAST` and `DRYRUN` unset. asciinema 3.2 writes
  **asciicast v3**: each event is `[seconds since the previous event, code, data]`.
- `cast_markers.py` removes every in-band token and puts a marker event `[interval, "m", "Title"]`
  where each `chapter` was (tokens split across two output events are handled; timing is
  unchanged). Then `../apply-fern-code-theme.py` embeds the dark fern-code palette; it only sets
  `term.theme`, so 120x32 is kept.
- agg ignores markers and renders the cast (`--gif`); `--mp4` converts the GIF with ffmpeg.
- asciinema-player **3.17.0** (pinned by the docs' TerminalDemo and both player pages) reads v3
  markers: it draws them on the progress bar, `[` and `]` jump to the previous and next one, and its
  API has `seek({marker: i})` and a `marker` event. `player_page.py` adds a chapter list under the
  player: click a chapter to jump there; the chapter playing is highlighted.
- Before sharing, `tools/redact_cast.py` masks `10.x.x.x` addresses and AKS node-pool IDs and flags
  GUIDs and emails; it must report "no remaining matches". It works on the joined output stream, so a
  typed IP must not be split by escapes (see `VIM_RULER_LIVE`; check.py warns if one is).
- Long holds stay long: nap emits an invisible keepalive (the cursor's visibility, re-sent) at least
  every second, so the recorder's and players' idle-time limits (2 s, 1.5 s) don't shorten them.

## Pitfalls (bash 3.2, set -eu)

- **An empty array under `set -u`** is an error: `"${A[@]}"` → write `${A[@]+"${A[@]}"}`.
- **`$name` before a non-ASCII character** (`"$a–$b"`, `"$n×"`): bash 3.2 reads the first byte of
  the character into the name. Always write `${a}–${b}`.
- **A function's last command decides its status.** If the last line is `[[ ... ]] && x` and the test
  fails, the function returns 1 and `set -e` ends the demo. End with an `if ... fi` or `return 0`.
- **`printf '%-20s'` pads by bytes**, so `→` or `×` misaligns tables: use `padr`.
- **No** associative arrays, `${x,,}`, `mapfile`, `readarray`, `[[ -v ]]`, `declare -n`, `&>>`.
- **Don't `clear_screen` inside vim**; `vim_open`/`vim_close` own the alternate screen. The shell
  helpers refuse to run while vim is open, and `narrate` turns into a `vim_note` callout.
- **A redraw taller than the screen** (32 rows) can't move back up: keep `ff_run` frames short.

## Changelog since READY

None of these changes an API that beats call.

- 22:15 record.sh, cast_markers.py, and player_page.py work: tested on a real recording of beat 99
  (1:52, 2 markers, rendered by agg, redacted cleanly).
- 22:15 vim: the ruler now updates after each Enter and when a typed line is finished, not on every
  keystroke (`VIM_RULER_LIVE=1` brings that back). Per-keystroke updates put ruler text between the
  characters of a typed `10.225.0.10`, and redact_cast.py left part of it unmasked.
- 22:15 check.py: `~wall` adds 9 ms per nap (measured; it was 4 ms); a warning for IPs split in
  the output; no warning for tabs (real `du` output has them, and they render fine).
- 22:20 While vim is open, `narrate` shows its lines as a `vim_note` callout instead of printing
  over the vim screen, and `show_command`, `show_command_lines`, `step_header`, `card`, and
  `clear_screen` stop the demo with "vim is open (call vim_close first)".
- 23:00 Integration pass (the integration agent): `beat_header` (every beat opens on a clean screen,
  its chapter named like its header, Title Case); `read_hold` counts words, not spaces; `read_hold_at`
  and `CALLOUT_WPM`; `TYPE_GROUP` and `VIM_GROUP` (several characters per nap, same speed: about 1,860
  naps instead of 4,100); `NAP_CATCHUP`. Pace: `NAR_WPM` 240, `CALLOUT_WPM` 220, `NAR_LEAD` 0.3,
  `NAR_FLOOR` 1.0, `PROMPT_PAUSE` 0.5, `CMD_PAUSE` 0.6. check.py's `~wall` (9 ms per nap) now estimates a
  recording with `NAP_CATCHUP=0`; with catch-up on, a recording lands within a few percent of `duration`.
- 23:10 `vim_fold_in`, `vim_collapse_begin`/`vim_collapse_end`, and `vim_type collapse` (see the vim
  section): beat 04 folds in the four engines' `0.0.0.0` and `127.0.0.1` lines, which the redaction
  check would otherwise flag.
- 2026-11-19 Beat 09 shows the comparison as terminal charts, three screens of real
  `../../tools/compare_bars.py` output (24-bit color; `fill/09_compare.py` checks every value, bar, and
  column) instead of compare_runs.py's table. The chapter now runs 1:34, and the replay 12:26.
