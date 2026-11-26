"""model.json schema validation (design spec 6.3).

`validate(model, base_dir)` returns a normalized deep copy (optional fields
defaulted) or raises ModelError with messages of the form "<path>: <cause>".
"""
import copy
import math
import re
from pathlib import Path

from .bom import DENSITIES, ROW_FIELDS
from .errors import ModelError

SCHEMA_VERSION = 1
DEFAULT_WIRE_COLOR = "#6E6A68"
PART_MATERIALS = ("matte", "satin", "gloss", "metal")  # viewer surface response
DEFAULT_PART_MATERIAL = "satin"

SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
EDGE_SELECTOR = re.compile(r"^(all-outer|feature:(.+)|face:[+-][XYZ])$")
SIGNED_AXIS = re.compile(r"^[+-]?[XYZ]$")
FACE = re.compile(r"^[+-][XYZ]$")
CONTROL = re.compile(r"[\x00-\x1f]")
# Drawing sheet names: the assembly sheet, the parts sheet (assembly-parts), and notes continuation sheets <id>-notes[-k].
RESERVED_PART_ID = re.compile(r"^(assembly(-parts)?|.*-notes(-[0-9]+)?)$")

CONSTRUCTIONS = ("solid", "hollow", "code")
OPERATIONS = ("union", "cut")
ORIGINS = ("sketch", "user", "assumed")
PLANES = ("XY", "YZ", "XZ")
AXES = ("X", "Y", "Z")
SPANS = ("width", "depth", "height")  # overall part size along X, Y, Z


def _fail(path, cause):
    raise ModelError(f"{path}: {cause}")


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _num(v, path):
    if not _is_num(v):
        _fail(path, f"must be a finite number, got {v!r}")


def _pos(v, path):
    _num(v, path)
    if v <= 0:
        _fail(path, f"must be > 0, got {v!r}")


def _int(v, path, minimum):
    if isinstance(v, bool) or not isinstance(v, int) or v < minimum:
        _fail(path, f"must be an integer >= {minimum}, got {v!r}")


def _vec(v, path, n):
    if not isinstance(v, list) or len(v) != n:
        _fail(path, f"must be a list of {n} numbers, got {v!r}")
    for i, x in enumerate(v):
        _num(x, f"{path}[{i}]")


def _pos_vec(v, path, n):
    _vec(v, path, n)
    for i, x in enumerate(v):
        _pos(x, f"{path}[{i}]")


def _points(v, path, minimum):
    if not isinstance(v, list) or len(v) < minimum:
        _fail(path, f"must be a list of at least {minimum} [x, y] points")
    for i, p in enumerate(v):
        _vec(p, f"{path}[{i}]", 2)


def _obj(v, path):
    if not isinstance(v, dict):
        _fail(path, "must be an object")


def _require(d, key, path):
    if key not in d:
        _fail(path, f"missing required field '{key}'")
    return d[key]


def _choice(v, path, options):
    if v not in options:
        _fail(path, f"must be one of {', '.join(options)}, got {v!r}")


def _slug(v, path):
    if not isinstance(v, str) or not SLUG.match(v):
        _fail(path, f"must be a slug (lowercase letters, digits, hyphens), got {v!r}")


def _sha(v, path):
    if not isinstance(v, str) or not SHA256.match(v):
        _fail(path, "must be a 64-character hex sha256")


def _no_control_chars(v, path):
    """Reject control characters (tabs, newlines, ...) in every string of the model."""
    if isinstance(v, str):
        if CONTROL.search(v):
            _fail(path, f"contains a control character: {v!r}")
    elif isinstance(v, dict):
        for k, x in v.items():
            _no_control_chars(k, f"{path}.<key>" if path else "<key>")
            _no_control_chars(x, f"{path}.{k}" if path else str(k))
    elif isinstance(v, list):
        for i, x in enumerate(v):
            _no_control_chars(x, f"{path}[{i}]")


def _file_in_base(rel, path, base_dir, must_exist=True):
    if not isinstance(rel, str) or not rel:
        _fail(path, "must be a non-empty relative path")
    base = Path(base_dir).resolve()
    target = (base / rel).resolve()
    if not target.is_relative_to(base):
        _fail(path, f"path escapes the model directory: {rel!r}")
    if must_exist and not target.is_file():
        _fail(path, f"file not found: {rel!r}")


