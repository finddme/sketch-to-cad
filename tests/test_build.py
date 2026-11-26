import json
from pathlib import Path

import pytest
import trimesh
from build123d import Solid

from sketchcad.build import load_manifest, load_part_shapes, run_build, write_manifest
from sketchcad.errors import ModelError

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("b") / "out"
    manifest = run_build(FIX / "two_parts.json", out)
    return out, manifest


def test_output_tree(built):
    out, m = built
    for rel in ["model.json", "manifest.json", "parts/box.step", "parts/box.glb",
                "parts/lid.step", "parts/lid.glb", "assembly.step", "assembly.glb"]:
        assert (out / rel).is_file(), rel
    assert (out / "model.json").read_bytes() == (FIX / "two_parts.json").read_bytes()
    assert not out.with_name("out.building").exists()
    assert m == load_manifest(out)
    assert m["schemaVersion"] == 1 and m["units"] == "mm" and m["name"] == "box-with-lid"
    assert list(m["parts"]) == ["box", "lid"]
    assert m["parts"]["box"]["step"] == "parts/box.step"
    assert m["parts"]["box"]["glb"] == "parts/box.glb"
    assert m["parts"]["box"]["volumeMm3"] == pytest.approx(24000)
    assert m["parts"]["box"]["checks"][0]["id"] == "base"
    assert m["stages"] == {"build": "ok"}
    assert m["glbUnit"] == "m" and m["glbUpAxis"] in ("Y", "Z")


def test_part_bounds_and_placement(built):
    _, m = built
    assert m["parts"]["lid"]["boundsMm"]["min"][2] == pytest.approx(0, abs=1e-6)
    assert m["parts"]["lid"]["boundsMm"]["max"][2] == pytest.approx(5)
    assert m["assembly"]["step"] == "assembly.step" and m["assembly"]["glb"] == "assembly.glb"
    assert m["assembly"]["placement"] == [
        {"part": "box", "translateMm": [0, 0, 0], "rotateDeg": [0, 0, 0]},
        {"part": "lid", "translateMm": [20, 15, 20], "rotateDeg": [0, 0, 0]},
    ]


def _glb_bounds_mm(path, up):
    scene = trimesh.load(path)
    lo, hi = scene.bounds
    lo, hi = lo * 1000, hi * 1000
    if up == "Y":  # glTF Y-up -> CAD Z-up: (x, y, z) = (X, -Z, Y)
        lo, hi = [lo[0], -hi[2], lo[1]], [hi[0], -lo[2], hi[1]]
    return list(lo), list(hi)


def test_assembly_glb_height(built):
    out, m = built
    lo, hi = _glb_bounds_mm(out / "assembly.glb", m["glbUpAxis"])
    assert hi[2] == pytest.approx(25, abs=0.5)
    assert lo[2] == pytest.approx(0, abs=0.5)


def test_rotation_order_xyz(tmp_path):
    model = json.loads((FIX / "two_parts.json").read_text())
    # box 40x30x20; rotate X 90 then Z 90, then translate
    model["parts"] = model["parts"][:1]
    model["placement"] = [{"part": "box", "translateMm": [100, 0, 0], "rotateDeg": [90, 0, 90]}]
    src = tmp_path / "m.json"
    src.write_text(json.dumps(model))
    run_build(src, tmp_path / "o")
    # X90: (x,y,z)->(x,-z,y) => extents x40,y20,z30; Z90: x<->y => x20,y40,z30
    from build123d import import_step
    box = import_step(tmp_path / "o" / "assembly.step").bounding_box()
    size = [box.max.X - box.min.X, box.max.Y - box.min.Y, box.max.Z - box.min.Z]
    assert size == pytest.approx([20, 40, 30], abs=1e-4)


def test_existing_out_refused(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "keep.txt").write_text("x")
    with pytest.raises(ModelError, match="already exists"):
        run_build(FIX / "two_parts.json", out)
    assert [p.name for p in out.iterdir()] == ["keep.txt"]


def test_existing_building_refused(tmp_path):
    stage = tmp_path / "out.building"
    stage.mkdir()
    with pytest.raises(ModelError, match="already exists"):
        run_build(FIX / "two_parts.json", tmp_path / "out")
    assert stage.exists() and not (tmp_path / "out").exists()


def test_failure_leaves_nothing(tmp_path):
    model = json.loads((FIX / "two_parts.json").read_text())
    model["parts"][1]["features"].append(
        {"id": "nocut", "operation": "cut", "kind": "cylinder", "centerMm": [500, 500, 0],
         "radiusMm": 1, "heightMm": 1, "axis": "Z"})
    src = tmp_path / "m.json"
    src.write_text(json.dumps(model))
    with pytest.raises(ModelError, match="part lid"):
        run_build(src, tmp_path / "out")
    assert not (tmp_path / "out").exists() and not (tmp_path / "out.building").exists()


def test_load_part_shapes(built):
    out, m = built
    shapes = load_part_shapes(out)
    assert list(shapes) == ["box", "lid"]
    assert all(isinstance(s, Solid) for s in shapes.values())
    assert shapes["box"].volume == pytest.approx(24000, rel=1e-6)


def test_write_manifest_preserves_keys_and_is_atomic(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    write_manifest(out, {"schemaVersion": 1, "stages": {"build": "ok"}, "extra": 1})
    m = load_manifest(out)
    m["stages"]["drawing"] = "ok"
    write_manifest(out, m)
    m2 = load_manifest(out)
    assert m2["extra"] == 1 and m2["stages"] == {"build": "ok", "drawing": "ok"}
    assert [p.name for p in out.iterdir()] == ["manifest.json"]


def test_cli_build(run_cli, tmp_path):
    r = run_cli("build", FIX / "two_parts.json", "--out", tmp_path / "o")
    assert r.returncode == 0, r.stderr
    assert "2 parts" in r.stdout and str(tmp_path / "o") in r.stdout


def _with_span(tmp_path, value, span="depth"):
    model = json.loads((FIX / "two_parts.json").read_text())
    model["dimensions"].append({"label": "box depth (reference)", "valueMm": value,
                                "origin": "assumed", "part": "box", "span": span})
    src = tmp_path / "m.json"
    src.write_text(json.dumps(model))
    return src


def test_span_dimension_within_half_a_mm_builds(tmp_path):
    run_build(_with_span(tmp_path, 30.4), tmp_path / "out")


def test_span_dimension_that_does_not_match_the_part_is_refused(tmp_path):
    with pytest.raises(ModelError, match=r"dimensions\[1\] \('box depth \(reference\)'\): .*depth of part "
                                         r"box, which is 30 mm"):
        run_build(_with_span(tmp_path, 31), tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("out", [".", "/", ".."])
def test_unusable_output_path_is_a_model_error(out):
    with pytest.raises(ModelError, match="output folder"):
        run_build(FIX / "two_parts.json", Path(out))
