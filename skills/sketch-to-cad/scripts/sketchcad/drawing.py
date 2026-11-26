"""Drawing stage: dimension collection, collision-free layout, sheets (spec 6.5).

run_drawing(out) reads the build output (manifest, parts/*.step,
model.json) and writes drawing/<id>.svg/.pdf, plus assembly.svg/.pdf when
the model has two or more parts, and then the parts sheet
assembly-parts.svg/.pdf (partsheet.py).
"""
import itertools
import json
import math
import shutil
from dataclasses import dataclass
from pathlib import Path

from build123d import Compound, GeomType

from .build import load_manifest, load_part_shapes, place_shape, write_manifest
from .dims import dim_h, dim_v, fmt_mm, radius_leader
from .errors import ModelError
from .features import make_feature
from .project import VIEW_AXES, project_view, to_view
from .sheet import DRAW_AREA, render_sheet, svg_to_pdf

VIEWS = ("front", "top", "right", "iso")
PROJECTIONS = ("first", "third")
MODEL_ORIGINS = ("sketch", "user", "assumed")
MATCH_TOL_MM = 1e-4
MEASURED_DECIMALS = 1  # overall sizes read off the geometry are shown to 0.1 mm

FIRST_OFFSET = 8.0   # paper mm from the view outline to the first dimension line
TIER_STEP = 8.0      # each push moves a dimension this much further out
MAX_PUSHES = 6       # then the opposite side, then the notes table
VIEW_GAP = 10.0
TITLE_SPACE = 8.0  # view title (plus centre-line overshoot) below the dims
ISO_FACTORS = (1.0, 0.75, 0.5)  # the isometric view may be drawn smaller than the sheet scale
ISO_MIN_FACTOR = 0.15  # below this the iso view is left out rather than drawn as a speck
SCALES = [(10, "10:1"), (5, "5:1"), (2, "2:1"), (1, "1:1"), (0.5, "1:2"), (0.4, "1:2.5"), (0.2, "1:5"),
          (0.1, "1:10"), (0.05, "1:20"), (0.02, "1:50"), (0.01, "1:100"), (0.005, "1:200")]
GRID = {  # view -> (row, column)
    "third": {"top": (0, 0), "iso": (0, 1), "front": (1, 0), "right": (1, 1)},
    "first": {"right": (0, 0), "front": (0, 1), "iso": (1, 0), "top": (1, 1)},
}


@dataclass
class Dim:
    """One dimension in view coordinates (model mm, see project.VIEW_AXES).

    h/v: a and b are the measured points. dia: a = centre, b = a point on the
    circle. r: a = arc centre, b = arc midpoint. view == "table" means the
    value has no place in a view and goes to the notes table.
    """
    view: str
    kind: str
    a: tuple
    b: tuple
    label: str
    origin: str
    name: str = ""
    measured: bool = False  # label rounded from the geometry, not a value the model states


@dataclass
class Placed:
    dim: Dim
    side: str            # below | above | left | right | leader | table
    tier: int
    geom: dict | None    # dims.dim_h / dim_v / radius_leader record (paper mm)
    bbox: tuple | None   # dimension line + text box; None for table rows


# ---- dimension collection --------------------------------------------------------

def _close(a, b):
    return abs(a - b) <= MATCH_TOL_MM


def _bbox(shape):
    bb = shape.bounding_box(optimal=True)
    return [bb.min.X, bb.min.Y, bb.min.Z, bb.max.X, bb.max.Y, bb.max.Z]


def _pattern_counts(features):
    counts = {}
    for f in features:
        if f["kind"] == "pattern":
            n = f["linear"]["countX"] * f["linear"].get("countY", 1) if "linear" in f else f["polar"]["count"]
            counts[f["of"]] = n
    return counts


def _feature_boxes(features):
    """Bounding boxes of each feature's own solid; None where it cannot be rebuilt here."""
    boxes, prior = {}, {}
    for f in features:
        if f["kind"] == "step" or (f["kind"] == "pattern" and f["of"] not in prior):
            boxes[f["id"]] = None
            continue
        solid = make_feature(f, Path("."), prior)
        prior[f["id"]] = solid
        boxes[f["id"]] = _bbox(solid)
    return boxes