def _edges(v, path, feature_ids):
    if not isinstance(v, str) or not EDGE_SELECTOR.match(v):
        _fail(path, f"must be 'all-outer', 'feature:<id>' or 'face:[+-][XYZ]', got {v!r}")
    if v.startswith("feature:") and v[len("feature:"):] not in feature_ids:
        _fail(path, f"unknown feature id {v[len('feature:'):]!r}")


def _check_profile(p, path):
    _obj(p, path)
    _choice(_require(p, "plane", path), f"{path}.plane", PLANES)
    kinds = [k for k in ("points", "circle", "arcs") if k in p]
    if len(kinds) != 1:
        _fail(path, "needs exactly one of points, circle, arcs")
    k = kinds[0]
    if k == "points":
        _points(p["points"], f"{path}.points", 3)
    elif k == "circle":
        c = p["circle"]
        _obj(c, f"{path}.circle")
        _vec(_require(c, "centerMm", f"{path}.circle"), f"{path}.circle.centerMm", 2)
        _pos(_require(c, "radiusMm", f"{path}.circle"), f"{path}.circle.radiusMm")
    else:
        _check_arcs(p["arcs"], f"{path}.arcs")


def _check_arcs(arcs, path):
    """arcs = {start: [u,v], segments: [{to} | {to, through}, ...]}, implicitly closed."""
    _obj(arcs, path)
    for key in arcs:
        if key not in ("start", "segments"):
            _fail(path, f"unknown key {key!r}")
    start = _require(arcs, "start", path)
    _vec(start, f"{path}.start", 2)
    segs = _require(arcs, "segments", path)
    if not isinstance(segs, list) or len(segs) < 2:
        _fail(f"{path}.segments", "must be a list of at least 2 segments")
    cur = start
    for i, seg in enumerate(segs):
        sp = f"{path}.segments[{i}]"
        _obj(seg, sp)
        for key in seg:
            if key not in ("to", "through"):
                _fail(sp, f"unknown key {key!r}")
        to = _require(seg, "to", sp)
        _vec(to, f"{sp}.to", 2)
        if "through" in seg:
            mid = seg["through"]
            _vec(mid, f"{sp}.through", 2)
            ux, uy = mid[0] - cur[0], mid[1] - cur[1]
            vx, vy = to[0] - cur[0], to[1] - cur[1]
            cross = ux * vy - uy * vx
            if abs(cross) <= 1e-9 * math.hypot(ux, uy) * math.hypot(vx, vy):
                _fail(sp, "arc points are collinear or coincide")
        cur = to


def _check_section(s, path):
    _obj(s, path)
    kind = _require(s, "kind", path)
    if kind == "polygon":
        pts = _require(s, "pointsMm", path)
        if not isinstance(pts, list) or len(pts) < 3:
            _fail(f"{path}.pointsMm", "must be a list of at least 3 [x, y, z] points")
        for i, pt in enumerate(pts):
            _vec(pt, f"{path}.pointsMm[{i}]", 3)
    elif kind == "ellipse":
        _vec(_require(s, "originMm", path), f"{path}.originMm", 3)
        _choice(_require(s, "plane", path), f"{path}.plane", PLANES)
        _pos(_require(s, "radiusUMm", path), f"{path}.radiusUMm")
        _pos(_require(s, "radiusVMm", path), f"{path}.radiusVMm")
    else:
        _fail(f"{path}.kind", f"must be one of polygon, ellipse, got {kind!r}")


