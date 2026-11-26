import hashlib
import math
import shutil
from pathlib import Path

import pytest
from build123d import Solid

from sketchcad.edges import select_edges
from sketchcad.errors import ModelError
from sketchcad.part import build_part

FIX = Path(__file__).parent / "fixtures"
approx = lambda v: pytest.approx(v, rel=1e-6)  # noqa: E731


def box(size=10, fid="base"):
    return {"id": fid, "kind": "box", "operation": "base", "originMm": [0, 0, 0], "sizeMm": [size] * 3}


def cyl_cut(fid="cyl", at=(5, 5, -1), h=12):
    return {"id": fid, "kind": "cylinder", "operation": "cut", "centerMm": list(at),
            "radiusMm": 2, "heightMm": h, "axis": "Z"}


def part(features, finish=None, construction="solid", pid="body"):
    return {"id": pid, "construction": construction, "features": features, "finish": finish or []}


def test_cut_volume(tmp_path):
    p, checks = build_part(part([box(), cyl_cut()]), tmp_path)
    assert p.volume == approx(1000 - math.pi * 4 * 10)
    assert [c["id"] for c in checks] == ["base", "cyl"]
    assert checks[1]["operation"] == "cut"
    assert checks[1]["changeMm3"] == pytest.approx(-math.pi * 40, rel=1e-6)


def test_ineffective_cut(tmp_path):
    with pytest.raises(ModelError, match="ineffective cut") as e:
        build_part(part([box(), cyl_cut(at=(50, 50, 0))]), tmp_path)
    assert "part body" in str(e.value)


def test_fillet_all_outer(tmp_path):
    p, checks = build_part(part([box()], [{"kind": "fillet", "radiusMm": 1, "edges": "all-outer"}]), tmp_path)
    assert 0 < p.volume < 1000
    assert p.is_valid and len(p.solids()) == 1
    assert checks[-1]["operation"] == "fillet"


def test_chamfer(tmp_path):
    p, _ = build_part(part([box()], [{"kind": "chamfer", "distanceMm": 1, "edges": "face:+Z"}]), tmp_path)
    assert 0 < p.volume < 1000


def test_fillet_too_large(tmp_path):
    with pytest.raises(ModelError) as e:
        build_part(part([box()], [{"kind": "fillet", "radiusMm": 6, "edges": "all-outer"}]), tmp_path)
    assert "part body" in str(e.value) and "reduce the radius" in str(e.value)


def test_chamfer_too_large(tmp_path):
    with pytest.raises(ModelError, match="reduce the distance"):
        build_part(part([box()], [{"kind": "chamfer", "distanceMm": 9, "edges": "all-outer"}]), tmp_path)


def test_face_selector_four_edges():
    m = Solid.make_box(10, 10, 10)
    assert len(select_edges(m, "face:+Z", {})) == 4
    assert len(select_edges(m, "face:-X", {})) == 4
    assert len(select_edges(m, "all-outer", {})) == 12


def test_feature_selector_hole_circles():
    base = Solid.make_box(10, 10, 10)
    tool = Solid.make_cylinder(2, 12, __import__("build123d").Plane(origin=(5, 5, -1)))
    model = base.cut(tool)
    edges = select_edges(model, "feature:h", {"h": tool})
    assert len(edges) == 2


def test_feature_selector_no_match():
    m = Solid.make_box(10, 10, 10)
    far = Solid.make_box(1, 1, 1, __import__("build123d").Plane(origin=(50, 50, 50)))
    with pytest.raises(ModelError):
        select_edges(m, "feature:x", {"x": far})


def test_fillet_feature_edges(tmp_path):
    hole = {"id": "h", "kind": "hole", "operation": "cut", "at": [5, 5, 10], "direction": "-Z",
            "diameterMm": 4, "through": True}
    p, _ = build_part(part([box(), hole], [{"kind": "fillet", "radiusMm": 0.5, "edges": "feature:h"}]), tmp_path)
    assert p.is_valid and p.volume < 1000 - math.pi * 4 * 10


def test_shell(tmp_path):
    p, checks = build_part(
        part([box(20)], [{"kind": "shell", "thicknessMm": 2, "openFace": "+Z"}], construction="hollow"), tmp_path)
    assert p.volume == approx(8000 - 16 * 16 * 18)
    bb = p.bounding_box()
    assert (bb.max.X, bb.max.Y, bb.max.Z) == pytest.approx((20, 20, 20), abs=1e-4)
    assert checks[-1]["operation"] == "shell"