def _linear(view_axis, fb, ext):
    """h/v Dim endpoints spanning a feature bbox along one model axis.

    The extension anchor is the feature side nearer the outline, so extension
    lines cross as little of the part as possible.
    """
    x0, y0, z0, x1, y1, z1 = fb
    if view_axis == "X":   # front, horizontal
        z = z0 if z0 - ext[2] <= ext[5] - z1 else z1
        return "front", "h", (x0, z), (x1, z)
    if view_axis == "Z":   # front, vertical
        x = x0 if x0 - ext[0] <= ext[3] - x1 else x1
        return "front", "v", (x, z0), (x, z1)
    x = x0 if x0 - ext[0] <= ext[3] - x1 else x1   # Y: top, vertical
    return "top", "v", (x, y0), (x, y1)


class _Dims:
    """Ordered dimension list that merges values drawn twice on one span."""

    def __init__(self):
        self.items = []

    @staticmethod
    def _span(d):
        if d.kind == "h":
            return (d.view, "h", round(min(d.a[0], d.b[0]), 4), round(max(d.a[0], d.b[0]), 4))
        if d.kind == "v":
            return (d.view, "v", round(min(d.a[1], d.b[1]), 4), round(max(d.a[1], d.b[1]), 4))
        return (d.view, d.kind, round(d.a[0], 4), round(d.a[1], 4), d.label)

    def add(self, d):
        if d.view != "table":
            key = self._span(d)
            for e in self.items:
                if e.view != "table" and self._span(e) == key:
                    self.merge(e, d)
                    return e
        self.items.append(d)
        return d

    @staticmethod
    def merge(existing, new):
        if (new.label[:1] in "ØR" and existing.label[:1] not in "ØR") or (existing.measured and not new.measured):
            existing.label = new.label
            existing.measured = new.measured
        if new.origin in MODEL_ORIGINS and existing.origin != "assumed":
            existing.origin = new.origin
            existing.name = new.name


def _fillet_dim(shape, r, origin="geometry"):
    """R leader on a visible fillet arc (a partial circle edge of radius r)."""
    best = None
    for e in shape.edges():
        if e.geom_type != GeomType.CIRCLE or e.is_closed or not _close(e.radius, r):
            continue
        pts = [e.position_at(t) for t in (0, 0.5, 1)]
        for prio, (view, axis, toward) in enumerate((("front", 1, -1), ("top", 2, 1), ("right", 0, 1))):
            coords = [(p.X, p.Y, p.Z)[axis] for p in pts]
            if max(coords) - min(coords) > 1e-6:
                continue
            c, m = e.arc_center, pts[1]
            mid = to_view(view, (m.X, m.Y, m.Z))
            key = (prio, -toward * coords[0], -(mid[0] + mid[1]))
            if best is None or key < best[0]:
                best = (key, view, to_view(view, (c.X, c.Y, c.Z)), mid)
    label = f"R{fmt_mm(r)}"
    if best is None:
        return Dim("table", "r", (), (), label, origin, "필렛 반경")
    return Dim(best[1], "r", best[2], best[3], label, origin, "필렛 반경")


def _feature_dims(f, fb, counts, ext):
    """Dims read from one feature's parameters: list of (Dim, value)."""
    kind, n = f["kind"], counts.get(f["id"], 1)
    many = f"{n}× " if n > 1 else ""
    if kind == "cylinder":
        r, (cx, cy, cz) = f["radiusMm"], f["centerMm"]
        lab = f"{many}Ø{fmt_mm(2 * r)}"
        if f["axis"] == "Z":
            d = Dim("front", "h", (cx - r, fb[2]), (cx + r, fb[2]), lab, "geometry", f"{f['id']} 지름")
        elif f["axis"] == "X":
            d = Dim("front", "v", (fb[0], cz - r), (fb[0], cz + r), lab, "geometry", f"{f['id']} 지름")
        else:
            d = Dim("top", "h", (cx - r, fb[1]), (cx + r, fb[1]), lab, "geometry", f"{f['id']} 지름")
        return [(d, 2 * r)]
    if kind == "hole":
        axis = f["direction"].lstrip("+-")
        view = {"Z": "top", "Y": "front", "X": "right"}[axis]
        c, r = to_view(view, f["at"]), f["diameterMm"] / 2
        lab = f"{many}Ø{fmt_mm(2 * r)}"
        return [(Dim(view, "dia", c, (c[0] + r, c[1]), lab, "geometry", f"{f['id']} 지름"), 2 * r)]
    if kind == "extrude":
        axis = {"XY": "Z", "XZ": "Y", "YZ": "X"}[f["profile"]["plane"]]
        view, k, a, b = _linear(axis, fb, ext)
        return [(Dim(view, k, a, b, fmt_mm(f["depthMm"]), "geometry", f"{f['id']} 깊이"), f["depthMm"])]
    if kind == "revolve":  # read off the solid: an arcs profile may bulge past its points
        rmax, z0, z1 = fb[3], fb[2], fb[5]
        return [
            (Dim("front", "h", (-rmax, z0), (rmax, z0), f"Ø{fmt_mm(2 * rmax)}", "geometry",
                 f"{f['id']} 최대 지름"), 2 * rmax),
            (Dim("front", "v", (-rmax, z0), (-rmax, z1), fmt_mm(z1 - z0), "geometry",
                 f"{f['id']} 높이"), z1 - z0),
        ]
    return []


