# model.json reference

The validator (`sketchcad/schema.py`) rejects anything not described here with
`<json path>: <cause>`. Units are mm, the up axis is Z, angles are degrees.
Every ```json block below is a complete model that passes the validator.

## Top level

| field | required | notes |
| --- | --- | --- |
| `schemaVersion` | yes | `1` |
| `units` | yes | `"mm"` |
| `upAxis` | yes | `"Z"` |
| `name` | yes | slug: lowercase letters, digits, hyphens |
| `assumptions` | yes | list of strings (may be empty); one sentence per guess |
| `parts` | yes | non-empty list |
| `placement` | no | list of `{part, translateMm, rotateDeg}` |
| `dimensions` | yes | list (may be empty) of annotated numbers |
| `bom` | no | parts list for the parts sheet; see [Bill of materials](#bill-of-materials) |
| `viewer` | no | `{"wireColor": "#RRGGBB", "partColors": {"<part id>": "#RRGGBB"}}`; see [Viewer](#viewer) |
| `source` | no | free-form (for example the sketch file and its hash); not validated. If `source.images[0].file` names a readable image inside the model folder, the build copies it to `OUT/source/` and the viewer opens on it (downscaled); otherwise that stage is skipped |

Relative file paths (`code.file`, `step` `file`) resolve against the folder
that contains `model.json` and may not leave it.

## Example 1: extruded outline, hole, fillet

```json
{
  "schemaVersion": 1,
  "units": "mm",
  "upAxis": "Z",
  "name": "phone-stand",
  "source": { "images": [{ "file": "sketch.jpg" }] },
  "assumptions": ["Depth of 70 mm is not on the sketch; the user said 'about a hand'"],
  "parts": [
    {
      "id": "body",
      "construction": "solid",
      "features": [
        {
          "id": "base",
          "operation": "base",
          "kind": "extrude",
          "profile": { "plane": "XZ", "points": [[0, 0], [80, 0], [80, 6], [0, 6]] },
          "depthMm": 70
        },
        {
          "id": "cable",
          "operation": "cut",
          "kind": "hole",
          "at": [40, 35, 6],
          "direction": "-Z",
          "diameterMm": 12,
          "through": true
        }
      ],
      "finish": [{ "kind": "fillet", "radiusMm": 1.5, "edges": "face:+Z" }]
    }
  ],
  "dimensions": [
    { "label": "Overall width", "valueMm": 80, "origin": "sketch", "part": "body" },
    { "label": "Plate thickness", "valueMm": 6, "origin": "sketch", "part": "body" },
    { "label": "Depth", "valueMm": 70, "origin": "assumed", "part": "body" },
    { "label": "Cable hole diameter", "valueMm": 12, "origin": "user", "part": "body", "feature": "cable" }
  ]
}
```

## Parts

`parts[]`: `id` (slug, unique), `construction`, plus:

- `solid` or `hollow`: `features` (non-empty) and optional `finish`.
  `hollow` requires exactly one `shell` in `finish`.
- `code`: `code` object, no `features`, no `finish`
  (see [code-parts.md](code-parts.md)).

Some part ids are reserved because they name drawing sheets: `assembly` and
any id ending in `-notes` or `-notes-<n>` (notes continuation sheets are
`drawing/<id>-notes.svg`, `<id>-notes-2.svg`, ...). Strings anywhere in the
model must not contain control characters (newlines, tabs, ...).

### Features

`features[]` is ordered. Each has `id` (slug, unique within the part),
`operation` and `kind`. The first feature must be `"base"`; every later one is
`"union"` or `"cut"`. A cut that removes no volume, a union that adds none,
or a result that is not one connected valid solid is an error.

Kinds and fields (all lengths mm):

| kind | fields | convention |
| --- | --- | --- |
| `box` | `originMm` [x,y,z], `sizeMm` [dx,dy,dz] > 0 | `originMm` is the minimum corner |
| `cylinder` | `centerMm` [x,y,z], `radiusMm`, `heightMm`, `axis` X/Y/Z | `centerMm` is the center of the **base circle**; extends **+axis** by `heightMm` |
| `sphere` | `centerMm`, `radiusMm` | |
| `revolve` | `profileRzMm` list of >= 3 `[r, z]`, **or** `profile` (arcs form in `[r, z]`); `r >= 0` | closed outline revolved 360 degrees about the Z axis |
| `loft` | `sections` (>= 2), `ruled` (true/false, required) | section kinds below |
| `step` | `file`, `sha256` (both required) | imports one solid from a STEP file; hash must match |
| `extrude` | `profile`, `depthMm` > 0 | profile forms below |
| `hole` | `at`, `direction`, `diameterMm`, one of `through: true` / `depthMm`, optional `counterbore` | below |
| `pattern` | `of`, one of `linear` / `polar` | below |

**extrude profile**: `{"plane": "XY"|"XZ"|"YZ", ...}` plus exactly one of:

- `points`: >= 3 `[u, v]` vertices, implicitly closed, must not self-intersect.
- `circle`: `{"centerMm": [u, v], "radiusMm": r}`.
- `arcs`: `{"start": [u, v], "segments": [...]}` with >= 2 segments, each
  `{"to": [u, v]}` (straight line) or `{"to": [u, v], "through": [u, v]}`
  (circular arc from the current point, through the middle point, to `to`).
  The outline is **implicitly closed** back to `start` by a straight line, so
  a final straight segment back to `start` may be left out. A closing **arc**
  must be written and must end exactly at `start`. Collinear or coincident
  arc points are rejected. Make an arc either tangent to its neighbours or
  clearly angled to them: a near-tangent corner (a few degrees) breaks fillets
  and the STEP check. A through-point meant to make an arc tangent needs
  enough decimals (4) that the arc does not overshoot the next line.

**revolve profile**: `profileRzMm` is a closed polygon of `[r, z]` points.
For anything round (a knob head, a handle, a rim) use `profile` instead: the
same `{"start": [r, z], "segments": [...]}` arcs form as above, in `[r, z]`.
Arcs give true curved surfaces; a dense polygon shows facet rings in the
drawing and the viewer. Every given point needs `r >= 0`, and no arc may
swing past the axis (`r < 0`). A dome arc that ends on the axis should be
centred on it (horizontal at the pole); if a rounded through-point leaves its
centre up to 0.01 mm off the axis, it is re-centred exactly. Example, a sphere of radius 5 from two
quarter arcs, closed along the axis:
`{"start": [0, -5], "segments": [{"to": [5, 0], "through": [3.5355, -3.5355]},
{"to": [0, 5], "through": [3.5355, 3.5355]}]}`.

`[u, v]` are the plane's own axes, and the extrusion goes in the positive
normal direction by `depthMm`:

| plane | u, v | extrudes toward |
| --- | --- | --- |
| `XY` | x, y | +Z |
| `XZ` | x, z | +Y |
| `YZ` | y, z | +X |

**loft sections**: `{"kind": "polygon", "pointsMm": [[x,y,z], ...]}` (>= 3 3D
points) or `{"kind": "ellipse", "originMm": [x,y,z], "plane": "XY"|"YZ"|"XZ",
"radiusUMm": a, "radiusVMm": b}` (u, v as in the table above). `ruled: true`
gives straight transitions, `false` a smooth blend.

Loft rules: each polygon section must lie in one plane. Points up to 0.01 mm
off the plane (rounded coordinates on a tilted plane) are projected onto it;
more is an error naming the section ("loft section 2 is not planar (max
deviation X mm)", counting from 1). Winding direction (clockwise or not) does
not matter. For a predictable, untwisted transition give every polygon the
same number of points, listed from corresponding corners.

**hole** (a cutting tool; use it with `"operation": "cut"`):

- `at` is the **entry point** on the surface; `direction` is the signed drill
  axis, one of `X Y Z -X -Y -Z` (`+X` also accepted). The hole runs from `at`
  along `direction`.
- `through: true` passes fully through the part (the tool is 1000 mm long,
  centered on `at`); otherwise `depthMm` makes a blind hole of that depth from
  `at`. Give exactly one of the two.
- `counterbore`: `{"diameterMm", "depthMm"}`, wider than the hole, measured
  from `at`.

**pattern** repeats an **earlier feature of the same part** (`of`: its id) and
is combined with the model by its own `operation`:

- The copies **exclude the original**; the original is already applied by its
  own feature.
- `linear`: `{"countX", "countY"(optional), "stepMm": [dx,dy,dz]}`. With
  `countY` omitted or 1: `countX` positions spaced by `stepMm` (any direction).
  With `countY > 1`: a grid with the X step `stepMm[0]` and the Y step
  `stepMm[1]`; `stepMm[2]` **must be 0**. A linear pattern must make at least
  one copy (`countX * countY >= 2`).
- `polar`: `{"count", "axis": "X"|"Y"|"Z", "totalDeg"}`. `count` **includes the
  original**: `count: 6` over 360 gives 5 copies at 60, 120, ... 300 degrees.
  For `totalDeg < 360` the original and last copy sit `totalDeg` apart (steps of
  `totalDeg / (count - 1)`). Rotation is about the world axis through the
  origin, so place the source feature accordingly. `totalDeg` is in (0, 360].

### Finishes

`finish[]` is applied in order, after all features:

- `{"kind": "fillet", "radiusMm": r, "edges": <selector>}`
- `{"kind": "chamfer", "distanceMm": d, "edges": <selector>}`
- `{"kind": "shell", "thicknessMm": t, "openFace": "+Z"}`: hollows the part
  inward, removing that one face (it must be a single flat face at the
  part's extreme in that direction; the outer size must not change).

Each finish may carry `"origin": "sketch" | "user" | "assumed"`. Set
`"assumed"` on every fillet, chamfer or shell size you chose yourself: the
drawing then shows it as `(R0.8)*`, `(C1)*` or `(2)*`. Without `origin` the
value is drawn unmarked. Chamfers and shell thickness are listed in the notes
table; fillet radii get an R leader in a view (or a table row when no view
shows the arc).

Edge selectors:

- `all-outer`: every edge of the part except the seam edges of closed (round)
  faces.
- `face:+Z` (also `-Z`, `+X`, `-X`, `+Y`, `-Y`): the boundary edges of the
  flat faces whose outward normal points that way **and** that lie at the
  part's outermost coordinate on that side (the top for `+Z`). Inner faces
  that point the same way, such as a blind-hole bottom or a lower step, are
  not picked; use `feature:<id>` for those. A hole that opens on the top face
  still gets its rim edge (it is a boundary edge of that face).
- `feature:<id>`: **approximate.** The final edges that lie on the faces of
  that feature's own solid (its tool), i.e. edges the feature created or
  reshaped. It does not track history through later operations, and it may
  also pick coplanar edges of other features that sit flush against it. A
  selector matching nothing is an error. Check the result in the drawing, or
  move the part to a code part if you need exactness.

A fillet or chamfer that fails (radius too big for an edge) reports the
finish index and suggests a smaller value or a code part.

## Example 2: hollow cup (revolve + shell + chamfer)

```json
{
  "schemaVersion": 1,
  "units": "mm",
  "upAxis": "Z",
  "name": "cup",
  "assumptions": ["Wall thickness 3 mm assumed; the sketch shows a hollow cup without a wall note"],
  "parts": [
    {
      "id": "cup",
      "construction": "hollow",
      "features": [
        {
          "id": "body",
          "operation": "base",
          "kind": "revolve",
          "profileRzMm": [[0, 0], [30, 0], [38, 90], [0, 90]]
        }
      ],
      "finish": [
        { "kind": "shell", "thicknessMm": 3, "openFace": "+Z" },
        { "kind": "chamfer", "distanceMm": 0.8, "edges": "face:+Z" }
      ]
    }
  ],
  "dimensions": [
    { "label": "Height", "valueMm": 90, "origin": "sketch", "part": "cup" },
    { "label": "Rim diameter", "valueMm": 76, "origin": "user", "part": "cup" },
    { "label": "Wall thickness", "valueMm": 3, "origin": "assumed", "part": "cup" }
  ]
}
```

## Example 3: arcs profile, counterbore, polar pattern

```json
{
  "schemaVersion": 1,
  "units": "mm",
  "upAxis": "Z",
  "name": "flange-plate",
  "assumptions": [],
  "parts": [
    {
      "id": "flange",
      "construction": "solid",
      "features": [
        {
          "id": "plate",
          "operation": "base",
          "kind": "extrude",
          "profile": {
            "plane": "XY",
            "arcs": {
              "start": [-40, -20],
              "segments": [
                { "to": [40, -20] },
                { "to": [40, 20], "through": [60, 0] },
                { "to": [-40, 20] },
                { "to": [-40, -20], "through": [-60, 0] }
              ]
            }
          },
          "depthMm": 8
        },
        {
          "id": "bolt",
          "operation": "cut",
          "kind": "hole",
          "at": [45, 0, 8],
          "direction": "-Z",
          "diameterMm": 5,
          "through": true,
          "counterbore": { "diameterMm": 9, "depthMm": 3 }
        },
        {
          "id": "bolts",
          "operation": "cut",
          "kind": "pattern",
          "of": "bolt",
          "polar": { "count": 2, "axis": "Z", "totalDeg": 360 }
        }
      ],
      "finish": []
    }
  ],
  "dimensions": [
    { "label": "Bolt circle diameter", "valueMm": 90, "origin": "sketch", "part": "flange" },
    { "label": "Plate thickness", "valueMm": 8, "origin": "sketch", "part": "flange" }
  ]
}
```

(`count: 2` includes the original, so `bolts` adds one copy at 180 degrees:
two bolt holes in total. A hole copy that lands outside the plate is
harmless as long as the whole cut removes volume.)

## Example 4: loft, linear pattern, two parts with placement

```json
{
  "schemaVersion": 1,
  "units": "mm",
  "upAxis": "Z",
  "name": "organizer",
  "assumptions": ["Lid fits the tray with no clearance; adjust for the printer"],
  "parts": [
    {
      "id": "tray",
      "construction": "hollow",
      "features": [
        {
          "id": "body",
          "operation": "base",
          "kind": "loft",
          "ruled": true,
          "sections": [
            { "kind": "polygon", "pointsMm": [[0, 0, 0], [100, 0, 0], [100, 60, 0], [0, 60, 0]] },
            { "kind": "polygon", "pointsMm": [[-5, -5, 30], [105, -5, 30], [105, 65, 30], [-5, 65, 30]] }
          ]
        }
      ],
      "finish": [{ "kind": "shell", "thicknessMm": 2.4, "openFace": "+Z" }]
    },
    {
      "id": "lid",
      "construction": "solid",
      "features": [
        { "id": "top", "operation": "base", "kind": "box", "originMm": [0, 0, 0], "sizeMm": [110, 70, 3] },
        { "id": "peg", "operation": "union", "kind": "cylinder", "centerMm": [10, 10, 3], "radiusMm": 3, "heightMm": 4, "axis": "Z" },
        {
          "id": "pegs",
          "operation": "union",
          "kind": "pattern",
          "of": "peg",
          "linear": { "countX": 3, "countY": 2, "stepMm": [45, 50, 0] }
        }
      ]
    }
  ],
  "placement": [{ "part": "lid", "translateMm": [-5, -5, 30], "rotateDeg": [0, 0, 0] }],
  "dimensions": [
    { "label": "Tray height", "valueMm": 30, "origin": "sketch", "part": "tray" },
    { "label": "Wall thickness", "valueMm": 2.4, "origin": "assumed", "part": "tray" }
  ],
  "viewer": { "wireColor": "#6E6A68", "partColors": { "lid": "#8193AB" } }
}
```

## Placement

`placement[]`: `{"part": <id>, "translateMm": [x,y,z], "rotateDeg": [rx,ry,rz]}`
(both vectors required). Rotation is applied about X, then Y, then Z, then the
translation. A part without an entry sits at the origin. Placement only
affects `assembly.step/glb`, the assembly drawing and the viewer; part files
and print files are in each part's own frame.

## Bill of materials

`bom` (optional) is the parts list the user gave with the sketch. It fills the
BOM table of the parts sheet (`drawing/assembly-parts.svg/.pdf`, written for
models with two or more parts) and does not change geometry or colors.

`bom[]` rows: `item` (integer >= 1, unique), `name` (non-empty; the user's
language, e.g. Korean), `parts` (non-empty list of part ids; a part may be in
at most one row; qty = its length), and optional `nameEn`, `material` (words:
material and finish, e.g. `"황동, 흑색 래커"`), `kind` (density for the mass
estimate: `cast-iron`, `steel`, `stainless-steel`, `brass`, `bronze`, `copper`,
`nickel-silver`, `zinc`, `aluminium`, `glass`, `wood`, `hardwood`, `leather`,
`rubber`, `pla`, `petg`, `abs`, `nylon`), `size` (main size as text, e.g.
`"Ø24 × 136"`; default: the first part's bounding box `W × D × H`) and `role`
(one line). Other fields are rejected.

Parts no row names get derived rows after the given ones: numbered copies
(`screw-1`, `screw-2`, ...) become one row with qty 2+, any other part a row
of its own, named by its id. Without `bom` every row is derived. The parts
sheet draws one view per row (the row's first part, in its own frame) with an
item balloon (`×n` for qty > 1). The estimated mass is solid volume x density;
rows without `kind` count at PLA density and the sheet says so. If materials
or sizes are guesses, say so in `assumptions`.

```json
{
  "schemaVersion": 1, "units": "mm", "upAxis": "Z", "name": "peg-box", "assumptions": [],
  "parts": [
    {"id": "box", "construction": "solid", "features": [
      {"id": "body", "operation": "base", "kind": "box", "originMm": [0, 0, 0], "sizeMm": [40, 30, 20]}]},
    {"id": "peg-1", "construction": "solid", "features": [
      {"id": "peg", "operation": "base", "kind": "cylinder", "centerMm": [0, 0, 0], "radiusMm": 2, "heightMm": 16, "axis": "Z"}]},
    {"id": "peg-2", "construction": "solid", "features": [
      {"id": "peg", "operation": "base", "kind": "cylinder", "centerMm": [0, 0, 0], "radiusMm": 2, "heightMm": 16, "axis": "Z"}]}
  ],
  "placement": [
    {"part": "peg-1", "translateMm": [5, 5, 20], "rotateDeg": [0, 0, 0]},
    {"part": "peg-2", "translateMm": [35, 5, 20], "rotateDeg": [0, 0, 0]}
  ],
  "dimensions": [],
  "bom": [
    {"item": 1, "name": "상자", "nameEn": "box", "parts": ["box"], "material": "참나무, 기름 마감",
     "kind": "hardwood", "role": "물건을 담는다"},
    {"item": 2, "name": "못", "nameEn": "peg", "parts": ["peg-1", "peg-2"], "material": "황동",
     "kind": "brass", "size": "Ø4 × 16", "role": "뚜껑을 걸어 둔다"}
  ]
}
```

## Viewer

`viewer` (optional) only changes how `viewer/index.html` looks:

- `wireColor`: color of the wire guides, `#RRGGBB`, default `#6E6A68`.
- `partColors`: `{"<part id>": "#RRGGBB"}` for the solid color of individual
  parts. Keys must be existing part ids. Parts without an entry take a muted
  default palette in part order (warm grey, sage, terracotta, slate blue,
  sand, charcoal, olive, mauve); a single part is warm grey. Set it only when
  the user asks for specific colors.
