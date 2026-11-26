import hashlib
import math

import pytest
from build123d import Solid, export_step, import_step

from sketchcad.errors import ModelError
from sketchcad.features import make_feature

approx = lambda v: pytest.approx(v, rel=1e-6)  # noqa: E731


def make(feature, tmp_path, prior=None):
    return make_feature(feature, tmp_path, prior or {})


def square(plane="XY", depth=5):
    return {"id": "e", "kind": "extrude", "operation": "base", "depthMm": depth,
            "profile": {"plane": plane, "points": [[0, 0], [10, 0], [10, 10], [0, 10]]}}


def test_box(tmp_path):
    s = make({"id": "b", "kind": "box", "originMm": [0, 0, 0], "sizeMm": [10, 20, 30]}, tmp_path)
    assert s.volume == approx(6000)


def test_cylinder_axis_and_base_center(tmp_path):
    f = {"id": "c", "kind": "cylinder", "centerMm": [1, 2, 3], "radiusMm": 5, "heightMm": 10, "axis": "X"}
    s = make(f, tmp_path)
    assert s.volume == approx(250 * math.pi)
    bb = s.bounding_box()
    assert (bb.min.X, bb.max.X) == (pytest.approx(1), pytest.approx(11))
    assert bb.min.Y == pytest.approx(-3)


def test_sphere(tmp_path):
    s = make({"id": "s", "kind": "sphere", "centerMm": [0, 0, 0], "radiusMm": 1}, tmp_path)
    assert s.volume == approx(4 / 3 * math.pi)


def test_extrude_xy(tmp_path):
    s = make(square("XY"), tmp_path)
    assert s.volume == approx(500)
    bb = s.bounding_box()
    assert (bb.min.Z, bb.max.Z) == (pytest.approx(0), pytest.approx(5))


def test_extrude_xz_goes_plus_y(tmp_path):
    bb = make(square("XZ"), tmp_path).bounding_box()
    assert (bb.min.Y, bb.max.Y) == (pytest.approx(0), pytest.approx(5))
    assert (bb.min.Z, bb.max.Z) == (pytest.approx(0), pytest.approx(10))


def test_extrude_yz_goes_plus_x(tmp_path):
    bb = make(square("YZ"), tmp_path).bounding_box()
    assert (bb.min.X, bb.max.X) == (pytest.approx(0), pytest.approx(5))


def test_extrude_circle(tmp_path):
    f = {"id": "e", "kind": "extrude", "operation": "base", "depthMm": 2,
         "profile": {"plane": "XY", "circle": {"centerMm": [0, 0], "radiusMm": 3}}}
    assert make(f, tmp_path).volume == approx(18 * math.pi)


def test_extrude_arcs_half_disc(tmp_path):
    f = {"id": "e", "kind": "extrude", "operation": "base", "depthMm": 1,
         "profile": {"plane": "XY", "arcs": {"start": [-1, 0], "segments": [
             {"to": [1, 0], "through": [0, 1]}, {"to": [-1, 0]}]}}}
    assert make(f, tmp_path).volume == approx(math.pi / 2)


def test_extrude_arcs_explicit_closing_segment_equals_implicit(tmp_path):
    f = {"id": "e", "kind": "extrude", "operation": "base", "depthMm": 1,
         "profile": {"plane": "XY", "arcs": {"start": [-1, 0], "segments": [
             {"to": [1, 0], "through": [0, 1]}, {"to": [-1, 0]}]}}}
    g = {**f, "profile": {"plane": "XY", "arcs": {"start": [-1, 0], "segments": [
        {"to": [1, 0], "through": [0, 1]}]}}}
    assert make(g, tmp_path).volume == approx(make(f, tmp_path).volume)


def test_extrude_self_intersecting_figure_eight(tmp_path):
    f = {"id": "cable", "kind": "extrude", "operation": "base", "depthMm": 1,
         "profile": {"plane": "XY", "points": [[0, 0], [10, 10], [10, 0], [0, 10]]}}
    with pytest.raises(ModelError, match=r"cable.*self-intersect"):
        make(f, tmp_path)


def test_extrude_self_intersecting_arcs(tmp_path):
    f = {"id": "cable", "kind": "extrude", "operation": "base", "depthMm": 1,
         "profile": {"plane": "XY", "arcs": {"start": [0, 0], "segments": [
             {"to": [10, 10]}, {"to": [10, 0]}, {"to": [0, 10]}]}}}
    with pytest.raises(ModelError, match=r"cable.*self-intersect"):
        make(f, tmp_path)


def test_revolve(tmp_path):
    f = {"id": "r", "kind": "revolve", "operation": "base",
         "profileRzMm": [[0, 0], [5, 0], [5, 10], [0, 10]]}
    assert make(f, tmp_path).volume == approx(250 * math.pi)