def collect_dims(part, model, shape):
    """Dimensions for one part sheet (part=None: the assembly sheet).

    Overall W/D/H always; feature parameters (cylinder/hole Ø, fillet R,
    extrude depth, revolve Ø and height); model.dimensions for this part
    (part-less ones go to the single part, or to the assembly when there
    are several parts). An entry with a `feature` is drawn on that feature's
    span when one matches; every other entry is listed by name in the notes
    table, never merged into an overall span on value alone. An entry with a
    `span` gives that overall dimension its value and origin and is also
    listed by name in the table. Assumed values are labelled "(value)*".
    """
    ext = _bbox(shape)
    x0, y0, z0, x1, y1, z1 = ext
    dims = _Dims()
    spans = {
        "width": dims.add(Dim("front", "h", (x0, z0), (x1, z0), fmt_mm(x1 - x0, MEASURED_DECIMALS),
                              "geometry", "전체 폭", measured=True)),
        "depth": dims.add(Dim("top", "v", (x0, y0), (x0, y1), fmt_mm(y1 - y0, MEASURED_DECIMALS),
                              "geometry", "전체 깊이", measured=True)),
        "height": dims.add(Dim("front", "v", (x0, z0), (x0, z1), fmt_mm(z1 - z0, MEASURED_DECIMALS),
                               "geometry", "전체 높이", measured=True)),
    }
    by_feature, fboxes, features = {}, {}, []
    if part is not None and part["construction"] != "code":
        features = part["features"]
        fboxes = _feature_boxes(features)
        counts = _pattern_counts(features)
        for f in features:
            if fboxes[f["id"]] is None:
                continue
            by_feature[f["id"]] = [(dims.add(d), v) for d, v in _feature_dims(f, fboxes[f["id"]], counts, ext)]
        for fin in part.get("finish", []):
            origin = fin.get("origin", "geometry")
            if fin["kind"] == "fillet":
                dims.add(_fillet_dim(shape, fin["radiusMm"], origin))
            elif fin["kind"] == "chamfer":
                dims.add(Dim("table", "h", (), (), f"C{fmt_mm(fin['distanceMm'])}", origin,
                             f"모따기 chamfer {fin['edges']}"))
            else:
                dims.add(Dim("table", "h", (), (), fmt_mm(fin["thicknessMm"]), origin, "셸 두께 shell"))

    single = len(model["parts"]) == 1
    marked = []
    for md in model["dimensions"]:
        owner = md.get("part")
        mine = owner is None if part is None else owner == part["id"] or (owner is None and single)
        if not mine:
            continue
        value = md["valueMm"]
        new = Dim("table", "h", (), (), fmt_mm(value), md["origin"], md["label"])
        if "span" in md:  # the overall span carries this entry's value and marking; the table names it
            span = spans[md["span"]]
            span.label, span.origin, span.name, span.measured = new.label, new.origin, new.name, False
            marked.append(span)
            dims.add(new)
            continue
        fid, target = md.get("feature"), None
        if fid is not None:
            target = next((d for d, v in by_feature.get(fid, []) if _close(v, value)), None)
            fb = fboxes.get(fid)
            if target is None and fb is not None:
                sizes = {"X": fb[3] - fb[0], "Y": fb[4] - fb[1], "Z": fb[5] - fb[2]}
                axis = next((a for a in ("X", "Z", "Y") if _close(sizes[a], value)), None)
                if axis:
                    new.view, new.kind, new.a, new.b = _linear(axis, fb, ext)
        if target is not None:
            _Dims.merge(target, new)
        else:
            dims.add(new)

    for span in marked:  # the same span drawn in another view carries the same value and marking
        for d in dims.items:
            if d is not span and d.origin == "geometry" and _axis_extent(d) == _axis_extent(span):
                d.label = f"Ø{span.label}" if d.label.startswith("Ø") else span.label
                d.origin, d.measured = span.origin, False
    for d in dims.items:
        if d.origin == "assumed":
            d.label = f"({d.label})*"
    return dims.items


