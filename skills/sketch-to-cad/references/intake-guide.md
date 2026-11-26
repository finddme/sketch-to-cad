# Intake guide: reading the sketch and asking questions

Goal: decide what the object is, what the sketch really pins down, and what
you must ask. Do this before writing `model.json`.

## Reading the sketch

1. Identify the views: front, side, top (plan), or a perspective drawing. A
   perspective sketch gives proportions only; do not read dimensions off
   foreshortened edges.
2. List the parts. Separate objects that touch or stack (a lid on a box) are
   separate parts with a `placement`; one connected solid is one part.
3. For each part pick the simplest construction that matches:
   - round and turned (cup, vase, knob): `revolve` of an r-z profile (use
     the arcs `profile` for curved outlines), or a `cylinder` plus fillets
   - prismatic (box, bracket, stand): `box`, or `extrude` of a 2D outline
   - outline with curves: `extrude` with an `arcs` profile
   - changing cross-section (a taper, a loft between shapes): `loft`
   - holes, slots, bosses repeated in a row or ring: `hole` / `pattern`
   - empty inside: `finish` with `shell` (needs wall thickness and the open face)
   - none of these fit: a `code` part (last resort)
4. Transcribe every number written on the sketch, with its unit. Check the unit
   (mm, cm, inch); the model is millimetres. Note radius versus diameter marks
   (R, Ø, "dia").
5. Note values that are only proportional ("the lid is about half the height").
6. Note what you cannot see: depth behind a front view, whether it is hollow,
   wall thickness, hole depth, hidden features.

## `intake.json`

Free-form (no script validates it) but keep this shape so the user and later
steps can read it:

```json
{
  "summary": "Phone stand: sloped back plate on a flat base, with a cable hole",
  "views": ["front", "side"],
  "parts": [
    {
      "id": "stand",
      "construction": "extrude of the side outline, one through hole, 2 mm fillets"
    }
  ],
  "partsList": {
    "source": "given by the user with the sketch",
    "rows": [
      {"item": 1, "parts": "stand", "name": "거치대", "nameEn": "stand", "material": "PLA, 무광",
       "kind": "pla", "size": "80 × 70 × 95", "role": "휴대폰을 기대어 세운다"}
    ]
  },
  "dimensionsFromSketch": [
    {"label": "Overall width", "valueMm": 80, "note": "written on the front view"}
  ],
  "proportionalOnly": ["Base thickness looks like 1/10 of the height"],
  "uncertain": ["Depth is not shown", "Hole diameter is not written"],
  "questions": [
    "What is the depth (front to back) of the stand?",
    "Hole diameter for the cable (about 12 mm?)"
  ]
}
```

`partsList` is only for a parts list the user hands over with the sketch
(names, material and finish, main size, role; `parts` may name a numbered
group as `screw-1..3`). Leave it out otherwise. It becomes `bom` in
`model.json` (one row per entry, `parts` spelled out as ids); see
[model-schema.md](model-schema.md#bill-of-materials).

## Questions

Always ask when:

- **There is no reference dimension.** At least one number that fixes the
  scale (overall height or width) is required. Without it, ask first; do not
  build from ratios.
- **Depth is hidden.** Ask for it, or record it as an assumption.
- **Hollow or solid is not clear.** Ask. If hollow: wall thickness, and which
  face is open (top of a cup, bottom of a lid).
- **The user wants to 3D print.** Ask for the printer bed size and nozzle
  diameter. If the user does not know, use the defaults (220 x 220 x 250 mm
  bed, 0.4 mm nozzle, 0.8 mm minimum wall) and say so in the report.

Rules for asking:

- Ask once, all questions together, numbered, each with your proposed default
  so the user can answer "ok" ("Depth? I would use 70 mm").
- Do not ask what does not change the geometry.
- If the user says "you decide", proceed and record every guess as a
  `dimensions` entry with `"origin": "assumed"` and a sentence in
  `assumptions`. Show that list in the final report.
- When the user answers a question, the value's origin is `user`, not
  `sketch`.
- A value you read from the sketch but are not sure about (smudged "8" or
  "6"): origin `sketch` only if you are confident, otherwise ask.

## Out of scope: refuse with the reason

Free-form/organic shapes (characters, car bodies, ergonomic grips),
moving-mechanism simulation, sheet-metal flat patterns, tolerance/GD&T
manufacturing drawings, building or room scale. Say plainly that it is out of
scope and why (the tool builds only dimensioned solids from features). Do not
build a "close enough" block or cylinder instead. If part of the object is
in scope (the box of a pencil case but not the sculpted lid), say that, and
only model that part if the user agrees.