def _check_kind(f, path, base_dir, earlier_ids):
    kind = _require(f, "kind", path)
    if kind == "box":
        _vec(_require(f, "originMm", path), f"{path}.originMm", 3)
        _pos_vec(_require(f, "sizeMm", path), f"{path}.sizeMm", 3)
    elif kind == "cylinder":
        _vec(_require(f, "centerMm", path), f"{path}.centerMm", 3)
        _pos(_require(f, "radiusMm", path), f"{path}.radiusMm")
        _pos(_require(f, "heightMm", path), f"{path}.heightMm")
        _choice(_require(f, "axis", path), f"{path}.axis", AXES)
    elif kind == "sphere":
        _vec(_require(f, "centerMm", path), f"{path}.centerMm", 3)
        _pos(_require(f, "radiusMm", path), f"{path}.radiusMm")
    elif kind == "revolve":
        forms = [k for k in ("profileRzMm", "profile") if k in f]
        if len(forms) != 1:
            _fail(path, "needs exactly one of profileRzMm, profile")
        if forms[0] == "profileRzMm":
            pts = f["profileRzMm"]
            _points(pts, f"{path}.profileRzMm", 3)
            radii = [(f"{path}.profileRzMm[{i}][0]", p[0]) for i, p in enumerate(pts)]
        else:
            prof = f["profile"]
            _check_arcs(prof, f"{path}.profile")
            radii = [(f"{path}.profile.start[0]", prof["start"][0])]
            for i, seg in enumerate(prof["segments"]):
                radii += [(f"{path}.profile.segments[{i}].{k}[0]", seg[k][0]) for k in ("to", "through") if k in seg]
        for rp, r in radii:
            if r < 0:
                _fail(rp, f"radius must be >= 0, got {r!r}")
    elif kind == "loft":
        secs = _require(f, "sections", path)
        if not isinstance(secs, list) or len(secs) < 2:
            _fail(f"{path}.sections", "must be a list of at least 2 sections")
        for i, s in enumerate(secs):
            _check_section(s, f"{path}.sections[{i}]")
        if not isinstance(_require(f, "ruled", path), bool):
            _fail(f"{path}.ruled", "must be true or false")
    elif kind == "step":
        _file_in_base(_require(f, "file", path), f"{path}.file", base_dir)
        _sha(_require(f, "sha256", path), f"{path}.sha256")
    elif kind == "extrude":
        _check_profile(_require(f, "profile", path), f"{path}.profile")
        _pos(_require(f, "depthMm", path), f"{path}.depthMm")
    elif kind == "hole":
        _vec(_require(f, "at", path), f"{path}.at", 3)
        d = _require(f, "direction", path)
        if not isinstance(d, str) or not SIGNED_AXIS.match(d):
            _fail(f"{path}.direction", f"must be [+-][XYZ], got {d!r}")
        _pos(_require(f, "diameterMm", path), f"{path}.diameterMm")
        has_through, has_depth = "through" in f, "depthMm" in f
        if has_through == has_depth:
            _fail(path, "needs exactly one of through, depthMm")
        if has_through:
            if f["through"] is not True:
                _fail(f"{path}.through", "must be true (use depthMm for blind holes)")
        else:
            _pos(f["depthMm"], f"{path}.depthMm")
        if "counterbore" in f:
            cb = f["counterbore"]
            _obj(cb, f"{path}.counterbore")
            _pos(_require(cb, "diameterMm", f"{path}.counterbore"), f"{path}.counterbore.diameterMm")
            _pos(_require(cb, "depthMm", f"{path}.counterbore"), f"{path}.counterbore.depthMm")
            if cb["diameterMm"] <= f["diameterMm"]:
                _fail(f"{path}.counterbore.diameterMm", "must be larger than diameterMm")
    elif kind == "pattern":
        of = _require(f, "of", path)
        if of not in earlier_ids:
            _fail(f"{path}.of", f"must reference an earlier feature id, got {of!r}")
        modes = [m for m in ("linear", "polar") if m in f]
        if len(modes) != 1:
            _fail(path, "needs exactly one of linear, polar")
        if modes[0] == "linear":
            lin = f["linear"]
            _obj(lin, f"{path}.linear")
            _int(_require(lin, "countX", f"{path}.linear"), f"{path}.linear.countX", 1)
            if "countY" in lin:
                _int(lin["countY"], f"{path}.linear.countY", 1)
            _vec(_require(lin, "stepMm", f"{path}.linear"), f"{path}.linear.stepMm", 3)
            if lin["countX"] * lin.get("countY", 1) < 2:
                _fail(f"{path}.linear", "pattern must produce at least one copy")
        else:
            pol = f["polar"]
            _obj(pol, f"{path}.polar")
            _int(_require(pol, "count", f"{path}.polar"), f"{path}.polar.count", 2)
            _choice(_require(pol, "axis", f"{path}.polar"), f"{path}.polar.axis", AXES)
            _pos(_require(pol, "totalDeg", f"{path}.polar"), f"{path}.polar.totalDeg")
            if pol["totalDeg"] > 360:
                _fail(f"{path}.polar.totalDeg", f"must be <= 360, got {pol['totalDeg']!r}")
    else:
        _fail(
            f"{path}.kind",
            "must be one of box, cylinder, sphere, revolve, loft, step, extrude, hole, "
            f"pattern, got {kind!r}",
        )