def _axis_extent(d):
    """(model axis, lo, hi) that an h/v dim in an orthographic view measures; None otherwise."""
    if d.view not in VIEW_AXES or d.kind not in ("h", "v"):
        return None
    k = 0 if d.kind == "h" else 1
    lo, hi = sorted((d.a[k], d.b[k]))
    return VIEW_AXES[d.view][k], round(lo, 4), round(hi, 4)


def _pattern_moves(f):
    """Point transforms for the copies a pattern makes (identity excluded)."""
    if "linear" in f:
        lin = f["linear"]
        nx, ny = lin["countX"], lin.get("countY", 1)
        sx, sy, sz = lin["stepMm"]
        offs = [(i * sx, j * sy, 0.0) if ny > 1 else (i * sx, i * sy, i * sz)
                for j in range(ny) for i in range(nx) if i or j]
        return [lambda p, o=o: tuple(p[k] + o[k] for k in range(3)) for o in offs], None
    pol = f["polar"]
    n, total = pol["count"], pol["totalDeg"]
    step = total / n if total == 360 else total / (n - 1)
    a, b = {"X": (1, 2), "Y": (2, 0), "Z": (0, 1)}[pol["axis"]]

    def rot(p, deg):
        c, s_ = math.cos(math.radians(deg)), math.sin(math.radians(deg))
        q = list(p)
        q[a], q[b] = p[a] * c - p[b] * s_, p[a] * s_ + p[b] * c
        return tuple(q)

    return [lambda p, d=k * step: rot(p, d) for k in range(1, n)], pol["axis"]


def center_lines(part, shape):
    """Axis lines of revolves, cylinders and holes (and their pattern copies).

    Returns {view: [((u0, v0), (u1, v1)), ...]} for views where the axis lies
    in the view plane; circles seen end-on get centre marks from the view.
    """
    if part is None or part["construction"] == "code":
        return {}
    ext = _bbox(shape)
    axes = {}
    for f in part["features"]:
        k = f["kind"]
        if k == "revolve":
            fb = _bbox(make_feature(f, Path("."), {}))
            axes[f["id"]] = [((0.0, 0.0, 0.0), "Z", fb[2], fb[5])]
        elif k == "cylinder":
            i = "XYZ".index(f["axis"])
            axes[f["id"]] = [(tuple(f["centerMm"]), f["axis"], f["centerMm"][i], f["centerMm"][i] + f["heightMm"])]
        elif k == "hole":
            axis = f["direction"].lstrip("+-")
            i = "XYZ".index(axis)
            at = f["at"][i]
            if f.get("through"):
                lo, hi = ext[i], ext[i + 3]
            else:
                end = at - f["depthMm"] if f["direction"].startswith("-") else at + f["depthMm"]
                lo, hi = sorted((at, end))
            axes[f["id"]] = [(tuple(f["at"]), axis, lo, hi)]
        elif k == "pattern" and f["of"] in axes:
            moves, polar_axis = _pattern_moves(f)
            copies = []
            for c, axis, lo, hi in axes[f["of"]]:
                if polar_axis not in (None, axis):
                    continue  # a rotated copy would tilt the axis; leave it unmarked
                i = "XYZ".index(axis)
                for move in moves:
                    q = move(c)
                    copies.append((q, axis, lo + q[i] - c[i], hi + q[i] - c[i]))
            axes[f["id"]] = copies
    out = {}
    in_plane = {"X": ("front", "top"), "Y": ("top", "right"), "Z": ("front", "right")}
    for c, axis, lo, hi in (a for group in axes.values() for a in group):
        i = "XYZ".index(axis)
        p0, p1 = list(c), list(c)
        p0[i], p1[i] = lo, hi
        for view in in_plane[axis]:
            seg = (to_view(view, p0), to_view(view, p1))
            if seg not in out.setdefault(view, []):
                out[view].append(seg)
    return out


