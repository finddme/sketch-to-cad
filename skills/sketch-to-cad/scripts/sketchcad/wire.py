"""Wire guides for the viewer: BRep edges plus evenly spaced section curves.

wire_guides(shape, policy) returns groups of polylines in part coordinates
(mm, Z-up):
  [{"kind": "brep-edge", "lines": [[[x, y, z], ...], ...]},
   {"kind": "section", "axis": "Z", "coordinateMm": 5.0, "lines": [...]}, ...]
"""
import math

from build123d import GeomType, Plane, section

from .edges import select_edges
from .project import turn_fraction

DEFAULT_POLICY = {"curveSegments": 48, "horizontalSections": 6, "verticalSections": 3}
MIN_EDGE_MM = 1e-7
NORMALS = {"X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1)}


def sample(edges, segments=48):
    """Polyline per edge: straight edges keep their two end points, curves get
    segments + 1 points, except circular and elliptic arcs, which get the share of
    `segments` their angle spans (at least 2), i.e. the same angular resolution."""
    lines = []
    for edge in edges:
        if edge.length <= MIN_EDGE_MM:
            continue
        if edge.geom_type == GeomType.LINE:
            n = 1
        else:
            n = max(2, math.ceil(segments * turn_fraction(edge)))
        lines.append([[p.X, p.Y, p.Z] for p in edge.positions([i / n for i in range(n + 1)])])
    return lines


def wire_guides(shape, policy=None):
    """BRep edges (seams of closed faces skipped), then Z sections, then X and Y sections."""
    policy = {**DEFAULT_POLICY, **(policy or {})}
    segments = policy["curveSegments"]
    box = shape.bounding_box()
    groups = [{"kind": "brep-edge", "lines": sample(select_edges(shape, "all-outer", {}), segments)}]
    for axis, count in [("Z", policy["horizontalSections"]), ("X", policy["verticalSections"]),
                        ("Y", policy["verticalSections"])]:
        lo, hi = getattr(box.min, axis), getattr(box.max, axis)
        for i in range(1, count + 1):
            at = lo + (hi - lo) * i / (count + 1)
            origin = [0.0, 0.0, 0.0]
            origin["XYZ".index(axis)] = at
            cut = section(shape, Plane(origin=origin, z_dir=NORMALS[axis]))
            lines = sample(cut.edges(), segments)
            if lines:
                groups.append({"kind": "section", "axis": axis, "coordinateMm": at, "lines": lines})
    return groups