C45 = 5 * math.sqrt(0.5)
HALF_DISC = {"start": [0, -5], "segments": [{"to": [5, 0], "through": [C45, -C45]},
                                            {"to": [0, 5], "through": [C45, C45]}]}


def test_revolve_arcs_profile_is_smooth(tmp_path):
    f = {"id": "r", "kind": "revolve", "operation": "base", "profile": HALF_DISC}
    solid = make(f, tmp_path)
    assert solid.volume == approx(4 / 3 * math.pi * 125)
    assert {f.geom_type.name for f in solid.faces()} == {"SPHERE"}  # true curves, not facet rings


def test_revolve_dome_with_a_rounded_through_point_survives_step(tmp_path):
    # through-point given to 4 decimals: the arc centre lands a hair off the axis
    f = {"id": "r", "kind": "revolve", "operation": "base", "profile": {
        "start": [0, 34.4525], "segments": [{"to": [19.9087, 34.4525]}, {"to": [0, 38.2], "through": [9, 37.4553]}]}}
    solid = make(f, tmp_path)
    assert "SPHERE" in {face.geom_type.name for face in solid.faces()}
    export_step(solid, tmp_path / "dome.step")
    assert import_step(tmp_path / "dome.step").volume == approx(solid.volume)


def test_revolve_tiny_arc_from_the_axis_at_one_height_is_named(tmp_path):
    f = {"id": "r", "kind": "revolve", "operation": "base", "profile": {
        "start": [0, 0], "segments": [{"to": [0.01, 0], "through": [0.005, 0.005]}, {"to": [0.01, 5]}, {"to": [0, 5]}]}}
    with pytest.raises(ModelError, match=r"feature r: profile\.segments\[0\]: .*same height"):
        make(f, tmp_path)


def test_revolve_arcs_profile_crossing_the_axis_rejected(tmp_path):
    def on(deg):
        return [0.5 + math.cos(math.radians(deg)), math.sin(math.radians(deg))]
    # every given point has r >= 0, but the arc swings through r = -0.5
    f = {"id": "r", "kind": "revolve", "operation": "base",
         "profile": {"start": on(100), "segments": [{"to": on(260), "through": on(110)}, {"to": on(100)}]}}
    with pytest.raises(ModelError, match=r"feature r: .*crosses the revolve axis"):
        make(f, tmp_path)


def test_loft_polygon_ruled_and_ellipse(tmp_path):
    sq = lambda z, h: {"kind": "polygon", "pointsMm": [[-h, -h, z], [h, -h, z], [h, h, z], [-h, h, z]]}  # noqa: E731
    f = {"id": "l", "kind": "loft", "operation": "base", "ruled": True, "sections": [sq(0, 5), sq(10, 5)]}
    assert make(f, tmp_path).volume == approx(1000)
    ell = lambda z: {"kind": "ellipse", "plane": "XY", "originMm": [0, 0, z], "radiusUMm": 4, "radiusVMm": 2}  # noqa: E731
    g = {"id": "l", "kind": "loft", "operation": "base", "ruled": True, "sections": [ell(0), ell(10)]}
    assert make(g, tmp_path).volume == pytest.approx(80 * math.pi, rel=1e-3)


def _tilted_square(z, h, deg, digits=None):
    t = math.tan(math.radians(deg))
    pts = [[x, y, z + y * t] for x, y in [(-h, -h), (h, -h), (h, h), (-h, h)]]
    return {"kind": "polygon", "pointsMm": [[round(c, digits) if digits else c for c in p] for p in pts]}


def test_loft_section_rounded_on_a_tilted_plane_builds(tmp_path):
    flat = {"kind": "polygon", "pointsMm": [[-5, -5, 0], [5, -5, 0], [5, 5, 0], [-5, 5, 0]]}
    exact = {"id": "l", "kind": "loft", "operation": "base", "ruled": True,
             "sections": [flat, _tilted_square(10, 5, 13)]}
    rounded = {**exact, "sections": [flat, _tilted_square(10, 5, 13, digits=2)]}
    rounded["sections"][1]["pointsMm"][1][2] += 0.004  # off the plane by rounding-sized noise
    assert make(rounded, tmp_path).volume == pytest.approx(make(exact, tmp_path).volume, rel=1e-3)


def test_loft_section_off_its_plane_is_named(tmp_path):
    flat = {"kind": "polygon", "pointsMm": [[-5, -5, 0], [5, -5, 0], [5, 5, 0], [-5, 5, 0]]}
    bent = _tilted_square(10, 5, 13)
    bent["pointsMm"][1][2] += 0.5
    f = {"id": "l", "kind": "loft", "operation": "base", "ruled": True, "sections": [flat, bent]}
    with pytest.raises(ModelError, match=r"feature l: loft section 2 is not planar \(max deviation 0\.1\d* mm\)"):
        make(f, tmp_path)