# ---- layout ------------------------------------------------------------------------

def to_paper(box, p):
    u0, v0, _u1, v1 = box["extent"]
    s, (px, py) = box["scale"], box["origin"]
    return (px + (p[0] - u0) * s, py + (v1 - p[1]) * s)


def paper_box(box):
    u0, v0, u1, v1 = box["extent"]
    s, (px, py) = box["scale"], box["origin"]
    return (px, py, px + (u1 - u0) * s, py + (v1 - v0) * s)


def _overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _seg_hits_box(seg, box):
    """Liang-Barsky clip: does the segment pass through the rectangle?"""
    (x1, y1), (x2, y2) = seg
    t0, t1 = 0.0, 1.0
    dx, dy = x2 - x1, y2 - y1
    for p, q in ((-dx, x1 - box[0]), (dx, box[2] - x1), (-dy, y1 - box[1]), (dy, box[3] - y1)):
        if p == 0:
            if q < 0:
                return False
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return False
    return True


def _collides(g, placed, outlines):
    if any(_overlap(g["text_box"], o) for o in outlines):
        return True
    for p in placed:
        o = p.geom
        if _overlap(g["bbox"], p.bbox):
            return True
        if any(_seg_hits_box(s, o["text_box"]) for s in g["guides"]):
            return True
        if any(_seg_hits_box(s, g["text_box"]) for s in o["guides"]):
            return True
    return False


def _linear_candidates(d, box):
    pa, pb = to_paper(box, d.a), to_paper(box, d.b)
    gx0, gy0, gx1, gy1 = paper_box(box)
    u0, v0, u1, v1 = box["extent"]
    if d.kind == "h":
        pref = "below" if d.a[1] <= (v0 + v1) / 2 + 1e-9 else "above"
        sides = (pref, "above" if pref == "below" else "below")
    else:
        pref = "left" if d.a[0] <= (u0 + u1) / 2 + 1e-9 else "right"
        sides = (pref, "right" if pref == "left" else "left")
    for side in sides:
        for tier in range(MAX_PUSHES + 1):
            off = FIRST_OFFSET + TIER_STEP * tier
            if d.kind == "h":
                y = gy1 + off if side == "below" else gy0 - off
                yield side, tier, dim_h(pa[0], pb[0], y, pa[1], pb[1], d.label)
            else:
                x = gx0 - off if side == "left" else gx1 + off
                yield side, tier, dim_v(pa[1], pb[1], x, pa[0], pb[0], d.label)


def _leader_candidates(d, box):
    c, edge = to_paper(box, d.a), to_paper(box, d.b)
    gx0, gy0, gx1, gy1 = paper_box(box)
    if d.kind == "dia":
        rp = math.dist(c, edge)
        ref = ((gx0 + gx1) / 2, (gy0 + gy1) / 2)
    else:
        ref = c
    sx = 1 if (edge if d.kind == "r" else c)[0] >= ref[0] else -1
    sy = 1 if (edge if d.kind == "r" else c)[1] > ref[1] else -1
    for qx, qy in ((sx, sy), (-sx, sy), (sx, -sy), (-sx, -sy)):
        for tier in range(MAX_PUSHES + 1):
            if d.kind == "dia":
                p = (c[0] + qx * rp / math.sqrt(2), c[1] + qy * rp / math.sqrt(2))
            else:
                p = edge
            ey = gy1 + FIRST_OFFSET + TIER_STEP * tier if qy > 0 else gy0 - FIRST_OFFSET - TIER_STEP * tier
            ex = p[0] + qx * abs(ey - p[1])
            yield "leader", tier, radius_leader(p[0], p[1], ex, ey, d.label)


def _span_len(d):
    return abs(d.b[0] - d.a[0]) if d.kind == "h" else abs(d.b[1] - d.a[1])