def test_shell_bad_open_face(tmp_path):
    cylinder = {"id": "c", "kind": "cylinder", "operation": "base", "centerMm": [0, 0, 0],
                "radiusMm": 5, "heightMm": 10, "axis": "Z"}
    with pytest.raises(ModelError, match="openFace"):
        build_part(part([cylinder], [{"kind": "shell", "thicknessMm": 1, "openFace": "+X"}],
                        construction="hollow"), tmp_path)


def code_part(name, **extra):
    return {"id": "knob", "construction": "code", "code": {"file": name, **extra}}


def test_code_ok():
    p, checks = build_part(code_part("code_ok.py"), FIX)
    assert p.volume == approx(1000)
    assert checks == [{"id": "knob", "operation": "code", "volumeMm3": pytest.approx(1000), "changeMm3": pytest.approx(1000)}]


def test_code_sha_ok_and_mismatch():
    good = hashlib.sha256((FIX / "code_ok.py").read_bytes()).hexdigest()
    build_part(code_part("code_ok.py", sha256=good), FIX)
    with pytest.raises(ModelError, match="sha256"):
        build_part(code_part("code_ok.py", sha256="0" * 64), FIX)


def test_code_raises():
    with pytest.raises(ModelError) as e:
        build_part(code_part("code_raises.py"), FIX)
    assert "code_raises.py" in str(e.value) and "boom" in str(e.value) and "part knob" in str(e.value)


def test_code_two_solids():
    with pytest.raises(ModelError, match="exactly one solid"):
        build_part(code_part("code_two_solids.py"), FIX)


def test_code_same_name_in_two_dirs(tmp_path):
    shutil.copy(FIX / "code_raises.py", tmp_path / "code_ok.py")
    with pytest.raises(ModelError):
        build_part(code_part("code_ok.py"), tmp_path)
    build_part(code_part("code_ok.py"), FIX)


def test_pattern_cut_applies_to_all_copies(tmp_path):
    pat = {"id": "pat", "kind": "pattern", "operation": "cut", "of": "cyl",
           "linear": {"countX": 3, "stepMm": [3, 0, 0]}}
    cyl = cyl_cut(at=(2, 5, -1))
    cyl["radiusMm"] = 1
    first = dict(cyl, operation="cut")
    p, checks = build_part(part([box(), first, pat]), tmp_path)
    assert p.volume == approx(1000 - 3 * math.pi * 10)


def test_fillet_all_outer_on_cylinder(tmp_path):
    cylinder = {"id": "c", "kind": "cylinder", "operation": "base", "centerMm": [0, 0, 0],
                "radiusMm": 10, "heightMm": 20, "axis": "Z"}
    p, _ = build_part(part([cylinder], [{"kind": "fillet", "radiusMm": 1, "edges": "all-outer"}]), tmp_path)
    assert p.is_valid and len(p.solids()) == 1
    assert 0 < p.volume < 2000 * math.pi


def test_code_sha_missing_file(tmp_path):
    with pytest.raises(ModelError, match="nope.py"):
        build_part(code_part("nope.py", sha256="0" * 64), tmp_path)


def test_face_selector_skips_inner_faces_below_the_extreme():
    # A blind hole bottom also faces +Z, but it is not on the part's top: leave it alone.
    from build123d import Plane
    m = Solid.make_box(10, 10, 10).cut(Solid.make_cylinder(2, 4, Plane(origin=(5, 5, 6))))
    edges = select_edges(m, "face:+Z", {})
    assert len(edges) == 5  # 4 outline edges + the hole rim
    assert all(abs(e.position_at(0.5).Z - 10) < 1e-6 for e in edges)


def test_code_part_announces_file_and_hash(capsys):
    digest = hashlib.sha256((FIX / "code_ok.py").read_bytes()).hexdigest()
    build_part(code_part("code_ok.py", sha256=digest), FIX)
    err = capsys.readouterr().err
    assert f"running code part knob: code_ok.py sha256={digest}" in err
    assert "no sha256" not in err
    build_part(code_part("code_ok.py"), FIX)
    err = capsys.readouterr().err
    assert f"running code part knob: code_ok.py sha256={digest} (no sha256 recorded in model.json)" in err


def test_through_hole_crosses_a_long_part(tmp_path):
    long_box = {"id": "base", "kind": "box", "operation": "base", "originMm": [0, 0, 0],
                "sizeMm": [1500, 10, 10]}
    hole = {"id": "bore", "kind": "hole", "operation": "cut", "at": [0, 5, 5], "direction": "X",
            "diameterMm": 4, "through": True}
    p, _ = build_part(part([long_box, hole]), tmp_path)
    assert p.volume == pytest.approx(1500 * 100 - math.pi * 4 * 1500, rel=1e-6)