- `partMaterials`: `{"<part id>": "matte" | "satin" | "gloss" | "metal"}` for
  how a part's surface responds to the studio light. Keys must be existing
  part ids. Parts without an entry are `satin` (soft sheen, close to printed
  or painted plastic). `matte` is a dull, chalky surface; `gloss` a lacquered
  or enamelled one with sharp clearcoat highlights; `metal` reflects the
  surroundings tinted by the part color, so give metal parts a light,
  saturated `partColors` value (brass `#D4AF62`, steel `#B4B8BD`, cast iron
  `#6E6F71`), since a dark color makes a dark mirror. Example for black
  enamel with a brass collar: `"partColors": {"tube": "#151515", "collar":
  "#D4AF62"}, "partMaterials": {"tube": "gloss", "collar": "metal"}`.

## Dimensions

`dimensions[]`: `label` (string), `valueMm` (> 0), `origin`
(`sketch` | `user` | `assumed`); optional `part` (a part id) and either
`feature` (a feature id of that part; requires `part`) or `span`
(`"width"` | `"depth"` | `"height"`: the part's overall size along X / Y / Z
in its own frame; requires `part`; not together with `feature`).

They **annotate**, they do not drive the geometry: make the features produce
these numbers. The drawing adds overall width/depth/height and feature
parameters (hole and cylinder diameters, fillet radius, extrude depth,
revolve diameter/height) automatically; an entry is merged onto a matching
feature dimension when `feature` is given and the value matches, otherwise it
is listed in the notes table. `assumed` values are drawn as `(value)*` and
listed in `report.md`. A part-less entry goes to the sole part, or to the
assembly sheet when there are several parts.