def layout_dims(dims, view_boxes):
    """Place dims without overlaps; returns Placed in input-independent order.

    view_boxes: {view: {"extent": [u0, v0, u1, v1], "scale": paper mm per
    model mm, "origin": paper (x, y) of the outline's top-left}}.
    Linear dims go below/left (or above/right) the outline, short spans
    first so longer ones stack outside; each collision pushes a dim
    TIER_STEP further out, up to MAX_PUSHES, then the opposite side, then
    the notes table. Ø/R leaders run at 45° to a shoulder outside the view.
    """
    outlines = [paper_box(b) for b in view_boxes.values()]
    order = {v: i for i, v in enumerate(VIEWS)}
    linear = sorted((d for d in dims if d.kind in "hv" and d.view in view_boxes),
                    key=lambda d: (order.get(d.view, 9), _span_len(d)))
    leaders = [d for d in dims if d.kind in ("dia", "r") and d.view in view_boxes]
    placed, table = [], []
    for d in linear + leaders:
        box = view_boxes[d.view]
        cands = _linear_candidates(d, box) if d.kind in "hv" else _leader_candidates(d, box)
        for side, tier, g in cands:
            if not _collides(g, placed, outlines):
                placed.append(Placed(d, side, tier, g, g["bbox"]))
                break
        else:
            table.append(Placed(d, "table", 0, None, None))
    table += [Placed(d, "table", 0, None, None) for d in dims
              if d.view not in view_boxes and d.view != "iso"]
    return placed + table


def _margins(placed, box):
    gx0, gy0, gx1, gy1 = paper_box(box)
    xs0, ys0, xs1, ys1 = [gx0], [gy0], [gx1], [gy1]
    for p in placed:
        if p.bbox is None:
            continue
        pts = [(p.bbox[0], p.bbox[1]), (p.bbox[2], p.bbox[3])]
        pts += [pt for seg in p.geom["guides"] + p.geom["lines"] for pt in seg]
        xs0 += [x for x, _ in pts]
        xs1 += [x for x, _ in pts]
        ys0 += [y for _, y in pts]
        ys1 += [y for _, y in pts]
    return (gx0 - min(xs0), max(xs1) - gx1, gy0 - min(ys0), max(ys1) - gy1 + TITLE_SPACE)


def _grid(views, dims, projection, scale, iso_factor):
    """Positions for each view at `scale`: (view_boxes, width, height)."""
    cells, size, marg = GRID[projection], {}, {}
    for v in VIEWS:
        s = scale * (iso_factor if v == "iso" else 1.0)
        u0, v0, u1, v1 = views[v]["extent"]
        size[v] = ((u1 - u0) * s, (v1 - v0) * s)
        box = {"extent": views[v]["extent"], "scale": s, "origin": (0.0, 0.0)}
        marg[v] = _margins(layout_dims([d for d in dims if d.view == v], {v: box}), box)
    rows = sorted({rc[0] for rc in cells.values()})
    cols = sorted({rc[1] for rc in cells.values()})
    in_col = {c: [v for v in VIEWS if cells[v][1] == c] for c in cols}
    in_row = {r: [v for v in VIEWS if cells[v][0] == r] for r in rows}
    left = {c: max(marg[v][0] for v in in_col[c]) for c in cols}
    body_w = {c: max(size[v][0] + marg[v][1] for v in in_col[c]) for c in cols}
    top = {r: max(marg[v][2] for v in in_row[r]) for r in rows}
    body_h = {r: max(size[v][1] + marg[v][3] for v in in_row[r]) for r in rows}
    col_x = dict(zip(cols, itertools.accumulate([0.0] + [left[c] + body_w[c] + VIEW_GAP for c in cols])))
    row_y = dict(zip(rows, itertools.accumulate([0.0] + [top[r] + body_h[r] + VIEW_GAP for r in rows])))
    boxes = {}
    for v in VIEWS:
        r, c = cells[v]
        if v == "iso":  # centred in its cell; no projection alignment to keep
            x = col_x[c] + (left[c] + body_w[c] - size[v][0]) / 2
            y = row_y[r] + (top[r] + body_h[r] - size[v][1] - TITLE_SPACE) / 2
        else:
            x, y = col_x[c] + left[c], row_y[r] + top[r]
        s = scale * (iso_factor if v == "iso" else 1.0)
        boxes[v] = {"extent": views[v]["extent"], "scale": s, "origin": (x, y)}
    width = sum(left[c] + body_w[c] for c in cols) + VIEW_GAP * (len(cols) - 1)
    height = sum(top[r] + body_h[r] for r in rows) + VIEW_GAP * (len(rows) - 1)
    r, c = cells["iso"]
    iso_cell = (left[c] + body_w[c], top[r] + body_h[r] - TITLE_SPACE)
    return boxes, width, height, iso_cell


