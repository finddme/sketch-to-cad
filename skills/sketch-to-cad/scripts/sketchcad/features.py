"""Build one validated feature dict into a build123d solid.

`make_feature(feature, base_dir, prior)` returns a Solid (or, for `pattern`,
a Compound of the copies). Every kind except `pattern` returns exactly one
valid solid with volume > 0. Errors are ModelError "feature <id>: <cause>".
"""
import hashlib
import math
from pathlib import Path

from build123d import (
    Axis, Edge, Face, GeomType, Location, Plane, Solid, Vector, Wire, import_step,
)

from .errors import ModelError

ARC_CHORDS = 16
EPS = 1e-9

# plane -> (map 2D [u, v] to 3D, extrusion direction)
_PLANES = {
    "XY": (lambda u, v: (u, v, 0.0), (0, 0, 1)),
    "XZ": (lambda u, v: (u, 0.0, v), (0, 1, 0)),
    "YZ": (lambda u, v: (0.0, u, v), (1, 0, 0)),
}
_AXIS_VEC = {"X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1)}
_AXIS_OBJ = {"X": Axis.X, "Y": Axis.Y, "Z": Axis.Z}
_LOFT_PLANE = {"XY": ((1, 0, 0), (0, 0, 1)), "YZ": ((0, 1, 0), (1, 0, 0)), "XZ": ((1, 0, 0), (0, -1, 0))}
THROUGH_MIN_LENGTH_MM = 1000.0  # through-hole tools are at least this long, centred on `at`
AXIS_SNAP_MM = 0.01  # revolve arcs ending on the axis are re-centred on it when this close
LOFT_PLANE_TOL_MM = 0.01  # polygon loft sections may be this far off planar (rounding); they are projected


def make_feature(feature, base_dir, prior):
    """Return the solid for one validated feature dict.

    `prior` maps earlier feature ids to their solids (used by `pattern`).
    Conventions:
    - cylinder: centerMm is the center of the base circle; it extends
      +axis by heightMm.
    - extrude: profile plane XY/XZ/YZ extrudes toward +Z/+Y/+X; [u, v] are the
      plane's first and second axes (XY: x,y; XZ: x,z; YZ: y,z).
    - revolve: `profileRzMm` polygon or `profile` arcs ({start, segments} in
      [r, z]) in the XZ half plane, revolved 360 degrees about Z.
    - hole: returns the cutting tool. `at` is the entry point on the surface,
      `direction` the drilling direction. Through holes are a tool centered on
      `at`, 2 x the diagonal of the earlier features' bounds (with `at`) plus
      10 mm long, at least 1000 mm; blind holes run depthMm from `at`; a counterbore is a
      wider cylinder of counterbore.depthMm from `at`, fused with the hole.
    - pattern: returns the fuse of the copies only (not the original).
      Disjoint copies give a multi-solid Compound; that is fine for a tool.
    """
    fid = feature["id"]
    kind = feature.get("kind")
    try:
        if kind == "pattern":
            return _pattern(feature, prior)
        if kind == "hole":
            return _check_solid(_hole(feature, prior), fid)
        solid = _BUILDERS[kind](feature, Path(base_dir))
        return _check_solid(solid, fid)
    except ModelError:
        raise
    except Exception as exc:  # OCC/build123d failures carry little context
        raise ModelError(f"feature {fid}: {kind} could not be built ({type(exc).__name__}: {exc})") from exc


def _fail(fid, cause):
    raise ModelError(f"feature {fid}: {cause}")


def _check_solid(shape, fid):
    solids = shape.solids()
    if len(solids) != 1:
        _fail(fid, f"expected exactly one solid, got {len(solids)}")
    solid = solids[0]
    if not solid.is_valid or solid.volume <= 0:
        _fail(fid, "result is not a valid solid with positive volume")
    return solid


def _box(f, _base):
    return Solid.make_box(*f["sizeMm"], Plane(origin=f["originMm"]))


def _sphere(f, _base):
    return Solid.make_sphere(f["radiusMm"], Plane(origin=f["centerMm"]))


def _cylinder_along(origin, direction, radius, length):
    return Solid.make_cylinder(radius, length, Plane(origin=origin, z_dir=direction))


def _cylinder(f, _base):
    return _cylinder_along(f["centerMm"], _AXIS_VEC[f["axis"]], f["radiusMm"], f["heightMm"])


def _revolve(f, _base):
    """[r, z] profile (polygon `profileRzMm` or arcs `profile`) revolved about Z."""
    if "profileRzMm" in f:
        wire = Wire.make_polygon([(r, 0, z) for r, z in f["profileRzMm"]], close=True)
        return Solid.revolve(Face(wire), 360, Axis.Z)
    wire, poly = _arcs_wire(_snap_axis_arcs(f["profile"], f["id"]), _PLANES["XZ"][0])
    for edge in wire.edges():
        if edge.geom_type != GeomType.CIRCLE:
            continue  # lines between points with r >= 0 stay at r >= 0
        c, radius = edge.arc_center, edge.radius
        leftmost = Vector(c.X - radius, 0, c.Z)
        if c.X - radius < -1e-6 and edge.distance_to(leftmost) < 1e-6:
            _fail(f["id"], f"profile crosses the revolve axis (an arc reaches r = {c.X - radius:.3g})")
    if _self_intersects(poly):
        _fail(f["id"], "profile self-intersects")
    return Solid.revolve(Face(wire), 360, Axis.Z)


def _onto_plane(points, fid, number):
    """Project a polygon section onto its best-fit plane (Newell normal through
    the centroid). Rounding noise up to LOFT_PLANE_TOL_MM is absorbed; more is
    a ModelError, since OCC cannot cap a non-planar section."""
    n = [0.0, 0.0, 0.0]
    for (x0, y0, z0), (x1, y1, z1) in zip(points, points[1:] + points[:1]):
        n[0] += (y0 - y1) * (z0 + z1)
        n[1] += (z0 - z1) * (x0 + x1)
        n[2] += (x0 - x1) * (y0 + y1)
    length = math.sqrt(sum(c * c for c in n))
    if length <= EPS:
        _fail(fid, f"loft section {number} has no area")
    n = [c / length for c in n]
    c = [sum(p[k] for p in points) / len(points) for k in range(3)]
    dev = [sum((p[k] - c[k]) * n[k] for k in range(3)) for p in points]
    worst = max(abs(d) for d in dev)
    if worst > LOFT_PLANE_TOL_MM:
        _fail(fid, f"loft section {number} is not planar (max deviation {worst:.3g} mm); "
                   "put all its points on one plane")
    return [tuple(p[k] - d * n[k] for k in range(3)) for p, d in zip(points, dev)]


def _circle_center(a, b, c):
    """Centre of the circle through three [u, v] points (not collinear)."""
    d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
    sa, sb, sc = (a[0] ** 2 + a[1] ** 2), (b[0] ** 2 + b[1] ** 2), (c[0] ** 2 + c[1] ** 2)
    return ((sa * (b[1] - c[1]) + sb * (c[1] - a[1]) + sc * (a[1] - b[1])) / d,
            (sa * (c[0] - b[0]) + sb * (a[0] - c[0]) + sc * (b[0] - a[0])) / d)


def _snap_axis_arcs(arcs, fid):
    """Arcs that end on the axis (r = 0) with their centre within AXIS_SNAP_MM
    of it are re-centred exactly on the axis, so the revolve is a true sphere
    cap. A rounded through-point otherwise leaves the centre a hair off the
    axis and OCC makes a surface that pinches at the pole and fails the STEP
    check."""
    cur, segs = arcs["start"], []
    for i, seg in enumerate(arcs["segments"]):
        to = seg["to"]
        if "through" in seg and (abs(cur[0]) <= EPS) != (abs(to[0]) <= EPS):
            cu, cv = _circle_center(cur, seg["through"], to)
            if 0 < abs(cu) <= AXIS_SNAP_MM:
                (r1, z1), z2 = (to, cur[1]) if abs(cur[0]) <= EPS else (cur, to[1])
                if abs(z1 - z2) <= EPS:
                    _fail(fid, f"profile.segments[{i}]: an arc that starts or ends on the axis cannot end at "
                               "the same height (its centre would sit on the axis only with zero radius)")
                cz = (r1 ** 2 + z1 ** 2 - z2 ** 2) / (2 * (z1 - z2))  # centre (0, cz): equidistant ends
                radius = abs(z2 - cz)
                t = seg["through"]
                ang = math.atan2(t[1] - cz, t[0])
                seg = {"to": to, "through": [radius * math.cos(ang), cz + radius * math.sin(ang)]}
        segs.append(seg)
        cur = to
    return {"start": arcs["start"], "segments": segs}


def _loft(f, _base):
    wires = []
    for i, s in enumerate(f["sections"]):
        if s["kind"] == "polygon":
            pts = _onto_plane([tuple(p) for p in s["pointsMm"]], f["id"], i + 1)
            wires.append(Wire.make_polygon(pts, close=True))
        else:
            x_dir, normal = _LOFT_PLANE[s["plane"]]
            plane = Plane(origin=s["originMm"], x_dir=x_dir, z_dir=normal)
            wires.append(Wire.make_ellipse(s["radiusUMm"], s["radiusVMm"], plane))
    return Solid.make_loft(wires, ruled=f["ruled"])


def _step(f, base_dir):
    path = (base_dir / f["file"]).resolve()
    if hashlib.sha256(path.read_bytes()).hexdigest() != f["sha256"].lower():
        _fail(f["id"], f"sha256 of {f['file']!r} does not match the file")
    shape = import_step(path)
    if not shape.is_valid:
        _fail(f["id"], f"STEP file {f['file']!r} is not a valid shape")
    return shape  # _check_solid enforces exactly one solid


# ---- extrude -------------------------------------------------------------

def _seg_intersect(p1, p2, p3, p4):
    """True if closed segments p1p2 and p3p4 share any point."""
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    def on_seg(a, b, c):  # c collinear with ab: is it within ab?
        return (min(a[0], b[0]) - EPS <= c[0] <= max(a[0], b[0]) + EPS
                and min(a[1], b[1]) - EPS <= c[1] <= max(a[1], b[1]) + EPS)

    d1, d2 = cross(p3, p4, p1), cross(p3, p4, p2)
    d3, d4 = cross(p1, p2, p3), cross(p1, p2, p4)
    if ((d1 > EPS and d2 < -EPS) or (d1 < -EPS and d2 > EPS)) and \
       ((d3 > EPS and d4 < -EPS) or (d3 < -EPS and d4 > EPS)):
        return True
    return ((abs(d1) <= EPS and on_seg(p3, p4, p1)) or (abs(d2) <= EPS and on_seg(p3, p4, p2))
            or (abs(d3) <= EPS and on_seg(p1, p2, p3)) or (abs(d4) <= EPS and on_seg(p1, p2, p4)))


def _self_intersects(poly):
    """poly: closed polygon vertices (no repeated last point). O(n^2)."""
    n = len(poly)
    for i in range(n):
        a1, a2 = poly[i], poly[(i + 1) % n]
        for j in range(i + 1, n):
            if j == i + 1 or (i == 0 and j == n - 1):
                # adjacent edges share a vertex; only a fold-back overlap is invalid
                b1, b2 = poly[j], poly[(j + 1) % n]
                shared = a2 if j == i + 1 else a1
                a_far = a1 if shared is a2 else a2
                b_far = b2 if shared is b1 else b1
                cr = (a_far[0] - shared[0]) * (b_far[1] - shared[1]) - (a_far[1] - shared[1]) * (b_far[0] - shared[0])
                dot = (a_far[0] - shared[0]) * (b_far[0] - shared[0]) + (a_far[1] - shared[1]) * (b_far[1] - shared[1])
                if abs(cr) <= EPS and dot > 0:
                    return True
                continue
            if _seg_intersect(a1, a2, poly[j], poly[(j + 1) % n]):
                return True
    return False


def _arcs_wire(arcs, to3d):
    """Closed Wire from line / three-point-arc segments, plus a polyline sample."""
    cur = tuple(arcs["start"])
    start = cur
    segs = list(arcs["segments"])
    if math.dist(segs[-1]["to"], start) > EPS:
        segs.append({"to": list(start)})
    edges, poly = [], [start]
    for seg in segs:
        to = tuple(seg["to"])
        if "through" in seg:
            edge = Edge.make_three_point_arc(to3d(*cur), to3d(*seg["through"]), to3d(*to))
            for k in range(1, ARC_CHORDS + 1):
                p = edge.position_at(k / ARC_CHORDS)
                poly.append(_uv(p, to3d))
        else:
            edge = Edge.make_line(to3d(*cur), to3d(*to))
            poly.append(to)
        edges.append(edge)
        cur = to
    poly.pop()  # closing point equals start
    return Wire(edges), poly


def _uv(point, to3d):
    """Invert to3d for a 3D point on the profile plane."""
    probe = to3d(1.0, 2.0)
    # to3d only places u, v on two fixed world axes; read them back by position
    ix = [i for i, c in enumerate(probe) if c == 1.0][0]
    iy = [i for i, c in enumerate(probe) if c == 2.0][0]
    p = (point.X, point.Y, point.Z)
    return (p[ix], p[iy])


def _extrude(f, _base):
    fid, prof = f["id"], f["profile"]
    to3d, direction = _PLANES[prof["plane"]]
    if "points" in prof:
        poly = [tuple(p) for p in prof["points"]]
        wire = Wire.make_polygon([to3d(*p) for p in poly], close=True)
    elif "circle" in prof:
        c = prof["circle"]
        plane = Plane(origin=to3d(*c["centerMm"]), z_dir=direction)
        wire, poly = Wire.make_circle(c["radiusMm"], plane), None
    else:
        wire, poly = _arcs_wire(prof["arcs"], to3d)
    if poly is not None and _self_intersects(poly):
        _fail(fid, "profile self-intersects")
    d = f["depthMm"]
    return Solid.extrude(Face(wire), Vector(*direction) * d)


# ---- hole ----------------------------------------------------------------

def _through_length(at, prior):
    """Long enough to cross everything built so far from `at`, in either direction."""
    lo, hi = list(at), list(at)
    for solid in prior.values():
        bb = solid.bounding_box()
        lo = [min(a, b) for a, b in zip(lo, (bb.min.X, bb.min.Y, bb.min.Z))]
        hi = [max(a, b) for a, b in zip(hi, (bb.max.X, bb.max.Y, bb.max.Z))]
    return max(THROUGH_MIN_LENGTH_MM, 2 * math.dist(lo, hi) + 10)


def _hole(f, prior):
    d = f["direction"]
    sign = -1 if d.startswith("-") else 1
    vec = tuple(sign * c for c in _AXIS_VEC[d.lstrip("+-")])
    at = Vector(*f["at"])
    r = f["diameterMm"] / 2
    if f.get("through"):
        length = _through_length(f["at"], prior)
        start = at - Vector(*vec) * (length / 2)
        tool = _cylinder_along(tuple(start), vec, r, length)
    else:
        tool = _cylinder_along(tuple(at), vec, r, f["depthMm"])
    cb = f.get("counterbore")
    if cb:
        tool = tool.fuse(_cylinder_along(tuple(at), vec, cb["diameterMm"] / 2, cb["depthMm"]))
        tool = tool.clean()
    return tool


# ---- pattern -------------------------------------------------------------

def _pattern(f, prior):
    fid = f["id"]
    src = prior.get(f["of"])
    if src is None:
        _fail(fid, f"pattern source {f['of']!r} was not built before this feature")
    try:
        copies = []
        if "linear" in f:
            lin = f["linear"]
            nx, ny = lin["countX"], lin.get("countY", 1)
            sx, sy, sz = lin["stepMm"]
            if ny > 1 and sz != 0:
                _fail(fid, "stepMm[2] must be 0 when countY > 1 (grid pattern lies in the XY plane)")
            for j in range(ny):
                for i in range(nx):
                    if i == 0 and j == 0:
                        continue
                    off = (i * sx, j * sy, 0.0) if ny > 1 else (i * sx, i * sy, i * sz)
                    copies.append(src.moved(Location(off)))
        else:
            pol = f["polar"]
            n, total = pol["count"], pol["totalDeg"]
            step = total / n if total == 360 else total / (n - 1)
            axis = _AXIS_OBJ[pol["axis"]]
            for k in range(1, n):
                copies.append(src.rotate(axis, k * step))
        fused = copies[0]
        for c in copies[1:]:
            fused = fused.fuse(c)
        fused = fused.clean()
        if not fused.solids() or fused.volume <= 0:
            _fail(fid, "pattern produced no solids")
        return fused
    except ModelError:
        raise
    except Exception as exc:
        raise ModelError(f"feature {fid}: pattern could not be built ({type(exc).__name__}: {exc})") from exc


_BUILDERS = {
    "box": _box, "cylinder": _cylinder, "sphere": _sphere, "revolve": _revolve,
    "loft": _loft, "step": _step, "extrude": _extrude,
}