def _check_features(features, path, base_dir):
    if not isinstance(features, list) or not features:
        _fail(path, "must be a non-empty list")
    ids = []
    for i, f in enumerate(features):
        fp = f"{path}[{i}]"
        _obj(f, fp)
        fid = _require(f, "id", fp)
        _slug(fid, f"{fp}.id")
        if fid in ids:
            _fail(f"{fp}.id", f"duplicate feature id {fid!r}")
        op = _require(f, "operation", fp)
        if i == 0:
            if op != "base":
                _fail(f"{fp}.operation", f"first feature must be 'base', got {op!r}")
        else:
            _choice(op, f"{fp}.operation", OPERATIONS)
        _check_kind(f, fp, base_dir, ids)
        ids.append(fid)
    return ids


def _check_finish(finish, path, feature_ids, construction):
    if not isinstance(finish, list):
        _fail(path, "must be a list")
    shells = 0
    for i, fin in enumerate(finish):
        fp = f"{path}[{i}]"
        _obj(fin, fp)
        kind = _require(fin, "kind", fp)
        if kind == "fillet":
            _pos(_require(fin, "radiusMm", fp), f"{fp}.radiusMm")
            _edges(_require(fin, "edges", fp), f"{fp}.edges", feature_ids)
        elif kind == "chamfer":
            _pos(_require(fin, "distanceMm", fp), f"{fp}.distanceMm")
            _edges(_require(fin, "edges", fp), f"{fp}.edges", feature_ids)
        elif kind == "shell":
            shells += 1
            _pos(_require(fin, "thicknessMm", fp), f"{fp}.thicknessMm")
            face = _require(fin, "openFace", fp)
            if not isinstance(face, str) or not FACE.match(face):
                _fail(f"{fp}.openFace", f"must be [+-][XYZ], got {face!r}")
        else:
            _fail(f"{fp}.kind", f"must be one of fillet, chamfer, shell, got {kind!r}")
        if "origin" in fin:
            _choice(fin["origin"], f"{fp}.origin", ORIGINS)
    if construction == "hollow" and shells != 1:
        _fail(path, f"hollow part needs exactly one shell, found {shells}")


def _check_part(part, path, base_dir):
    _obj(part, path)
    construction = _require(part, "construction", path)
    _choice(construction, f"{path}.construction", CONSTRUCTIONS)
    if construction == "code":
        if "features" in part:
            _fail(f"{path}.features", "not allowed on a code part")
        code = _require(part, "code", path)
        _obj(code, f"{path}.code")
        _file_in_base(_require(code, "file", f"{path}.code"), f"{path}.code.file", base_dir)
        if "sha256" in code:
            _sha(code["sha256"], f"{path}.code.sha256")
        if part.get("finish"):
            _fail(f"{path}.finish", "code parts cannot have finishes")
        return {}
    ids = _check_features(_require(part, "features", path), f"{path}.features", base_dir)
    _check_finish(part.get("finish", []), f"{path}.finish", ids, construction)
    return set(ids)


def _text(v, path, required=False):
    if not isinstance(v, str):
        _fail(path, f"must be a string, got {v!r}")
    if required and not v.strip():
        _fail(path, "must be a non-empty string")


def _check_bom(rows, part_ids):
    """Parts list rows: unique item numbers, known part ids, each part in one row."""
    if not isinstance(rows, list) or not rows:
        _fail("bom", "must be a non-empty list of rows")
    items, owner = set(), {}
    for i, row in enumerate(rows):
        rp = f"bom[{i}]"
        _obj(row, rp)
        for key in row:
            if key not in ROW_FIELDS:
                _fail(rp, f"unknown field {key!r} (fields: {', '.join(ROW_FIELDS)})")
        item = _require(row, "item", rp)
        _int(item, f"{rp}.item", 1)
        if item in items:
            _fail(f"{rp}.item", f"duplicate item {item}")
        items.add(item)
        _text(_require(row, "name", rp), f"{rp}.name", required=True)
        for key in ("nameEn", "material", "size", "role"):
            if key in row:
                _text(row[key], f"{rp}.{key}")
        if "kind" in row:
            _choice(row["kind"], f"{rp}.kind", tuple(DENSITIES))
        parts = _require(row, "parts", rp)
        if not isinstance(parts, list) or not parts:
            _fail(f"{rp}.parts", "must be a non-empty list of part ids")
        for k, pid in enumerate(parts):
            if pid not in part_ids:
                _fail(f"{rp}.parts[{k}]", f"unknown part id {pid!r}")
            if pid in owner:
                _fail(f"{rp}.parts[{k}]", f"part {pid!r} is already in item {owner[pid]}")
            owner[pid] = item


