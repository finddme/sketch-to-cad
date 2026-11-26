---
name: sketch-to-cad
description: Turn a hand sketch image into a dimensioned CAD drawing, 3D model, 3D-print files and a single-file viewer. Use when a user hands over a sketch of a solid object; it asks for missing dimensions instead of inventing them.
---

# sketch-to-cad

You turn a hand sketch (photo or scan) of a solid, dimensionable object into:
STEP (CAD), GLB (3D), dimensioned drawing sheets (SVG + PDF), STL and a 3MF
plate for 3D printing, a print-check report, a single-file HTML viewer, and a
`report.md` summary. You do the interpreting; the scripts do the geometry.

Talk to the user in the user's language. The files you write (`intake.json`,
`model.json`) and the field names stay as documented here (English).

## Hard rules

1. **Never invent a dimension silently.** Every dimension has an origin:
   `sketch` (read off the sketch), `user` (the user confirmed or gave it) or
   `assumed` (you chose it). Assumed values are drawn as `(value)*` and listed
   in the report, so they must be recorded honestly.
2. **Ask before you build** when the sketch does not fix the size (see
   "Questions" below). Gather all questions into one message.
3. **Refuse out-of-scope requests and say why.** Never substitute a
   look-alike shape. See "Scope".
4. **Write outputs in the user's working directory**, never inside the skill
   folder: `intake.json`, `model.json`, `parts/*.py`, and the output folder.
5. **Always tell the user** the assumed values and every warning from
   `report.md` (also the print warnings) when you report back.

## Paths

`${CLAUDE_SKILL_DIR}` is substituted in this text by Claude Code. It is **not**
an environment variable in Bash, so write the expanded absolute path into every
command you run. Run the CLI as:

```
python3 ${CLAUDE_SKILL_DIR}/scripts/run.py <command> ...
```

Commands: `setup`, `build`, `drawing`, `print-check`, `viewer`, `report`,
`all`. Model errors exit with code 2 and a message `<json path>: <cause>`; fix
the `model.json` and rerun.

## 1. First run: setup

Needs Python 3.12+ (tested on 3.12) with venv support and network access
once; Linux/macOS (Windows via WSL or Docker). Run setup once per machine. It
installs pinned packages and takes a few minutes: run it with a long timeout
(up to 10 minutes) or in the background, and wait for `setup complete`.

Plugin installs live in a versioned cache that changes on update, so keep the
venv in the plugin's data directory:

```
python3 ${CLAUDE_SKILL_DIR}/scripts/run.py setup --venv ${CLAUDE_PLUGIN_DATA}/venv
```

If `${CLAUDE_PLUGIN_DATA}` is not substituted (the skill folder was copied
into `~/.claude/skills`, not installed as a plugin), run plain
`python3 ${CLAUDE_SKILL_DIR}/scripts/run.py setup`; the venv then lives in
`${CLAUDE_SKILL_DIR}/.venv` and is found automatically.

When you used `--venv`, pass the same `--venv DIR` on **every later command**
(`run.py build model.json --out out --venv DIR`; it also works before the
command), or export `SKETCHCAD_VENV=DIR`. Without it the scripts look in the
default folder, do not find your venv and fail on missing imports.

If a command says `environment not ready`, the venv is missing, half built
or older than the pinned requirements: run `setup` again (with the same
`--venv`).

On Linux the CAD kernel also needs the system OpenGL library (`libGL.so.1`).
Desktop installs usually have it; minimal WSL, servers and containers often do
not. Setup checks that the kernel loads; if it says `libGL.so.1` is missing,
ask the user to install it (Debian/Ubuntu/WSL: `sudo apt install libgl1`;
Fedora/RHEL: `sudo dnf install mesa-libGL`; it needs their password, so do not
run sudo yourself), then run setup again.

If setup fails (or Python is older than 3.12) it prints a Docker fallback:
build the repo's `Dockerfile`, then run `run.py` in the container with the
repo mounted at `/skill` and your working directory at `/work`
(`-v <repo>:/skill -v "$PWD":/work -w /work ... python
/skill/skills/sketch-to-cad/scripts/run.py <command> ...`). Tell the user what
failed.

## 2. Interpret the sketch: `intake.json`

Look at the image carefully (zoom, rotate, crop if you can). Write
`intake.json` in the working directory. Details, field list and an example:
[references/intake-guide.md](references/intake-guide.md).