def test_loft_section_winding_does_not_matter(tmp_path):
    sq = [[-5, -5, 0], [5, -5, 0], [5, 5, 0], [-5, 5, 0]]
    top = [[x * 0.5, y * 0.5, 10] for x, y, _ in sq]
    for a, b in ((sq, top), (sq[::-1], top), (sq, top[::-1]), (top, sq)):
        f = {"id": "l", "kind": "loft", "operation": "base", "ruled": True,
             "sections": [{"kind": "polygon", "pointsMm": a}, {"kind": "polygon", "pointsMm": b}]}
        assert make(f, tmp_path).volume == approx(10 / 3 * (100 + 25 + 50))


def _step_file(tmp_path):
    p = tmp_path / "b.step"
    export_step(Solid.make_box(1, 2, 3), p)
    return p


def test_step_loads_and_checks_hash(tmp_path):
    p = _step_file(tmp_path)
    f = {"id": "imp", "kind": "step", "operation": "base", "file": "b.step",
         "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
    assert make(f, tmp_path).volume == approx(6)
    with pytest.raises(ModelError, match=r"imp.*sha256"):
        make({**f, "sha256": "0" * 64}, tmp_path)


def test_hole_tool_through(tmp_path):
    f = {"id": "h", "kind": "hole", "operation": "cut", "at": [0, 0, 0], "direction": "Z",
         "diameterMm": 4, "through": True}
    s = make(f, tmp_path)
    bb = s.bounding_box()
    assert bb.max.X - bb.min.X == pytest.approx(4)
    assert bb.min.Z < -100 and bb.max.Z > 100
    assert s.volume == approx(math.pi * 4 * 1000)


def test_hole_blind_negative_direction(tmp_path):
    f = {"id": "h", "kind": "hole", "operation": "cut", "at": [0, 0, 10], "direction": "-Z",
         "diameterMm": 4, "depthMm": 3}
    bb = make(f, tmp_path).bounding_box()
    assert (bb.min.Z, bb.max.Z) == (pytest.approx(7), pytest.approx(10))


def test_hole_counterbore(tmp_path):
    f = {"id": "h", "kind": "hole", "operation": "cut", "at": [0, 0, 10], "direction": "-Z",
         "diameterMm": 4, "depthMm": 8, "counterbore": {"diameterMm": 8, "depthMm": 2}}
    s = make(f, tmp_path)
    assert s.volume == approx(math.pi * 16 * 2 + math.pi * 4 * 6)


def test_pattern_polar(tmp_path):
    ball = make({"id": "s", "kind": "sphere", "centerMm": [10, 0, 0], "radiusMm": 1}, tmp_path)
    f = {"id": "p", "kind": "pattern", "operation": "union", "of": "s",
         "polar": {"count": 6, "axis": "Z", "totalDeg": 360}}
    s = make(f, tmp_path, {"s": ball})
    assert len(s.solids()) == 5  # copies only, original excluded
    assert s.volume == approx(5 * 4 / 3 * math.pi)


def test_pattern_polar_partial_arc_spacing(tmp_path):
    ball = make({"id": "s", "kind": "sphere", "centerMm": [10, 0, 0], "radiusMm": 1}, tmp_path)
    f = {"id": "p", "kind": "pattern", "operation": "union", "of": "s",
         "polar": {"count": 3, "axis": "Z", "totalDeg": 90}}
    s = make(f, tmp_path, {"s": ball})
    centers = sorted(round(c.center().Y, 6) for c in s.solids())
    assert centers == [pytest.approx(10 * math.sin(math.pi / 4)), pytest.approx(10.0)]


def test_pattern_linear_grid(tmp_path):
    ball = make({"id": "s", "kind": "sphere", "centerMm": [0, 0, 0], "radiusMm": 1}, tmp_path)
    f = {"id": "p", "kind": "pattern", "operation": "union", "of": "s",
         "linear": {"countX": 3, "countY": 2, "stepMm": [10, 10, 0]}}
    s = make(f, tmp_path, {"s": ball})
    assert len(s.solids()) == 5
    f1 = {**f, "linear": {"countX": 3, "stepMm": [0, 0, 5]}}
    s1 = make(f1, tmp_path, {"s": ball})
    assert sorted(round(c.center().Z, 6) for c in s1.solids()) == [5, 10]


def test_unexpected_occ_error_wrapped(tmp_path):
    f = {"id": "bad", "kind": "revolve", "operation": "base",
         "profileRzMm": [[0, 0], [0, 5], [0, 10]]}  # degenerate: zero-area
    with pytest.raises(ModelError, match="bad"):
        make(f, tmp_path)