def validate(model, base_dir):
    """Validate a model.json dict; return a normalized deep copy."""
    if not isinstance(model, dict):
        _fail("model", "must be a JSON object")
    m = copy.deepcopy(model)
    _no_control_chars(m, "")

    if m.get("schemaVersion") != SCHEMA_VERSION:
        _fail("schemaVersion", f"expected {SCHEMA_VERSION}, got {m.get('schemaVersion')!r}")
    if m.get("units") != "mm":
        _fail("units", f"must be 'mm', got {m.get('units')!r}")
    if m.get("upAxis") != "Z":
        _fail("upAxis", f"must be 'Z', got {m.get('upAxis')!r}")
    _slug(_require(m, "name", "name"), "name")
    if not isinstance(_require(m, "assumptions", "assumptions"), list):
        _fail("assumptions", "must be a list")

    parts = _require(m, "parts", "parts")
    if not isinstance(parts, list) or not parts:
        _fail("parts", "must be a non-empty list")
    part_ids, features_by_part = [], {}
    for i, part in enumerate(parts):
        pp = f"parts[{i}]"
        _obj(part, pp)
        pid = _require(part, "id", pp)
        _slug(pid, f"{pp}.id")
        if RESERVED_PART_ID.match(pid):
            _fail(f"{pp}.id", f"{pid!r} is reserved for drawing sheet names "
                  "('assembly', 'assembly-parts' and ids ending in '-notes' or '-notes-<n>')")
        if pid in part_ids:
            _fail(f"{pp}.id", f"duplicate part id {pid!r}")
        features_by_part[pid] = _check_part(part, pp, base_dir)
        part.setdefault("finish", [])
        part_ids.append(pid)

    m.setdefault("placement", [])
    if not isinstance(m["placement"], list):
        _fail("placement", "must be a list")
    for i, pl in enumerate(m["placement"]):
        pp = f"placement[{i}]"
        _obj(pl, pp)
        ref = _require(pl, "part", pp)
        if ref not in part_ids:
            _fail(f"{pp}.part", f"unknown part id {ref!r}")
        _vec(_require(pl, "translateMm", pp), f"{pp}.translateMm", 3)
        _vec(_require(pl, "rotateDeg", pp), f"{pp}.rotateDeg", 3)

    dims = _require(m, "dimensions", "dimensions")
    if not isinstance(dims, list):
        _fail("dimensions", "must be a list")
    for i, d in enumerate(dims):
        dp = f"dimensions[{i}]"
        _obj(d, dp)
        label = _require(d, "label", dp)
        if not isinstance(label, str) or not label:
            _fail(f"{dp}.label", "must be a non-empty string")
        _pos(_require(d, "valueMm", dp), f"{dp}.valueMm")
        _choice(_require(d, "origin", dp), f"{dp}.origin", ORIGINS)
        if "part" in d and d["part"] not in part_ids:
            _fail(f"{dp}.part", f"unknown part id {d['part']!r}")
        if "feature" in d:
            if "part" not in d:
                _fail(f"{dp}.feature", "requires 'part'")
            if d["feature"] not in features_by_part[d["part"]]:
                _fail(f"{dp}.feature", f"unknown feature id {d['feature']!r} in part {d['part']!r}")
        if "span" in d:
            if "feature" in d:
                _fail(dp, "span and feature are mutually exclusive")
            _choice(d["span"], f"{dp}.span", SPANS)
            if "part" not in d:
                _fail(f"{dp}.span", "requires 'part'")

    if "bom" in m:
        _check_bom(m["bom"], part_ids)

    viewer = m.setdefault("viewer", {})
    _obj(viewer, "viewer")
    color = viewer.setdefault("wireColor", DEFAULT_WIRE_COLOR)
    if not isinstance(color, str) or not HEX_COLOR.match(color):
        _fail("viewer.wireColor", f"must be #RRGGBB, got {color!r}")
    if "partColors" in viewer:
        colors = viewer["partColors"]
        _obj(colors, "viewer.partColors")
        for pid, value in colors.items():
            if pid not in part_ids:
                _fail(f"viewer.partColors.{pid}", f"unknown part id {pid!r}")
            if not isinstance(value, str) or not HEX_COLOR.match(value):
                _fail(f"viewer.partColors.{pid}", f"must be #RRGGBB, got {value!r}")
    if "partMaterials" in viewer:
        materials = viewer["partMaterials"]
        _obj(materials, "viewer.partMaterials")
        for pid, value in materials.items():
            if pid not in part_ids:
                _fail(f"viewer.partMaterials.{pid}", f"unknown part id {pid!r}")
            _choice(value, f"viewer.partMaterials.{pid}", PART_MATERIALS)
    return m