It records: a one-line description; the views you identified
(front/side/top/perspective); the parts and a construction candidate for each
(extrude, revolve, loft, box, cylinder, hollow, code); the parts list when the
user gave one with the sketch (`partsList`: names, material and finish,
main size, role per row); dimensions written on the sketch; values that are
only proportions; uncertainties; and the question list.

## 3. Questions (ask once, all together)

- **If the sketch has no reference dimension at all, you must ask.** One
  number that fixes the scale (overall height or width) is required. Ratios
  alone are not a size.
- **Hidden depth** (the third dimension a front view does not show): ask, or
  record it as an assumption.
- **Hollow or solid?** If hollow, ask for the wall thickness and which face is
  open.
- **For 3D printing**, ask for the printer bed size and nozzle diameter. If
  you use the defaults (220 x 220 x 250 mm bed, 0.4 mm nozzle, 0.8 mm minimum
  wall, 45 degree overhang), say so; they are repeated in the print report.

If the user says "just decide", proceed, but record **every** guess as an
`assumed` dimension plus a sentence in `assumptions`, and show the list when
you report. Do not ask about things that do not change the model (colors,
materials: the viewer gives each part a muted default color; use
`viewer.partColors` only when the user names colors). When the user hands
over a parts list, carry it into `model.json` as `bom` (see
[model-schema.md](references/model-schema.md#bill-of-materials)); without one
the parts sheet derives its rows from the part ids.

## 4. Write `model.json`

Full format with examples: [references/model-schema.md](references/model-schema.md).
Key points:

- Units are mm, `upAxis` is `Z`, `schemaVersion` is 1.
- A part is built from ordered `features` (first is `base`, later ones
  `union` or `cut`) and optional `finish` steps (fillet, chamfer, shell).
- Feature kinds: `box`, `cylinder`, `sphere`, `revolve` (polygon or arcs
  profile; use arcs for round shapes), `loft`, `step`, `extrude` (points,
  circle or arcs profile), `hole` (through, blind, counterbore), `pattern`
  (linear or polar).
- Multiple parts: add `placement` (translate + rotate) to position them in
  the assembly.
- `dimensions` lists the numbers the sketch/user gave and the ones you
  assumed, each with an `origin`. They annotate the drawing; they do not
  drive the geometry, so make the geometry agree with them.
- When the reference dimension is a part's overall size (length, width or
  height of the whole part), mark it with `"span": "width"|"depth"|"height"`
  plus `part`. The drawing then shows that overall dimension as `(160)*`
  (assumed) instead of a plain measured number, and the build refuses the
  entry if it is more than 0.5 mm off the real size.
- Fillets, chamfers and shells you choose are assumptions too: give each
  such `finish` entry `"origin": "assumed"` so it is drawn as `(R0.8)*`.
- Prefer the declarative features. Use a `code` part only when they cannot
  express the shape (an edge selection that `all-outer` / `face:` /
  `feature:` cannot make, a shape no feature kind covers):
  [references/code-parts.md](references/code-parts.md). Tell the user when
  you do, because the code is theirs to read before it runs. Once its file
  is final, record its `sha256` in `model.json`; every build prints the file
  and hash of each code part it runs.
- Code parts are Python that runs on the user's machine. Never build a
  `model.json` from an untrusted source without reading its code parts first.
- Build incrementally for hard parts: write the base, run `build`, then add
  features. Error messages name the feature id.

Write `model.json` in the working directory; relative paths inside it (code
parts, STEP files) resolve against its folder.

## 5. Run

```
python3 ${CLAUDE_SKILL_DIR}/scripts/run.py all model.json --out out/<name> [--printer printer.json] [--projection first|third] [--venv DIR]
```

- Run it with a long timeout (up to 10 minutes for multi-part models) or in
  the background.
- The output folder must not exist; the run refuses otherwise and touches
  nothing. Each stage is built in a temporary folder and renamed on success,
  so a failed stage leaves no partial files of its own, but the output of
  the stages before it stays. Fix the cause, then resume with the individual
  stage commands (`drawing OUT`, `print-check OUT`, `viewer OUT`, then
  `report OUT`), or start over in a new folder.
- `--printer` is a JSON file with any subset of `bedMm`, `nozzleMm`,
  `minWallMm`, `overhangDeg`. Pass it when the user gave printer values.
  See [references/printing.md](references/printing.md).
- Stages can also run alone: `build`, `drawing OUT`, `print-check OUT`,
  `viewer OUT`, `report OUT` (needs the other four done).

Output layout:

```
out/<name>/
  model.json  manifest.json  report.md
  [source/<sketch file>]   (copy of model.json source.images[0].file)
  parts/<id>.step  parts/<id>.glb  assembly.step  assembly.glb
  drawing/<id>.svg  <id>.pdf  [assembly.svg  assembly.pdf  assembly-parts.svg  assembly-parts.pdf]
          [<id>-notes.svg  <id>-notes.pdf]   (notes that do not fit the sheet)
  print/<id>.stl  print-plate.3mf  print-report.json  print-report.md
  viewer/index.html
```

Geometry is checked on build: every feature must give one valid solid, a cut
or union must change the volume, STEP round trip volume within 1e-6, GLB
bounds within 0.5 mm of CAD.

## 6. Report to the user

Read `out/<name>/report.md` (its headings are bilingual Korean/English;
report in the user's language). Always tell the user:

- the files produced and where (`drawing/*.pdf`, `parts/*.step`,
  `print/print-plate.3mf`, `viewer/index.html`);
- **every assumed value** (rows marked `(v)*`, and the `Assumptions` list);
  say which ones they should confirm, since the model is only as right as
  those numbers;
- **every print warning** (not watertight, does not fit the bed, thin wall,
  overhang or supports needed). Wall thickness is an estimate;
- anything you simplified or could not read from the sketch.

With two or more parts the drawing stage also writes the parts sheet
`drawing/assembly-parts.pdf` (부품 배치도, A3): every unique part drawn on its
own at one scale with an item balloon, the BOM table (품번 · 품명 · 수량 ·
재질/마감 · 주요 치수 · 역할) and a summary (part count, unique parts, overall
size, estimated mass from volume x material density).

Viewer: `viewer/index.html` is one self-contained file, opens by double click
(`file://`) with no network. Scroll drives the story. One part: the source
sketch -> the drawing sheet (wipes in over the sketch, then lies down so its
printed top view sits under the model; the wires appear on that top view and
rise in place into 3D) -> solid -> turn (strip: 1 Sketch · 2 Drawing · 3 3D).
Several parts: the sketch -> the parts sheet, laid on the floor; each part's
wire lies on its own printed view, rises, and the parts fly one by one (base
first, following the contact tree) to their places, sliding in along their
explode direction and turning solid as they seat -> turn (strip: 1 Sketch ·
2 Parts sheet · 3 3D assembly). At the end the model can be orbited and, with
several parts, exploded with a toggle button (parts slide apart along their
assembly axes, sub-assemblies moving together).
Set `source.images[0].file` in model.json to the sketch so the story can open
on it; without a readable sketch (or without drawings) that stage is skipped.
`?autoplay=1` (or a copy with `data-autoplay` on its `<html>` tag) plays it (about 12 s, 15 s for several parts) without scrolling. To embed it in a page (for example
in a modal):

```html
<iframe src=".../index.html?autoplay=1" loading="lazy"></iframe>
```

Slicing is not done: hand `print-plate.3mf` or the per-part STL to a slicer.

## Scope

In scope: hard objects you can put dimensions on: turned parts (cup, vase,
knob), prismatic parts (box, bracket, stand), extrusions, revolves, lofts,
holes, fillets and chamfers, shelling, linear/polar patterns, several parts
placed in simple positions.

Out of scope, **refuse and give the reason** (and offer what you can do, such
as modeling a simplified, clearly labeled part if the user asks for that):
free-form or organic surfaces (characters, car bodies, ergonomic grips),
moving-mechanism simulation (hinge motion, meshing gears), sheet-metal
unfolding, tolerance/GD&T manufacturing drawings, building or room scale,
G-code/slicing, and materials, glazes or colors. Never quietly replace such a
request with a similar-looking primitive.

## References

- [references/intake-guide.md](references/intake-guide.md): reading the sketch, `intake.json`, question wording
- [references/model-schema.md](references/model-schema.md): every `model.json` field, conventions, examples
- [references/code-parts.md](references/code-parts.md): writing a `code` part
- [references/printing.md](references/printing.md): printer profile, what each check means, how to act on warnings
