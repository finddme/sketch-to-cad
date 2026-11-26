# Printing

`print-check` (also run by `all`) takes each part's STEP, checks it as a mesh,
turns it into a good print orientation and writes `print/<id>.stl`,
`print/print-plate.3mf`, `print/print-report.json` and `print/print-report.md`.
Checks only warn; files are always written. Parts are checked one by one; the
assembly as a whole is not checked.

## Printer profile

Defaults: bed 220 x 220 x 250 mm, nozzle 0.4 mm, minimum wall 0.8 mm,
overhang threshold 45 degrees. Override with `--printer printer.json`
(accepted by `print-check` and `all`; any subset of the keys, unknown keys are
rejected, and `all` validates the file before building):

```json
{ "bedMm": [180, 180, 180], "nozzleMm": 0.6, "minWallMm": 1.2, "overhangDeg": 50 }
```

`bedMm` three positive numbers; `nozzleMm`, `minWallMm` positive;
`overhangDeg` strictly between 0 and 90. Ask the user for bed size and nozzle;
if you used the defaults, say so.

## What the report means

Per part (`print-report.json` -> `parts.<id>`):

| field | meaning |
| --- | --- |
| `watertight` | mesh is closed and consistently wound. False means slicers may fail or print gaps. |
| `fitsBed`, `sizeMm` | size in the chosen print orientation versus the bed. |
| `minWallMm` | **estimate** of the thinnest wall: a low percentile of ray distances from surface samples straight inward. Rays that never hit again are ignored; `null` means nothing could be measured. `minWallEstimated` is always true. Do not present it as an exact measurement. |
| `overhangAreaRatio` | share of surface area that overhangs, **as modeled** (the orientation in the CAD model). |
| `overhangAreaRatioOriented` | the same ratio **after the chosen print orientation**. This is the one that matters for printing. |
| `overhangRegionsMm` | the largest overhanging patches (`centerMm`, `areaMm2`) in the oriented part, to tell the user where supports will go. |
| `transform` | the 4x4 rotation + translation applied to put the part on the bed (lowest point at z = 0). |
| `warnings` | human-readable list; relay these. |

Orientation: candidates lay each of the largest flat hull faces down (and also
turned 90 degrees about Z); candidates that fit the bed are preferred, then the
least overhang area, then the larger contact area with the bed. The assembly
placement is deliberately not used for printing: stacked or inserted parts
would float or overlap.

`plate.warnings` reports when the row layout of all parts exceeds the bed;
print those parts in separate runs.

## Acting on warnings

- not watertight: usually a modeling problem (zero-thickness face); check the
  features, simplify, rebuild.
- does not fit the bed: ask the user whether to scale the design or print in
  pieces (split into parts); never scale silently.
- wall below minimum: thicken the wall (ask the user; it is a dimension),
  or tell them the printer may not resolve it.
- more than 10% overhang after orientation: tell the user supports will be
  needed, and where (`overhangRegionsMm`). Suggest design changes only as
  options.

Slicing (G-code) is not done: load `print-plate.3mf` (whole layout) or
`print/<id>.stl` (per part) into a slicer.