def _fit_iso(views, dims, projection, scale, area):
    """Grid sized by the three orthographic views alone, with the iso view
    shrunk into its cell plus the spare sheet room (or left out when it would
    fall below ISO_MIN_FACTOR). None when the orthographic views do not fit."""
    aw, ah = area
    _boxes, w, h, (cw, ch) = _grid(views, dims, projection, scale, 0.0)
    if w > aw or h > ah:
        return None
    u0, v0, u1, v1 = views["iso"]["extent"]
    factor = min(1.0, (cw + aw - w) / ((u1 - u0) * scale), (ch + ah - h) / ((v1 - v0) * scale))
    while factor >= ISO_MIN_FACTOR:
        boxes, w, h, _cell = _grid(views, dims, projection, scale, factor)
        if w <= aw and h <= ah:
            return boxes, w, h
        factor *= 0.9
    boxes, w, h, _cell = _grid(views, dims, projection, scale, 0.0)
    del boxes["iso"]
    return boxes, w, h


def arrange(views, dims, projection, name="sheet"):
    """Pick the largest standard scale at which the front, top and right views
    and their dims fit the drawing area.

    The isometric view is drawn at ISO_FACTORS of the sheet scale when that
    fits; otherwise it shrinks further into the room left over (and is left
    out below ISO_MIN_FACTOR), so it never forces a smaller scale.
    Returns (view_boxes, placed, scale_label); view_boxes has no "iso" entry
    when it was left out. ModelError when even the smallest scale does not fit.
    """
    ax0, ay0, ax1, ay1 = DRAW_AREA
    area = (ax1 - ax0, ay1 - ay0)
    for scale, label in SCALES:
        fitted = None
        for iso in ISO_FACTORS:
            boxes, w, h, _cell = _grid(views, dims, projection, scale, iso)
            if w <= area[0] and h <= area[1]:
                fitted = boxes, w, h
                break
        fitted = fitted or _fit_iso(views, dims, projection, scale, area)
        if fitted:
            boxes, w, h = fitted
            break
    else:
        raise ModelError(f"{name}: part too large to fit a drawing sheet at {SCALES[-1][1]}")
    dx, dy = ax0 + (ax1 - ax0 - w) / 2, ay0 + (ay1 - ay0 - h) / 2
    for b in boxes.values():
        b["origin"] = (b["origin"][0] + dx, b["origin"][1] + dy)
    return boxes, layout_dims(dims, boxes), label


# ---- stage -------------------------------------------------------------------------

def _notes(model, placed, extra=()):
    rows = list(extra)
    if any(p.dim.origin == "assumed" for p in placed):
        rows.append("* 가정값: (값)* 으로 표시한 치수는 스케치에 없어 가정한 값")
    rows += [f"가정: {a}" for a in model.get("assumptions", [])]
    return rows


