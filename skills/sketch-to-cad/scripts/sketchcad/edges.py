"""Edge selectors for fillet and chamfer."""
from build123d import GeomType
from OCP.BRep import BRep_Tool

from .errors import ModelError

ON_SURFACE_TOL_MM = 0.01
EXTREME_TOL_MM = 1e-4
AXIS_VEC = {"X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1)}


def select_edges(model, selector, feature_solids):
    """Return the edges of `model` picked by `selector`.

    - all-outer: every edge of the model except seam edges of closed faces.
    - face:[+-][XYZ]: boundary edges of the planar faces whose outward normal
      points along that axis direction and that lie at the part's extreme
      coordinate on that side (within EXTREME_TOL_MM), so blind-hole bottoms
      and steps facing the same way are not picked.
    - feature:<id>: approximate. Final edges whose midpoint and both end points
      lie within 0.01 mm of the surface of that feature's own solid (edges that
      the feature created or reshaped). Seam edges of closed faces are skipped.
      Raises ModelError when nothing matches.
    """
    if selector == "all-outer":
        return [e for e in model.edges() if not _is_seam(e, model)]
    if selector.startswith("face:"):
        return _face_edges(model, selector[5:])
    if selector.startswith("feature:"):
        return _feature_edges(model, selector[8:], feature_solids)
    raise ModelError(f"unknown edge selector {selector!r}")


def _face_edges(model, spec):
    sign = -1 if spec[0] == "-" else 1
    want = tuple(sign * c for c in AXIS_VEC[spec[1]])
    axis = spec[1]
    box = model.bounding_box(optimal=True)
    extreme = getattr(box.min if sign < 0 else box.max, axis)
    picked = []
    for face in model.faces():
        if face.geom_type != GeomType.PLANE:
            continue
        n = face.normal_at()
        at = getattr(face.center(), axis)
        if (all(abs(a - b) < 1e-6 for a, b in zip((n.X, n.Y, n.Z), want))
                and abs(at - extreme) <= EXTREME_TOL_MM):
            for edge in face.edges():
                if not any(edge.is_same(e) for e in picked):
                    picked.append(edge)
    if not picked:
        raise ModelError(f"edge selector face:{spec} matches no planar face at the part's extreme")
    return picked


def _is_seam(edge, model):
    return any(BRep_Tool.IsClosed_s(edge.wrapped, f.wrapped) for f in model.faces())


def _feature_edges(model, fid, feature_solids):
    tool = feature_solids.get(fid)
    if tool is None:
        raise ModelError(f"edge selector feature:{fid} refers to a feature that was not built")
    tool_faces = tool.faces()

    def near(p):
        return any(f.distance_to(p) <= ON_SURFACE_TOL_MM for f in tool_faces)

    picked = [e for e in model.edges()
              if near(e.position_at(0)) and near(e.position_at(0.5)) and near(e.position_at(1))
              and not _is_seam(e, model)]
    if not picked:
        raise ModelError(f"edge selector feature:{fid} matches no edge of the final shape")
    return picked
