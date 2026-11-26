"""Orthographic views of a solid by OCC hidden-line removal.

The camera sits far out along the view direction; the HLR call mirrors
build123d's `project_to_viewport` but keeps smooth (tangent) edges apart so
the sheet can draw them thin, and drops hidden tangent edges.

View coordinates are model millimetres along the view's screen axes, so
dimensions can be computed straight from model values:
    front (looking +Y): (X, Z)    top (looking -Z): (X, Y)
    right (looking -X): (Y, Z)    iso: screen axes of the (1,-1,1) camera
"""
import math

from build123d import Edge, GeomType, Vector
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepLib import BRepLib
from OCP.gp import gp_Ax1, gp_Ax2
from OCP.HLRAlgo import HLRAlgo_Projector
from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS

# view -> (direction from the part toward the eye, screen up)
CAMERAS = {
    "front": ((0, -1, 0), (0, 0, 1)),
    "top": ((0, 0, 1), (0, 1, 0)),
    "right": ((1, 0, 0), (0, 0, 1)),
    "iso": ((1, -1, 1), (0, 0, 1)),
}
# model axis index for the screen (u, v) axes of each orthographic view
VIEW_AXES = {"front": (0, 2), "top": (0, 1), "right": (1, 2)}
CURVE_SAMPLES_PER_MM = 4  # curved edges: points per model mm, clamped below
CURVE_SEGMENTS = (24, 720)  # a full turn of a circle or ellipse gets at least 24 segments


def to_view(view, xyz):
    """Model point -> (u, v) in an orthographic view's coordinates."""
    i, j = VIEW_AXES[view]
    return (float(xyz[i]), float(xyz[j]))


def _edges(compound):
    out = []
    if compound.IsNull():
        return out
    ex = TopExp_Explorer(compound, TopAbs_EDGE)
    while ex.More():
        e = TopoDS.Edge(ex.Current())
        BRepLib.BuildCurves3d_s(e, 1e-6)
        out.append(Edge(e))
        ex.Next()
    return out


def _hlr(shape, eye, look_at, up):
    """Hidden-line removal: (visible sharp+outline, visible smooth, hidden sharp+outline)."""
    algo = HLRBRep_Algo()
    algo.Add(shape.wrapped)
    ax = gp_Ax2()
    ax.SetAxis(gp_Ax1(Vector(eye).to_pnt(), (Vector(eye) - Vector(look_at)).normalized().to_dir()))
    ax.SetYDirection(Vector(up).normalized().to_dir())
    algo.Projector(HLRAlgo_Projector(ax))
    algo.Update()
    algo.Hide()
    h = HLRBRep_HLRToShape(algo)
    return (_edges(h.VCompound()) + _edges(h.OutLineVCompound()), _edges(h.Rg1LineVCompound()),
            _edges(h.HCompound()) + _edges(h.OutLineHCompound()))


def turn_fraction(edge):
    """Share of a full turn a circle or ellipse edge spans (1.0 for any other curve)."""
    if edge.geom_type not in (GeomType.CIRCLE, GeomType.ELLIPSE):
        return 1.0
    curve = BRepAdaptor_Curve(edge.wrapped)
    return min(1.0, abs(curve.LastParameter() - curve.FirstParameter()) / (2 * math.pi))


def _sample(edge, shift):
    """Polyline of a projected edge. Circular and elliptic arcs keep the angular
    resolution of a full turn (lo segments per turn), so a short arc piece left by
    hidden-line removal gets a few points instead of lo."""
    lo, hi = CURVE_SEGMENTS
    if edge.geom_type == GeomType.LINE:
        n = 1
    else:
        floor = max(2, math.ceil(lo * turn_fraction(edge)))
        n = min(hi, max(floor, math.ceil(edge.length * CURVE_SAMPLES_PER_MM)))
    return [(p.X + shift[0], p.Y + shift[1]) for p in edge.positions([k / n for k in range(n + 1)])]


def _circles(edges, shift):
    """Full circles among projected edges as (cu, cv, r).

    Arcs sharing a centre and radius are merged only by angular coverage, so
    quarter arcs that overlap in projection (fillet ends) never fake a circle.
    """
    bins = {}
    for e in edges:
        if e.geom_type != GeomType.CIRCLE:
            continue
        c, r = e.arc_center, e.radius
        key = (round(c.X + shift[0], 4), round(c.Y + shift[1], 4), round(r, 4))
        covered = bins.setdefault(key, set())
        for p in e.positions([k / 72 for k in range(73)]):
            covered.add(int(math.degrees(math.atan2(p.Y - c.Y, p.X - c.X)) // 10) % 36)
    return [k for k, covered in bins.items() if len(covered) == 36]


def project_view(shape, view):
    """Return {"visible", "hidden": [[(u, v), ...]], "extent": [u0, v0, u1, v1], ...}.

    Extra keys: "smooth" (visible tangent edges, drawn thin) and "circles"
    (visible full circles (cu, cv, r), for centre marks).
    """
    direction, up = CAMERAS[view]
    bb = shape.bounding_box()
    center = (bb.center().X, bb.center().Y, bb.center().Z)
    norm = math.sqrt(sum(d * d for d in direction))
    distance = bb.diagonal * 4 + 1
    eye = tuple(center[k] + direction[k] / norm * distance for k in range(3))
    visible, smooth, hidden = _hlr(shape, eye, center, up)
    # HLR output is relative to look_at; shift back to model axes.
    shift = to_view(view, center) if view in VIEW_AXES else (0.0, 0.0)
    vis, smo, hid = ([_sample(e, shift) for e in group if e.length > 1e-7]
                     for group in (visible, smooth, hidden))
    pts = [p for line in vis + smo + hid for p in line]
    if not pts:
        raise ValueError(f"empty {view} view")
    extent = [min(p[0] for p in pts), min(p[1] for p in pts),
              max(p[0] for p in pts), max(p[1] for p in pts)]
    return {"visible": vis, "hidden": hid, "extent": extent, "smooth": smo,
            "circles": _circles(visible + smooth, shift)}
