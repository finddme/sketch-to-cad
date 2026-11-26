"""Bill of materials: the rows of the parts sheet (부품표).

A model may carry a hand-written `bom` (the parts list the user gave with the
sketch, see references/model-schema.md). bom_rows() returns those rows in item
order and adds derived rows for every part no row names: numbered copies
(`screw-1`, `screw-2`, ...) become one row with their count, every other part
a row of its own. A row without a `size` gets the part's bounding box.
"""
import math
import re


def fmt_mm(v, decimals=3):
    """Same as dims.fmt_mm (kept here so the schema check needs no font tooling)."""
    s = f"{float(v):.{decimals}f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


# g/cm3, for the mass estimate on the parts sheet (volume x density). The keys are
# the values `bom[].kind` accepts.
DENSITIES = {
    "cast-iron": 7.2, "steel": 7.85, "stainless-steel": 7.9, "brass": 8.5, "bronze": 8.8,
    "copper": 8.96, "nickel-silver": 8.7, "zinc": 7.1, "aluminium": 2.7, "glass": 2.5,
    "wood": 0.6, "hardwood": 0.75, "leather": 0.9, "rubber": 1.2, "pla": 1.24, "petg": 1.27,
    "abs": 1.05, "nylon": 1.14,
}
DEFAULT_KIND = "pla"  # rows without a kind: the print material
ROW_FIELDS = ("item", "name", "nameEn", "parts", "material", "kind", "size", "role")
NUMBERED = re.compile(r"^(.+)-([0-9]+)$")


def bbox_size(bounds):
    """'W × D × H' of a manifest boundsMm entry, to 0.1 mm."""
    lo, hi = bounds["min"], bounds["max"]
    return " × ".join(fmt_mm(hi[k] - lo[k], 1) for k in range(3))


def _derived(pids, start):
    groups = {}
    for pid in pids:
        m = NUMBERED.match(pid)
        groups.setdefault(m.group(1) if m else pid, []).append(pid)
    rows, item = [], start
    seen = set()
    for pid in pids:
        m = NUMBERED.match(pid)
        key = m.group(1) if m else pid
        if key in seen:
            continue
        seen.add(key)
        members = groups[key]
        if m and len(members) == 1:  # a lone "pin-1" keeps its own name
            key = pid
        rows.append({"item": item, "name": key, "parts": members})
        item += 1
    return rows


def bom_rows(model, bounds):
    """Rows {item, name, nameEn, parts, qty, material, kind, size, role, derived}
    in item order: the model's own rows, then derived rows for the other parts.

    bounds: {part id: {"min": [x, y, z], "max": [...]}} (manifest parts[].boundsMm).
    """
    given = sorted(model.get("bom", []), key=lambda r: r["item"])
    named = {pid for r in given for pid in r["parts"]}
    rest = [p["id"] for p in model["parts"] if p["id"] not in named]
    start = max((r["item"] for r in given), default=0) + 1
    rows = []
    for r, derived in [(r, False) for r in given] + [(r, True) for r in _derived(rest, start)]:
        rows.append({
            "item": r["item"], "name": r["name"], "nameEn": r.get("nameEn", ""),
            "parts": list(r["parts"]), "qty": len(r["parts"]),
            "material": r.get("material", ""), "kind": r.get("kind", ""),
            "size": r.get("size") or bbox_size(bounds[r["parts"][0]]),
            "role": r.get("role", ""), "derived": derived,
        })
    return rows


def mass_grams(rows, volumes):
    """(grams, defaulted): solid volume x density over every part; rows without a
    kind count at DEFAULT_KIND, and defaulted says whether any did."""
    grams, defaulted = 0.0, False
    for r in rows:
        kind = r["kind"] or DEFAULT_KIND
        defaulted |= not r["kind"]
        grams += sum(volumes[p] for p in r["parts"]) / 1000.0 * DENSITIES[kind]
    return grams, defaulted


def format_mass(grams):
    if grams >= 1000:
        return f"약 {fmt_mm(grams / 1000, 1)} kg"
    if grams >= 10:
        digits = 1 - int(math.floor(math.log10(grams)))  # two significant figures
        return f"약 {round(grams, digits):.0f} g"
    return f"약 {fmt_mm(grams, 1)} g"