Mark the reference dimension with `span` when it is an overall size, e.g.
`{"label": "Base length (reference)", "valueMm": 160, "origin": "assumed",
"part": "base", "span": "width"}`. That overall dimension on the part's sheet
is then drawn with the entry's value and marking (`(160)*`) and the entry is
also listed by its label in the notes table. The label shows the **stated**
`valueMm`, not the measured size (which may differ by up to 0.5 mm). Any
other dimension in any view that spans the same extent (for example a
diameter seen in the top view) gets the same value and marking. The build fails with an error
naming the entry when `valueMm` is more than 0.5 mm off the part's real size.
Overall sizes without a `span` entry are measured from the geometry and shown
to 0.1 mm; values the model states (feature parameters, `dimensions`) are
shown as written, up to 3 decimals.

## Example 5: STEP import and code part

```json
{
  "schemaVersion": 1,
  "units": "mm",
  "upAxis": "Z",
  "name": "knob-on-ring",
  "assumptions": ["Knob head radius 12 mm estimated from the sketch proportions"],
  "parts": [
    {
      "id": "ring",
      "construction": "solid",
      "features": [
        {
          "id": "ring-body",
          "operation": "base",
          "kind": "step",
          "file": "parts/ring.step",
          "sha256": "9f2b6c1d0e4a47b8a3d5c7e1f0b2a4c6d8e0f1a3b5c7d9e1f2a4b6c8d0e2f4a6"
        }
      ]
    },
    {
      "id": "knob",
      "construction": "code",
      "code": { "file": "parts/knob.py" }
    }
  ],
  "placement": [{ "part": "knob", "translateMm": [0, 0, 5], "rotateDeg": [0, 0, 0] }],
  "dimensions": [{ "label": "Knob head radius", "valueMm": 12, "origin": "assumed", "part": "knob" }]
}
```

(The STEP file must exist and match `sha256` at build time; the validator
only checks that the file exists and the hash is well formed.)

## Common mistakes

- The first feature has `operation` `union` (it must be `base`).
- A `pattern` placed before the feature it repeats, or `of` naming a feature
  from another part.
- `polar.count` counted as copies. It includes the original.
- A `hole` whose `at` is not on the surface; the tool starts at `at`, so a
  blind hole starting in mid-air removes nothing and fails as an ineffective
  cut.
- A `hollow` part with no `shell`, or a shell whose `openFace` is not a single
  flat extremal face.
- Writing outputs or code files inside the skill folder.