def _sheet(stage, sheet_id, title, subtitle, part, model, shape, projection):
    views = {v: project_view(shape, v) for v in VIEWS}
    dims = collect_dims(part, model, shape)
    boxes, placed, scale = arrange(views, dims, projection, sheet_id)
    extra = []
    if part is None:
        extra.append(f"부품 {len(model['parts'])}개: 품번·품명·재질은 부품 배치도 (drawing/assembly-parts) 참조")
    axes = center_lines(part, shape)
    names = [sheet_id] + [_notes_sheet_id(sheet_id, k) for k in range(1, 100)]
    svgs = render_sheet(title, {v: {"proj": views[v], "box": boxes[v], "axes": axes.get(v, [])}
                                for v in VIEWS if v in boxes}, placed,
                        _notes(model, placed, extra), projection, scale=scale, subtitle=subtitle,
                        notes_sheet_name=lambda k: f"{names[k]}.svg")
    for name, svg in zip(names, svgs):
        if (stage / f"{name}.svg").exists():
            raise ModelError(f"drawing/{name}.svg: sheet name used twice (a part id clashes with a notes sheet)")
        (stage / f"{name}.svg").write_text(svg, encoding="utf-8")
        svg_to_pdf(svg, stage / f"{name}.pdf")
    entry = {"id": sheet_id, "svg": f"drawing/{sheet_id}.svg", "pdf": f"drawing/{sheet_id}.pdf", "scale": scale}
    top = boxes["top"]  # where the top view sits on the sheet (the viewer grows the model out of it)
    entry["topView"] = {"origin": [round(top["origin"][0], 4), round(top["origin"][1], 4)],
                        "scale": top["scale"], "extent": [round(v, 6) for v in top["extent"]]}
    if len(svgs) > 1:
        entry["notesSheets"] = [{"svg": f"drawing/{n}.svg", "pdf": f"drawing/{n}.pdf"} for n in names[1:len(svgs)]]
    return entry


def _parts_sheet(stage, model, manifest, shapes, assembly, projection):
    """drawing/assembly-parts.svg/.pdf and its manifest entry (see partsheet.py)."""
    from .bom import bom_rows
    from .partsheet import SHEET_ID, parts_sheet

    bounds = {pid: manifest["parts"][pid]["boundsMm"] for pid in shapes}
    volumes = {pid: manifest["parts"][pid]["volumeMm3"] for pid in shapes}
    bb = assembly.bounding_box()
    whole = {"min": [bb.min.X, bb.min.Y, bb.min.Z], "max": [bb.max.X, bb.max.Y, bb.max.Z]}
    svg, record, scale = parts_sheet(model, bom_rows(model, bounds), shapes, bounds, volumes, whole, projection)
    if (stage / f"{SHEET_ID}.svg").exists():
        raise ModelError(f"drawing/{SHEET_ID}.svg: sheet name used twice")
    (stage / f"{SHEET_ID}.svg").write_text(svg, encoding="utf-8")
    svg_to_pdf(svg, stage / f"{SHEET_ID}.pdf")
    return {"id": SHEET_ID, "svg": f"drawing/{SHEET_ID}.svg", "pdf": f"drawing/{SHEET_ID}.pdf", "scale": scale,
            "parts": record}


def _notes_sheet_id(sheet_id, k):
    return f"{sheet_id}-notes" if k == 1 else f"{sheet_id}-notes-{k}"


def run_drawing(out, projection="third"):
    """Write OUT/drawing/ (built in drawing.tmp, then renamed) and update the manifest."""
    out = Path(out)
    if projection not in PROJECTIONS:
        raise ModelError("projection: must be first or third")
    try:
        manifest = load_manifest(out)
        model = json.loads((out / "model.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ModelError(f"{out}: not a build output folder ({e})") from e
    if manifest.get("stages", {}).get("build") != "ok":
        raise ModelError(f"{out}: build stage has not completed")
    final, stage = out / "drawing", out / "drawing.tmp"
    for p in (final, stage):
        if p.exists():
            raise ModelError(f"{p}: already exists")
    shapes = load_part_shapes(out)
    multi = len(shapes) >= 2
    if multi and "assembly" in shapes:
        raise ModelError("parts: id 'assembly' is reserved for the assembly drawing")
    parts = {p["id"]: p for p in model["parts"]}
    stage.mkdir()
    try:
        sheets = [_sheet(stage, pid, model["name"], pid, parts[pid], model, shape, projection)
                  for pid, shape in shapes.items()]
        if multi:
            placed = [place_shape(shapes[pl["part"]], pl) for pl in manifest["assembly"]["placement"]]
            assembly = Compound(placed)
            sheets.append(_sheet(stage, "assembly", model["name"], "조립 ASSEMBLY", None, model,
                                 assembly, projection))
            sheets.append(_parts_sheet(stage, model, manifest, shapes, assembly, projection))
        stage.rename(final)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    manifest["stages"]["drawing"] = "ok"
    manifest["drawing"] = {"projection": projection, "sheets": sheets}
    try:
        write_manifest(out, manifest)
    except BaseException:  # never leave drawing/ behind without its manifest entry
        shutil.rmtree(final, ignore_errors=True)
        raise
    return manifest
