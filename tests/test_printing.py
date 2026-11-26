import json
from pathlib import Path

import numpy as np
import pytest
import trimesh

from sketchcad.build import load_manifest, run_build
from sketchcad.errors import ModelError
from sketchcad.printing import (DEFAULT_PRINTER, _overhang_regions, best_orientation, estimate_min_wall,
                                run_print_check)

FIX = Path(__file__).parent / "fixtures"


def _model(parts, name="m"):
    return {"schemaVersion": 1, "units": "mm", "upAxis": "Z", "name": name,
            "assumptions": [], "parts": parts, "placement": [], "dimensions": []}


def _box(pid, size, finish=None, construction="solid"):
    part = {"id": pid, "construction": construction, "features": [
        {"id": "base", "operation": "base", "kind": "box", "originMm": [0, 0, 0], "sizeMm": size}]}
    if finish:
        part["finish"] = finish
    return part


def _built(tmp_path, model, name="out"):
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(model))
    out = tmp_path / name
    run_build(path, out)
    return out


def _stl(out, pid):
    return trimesh.load(out / "print" / f"{pid}.stl")


def test_cube_is_clean(tmp_path):
    out = _built(tmp_path, _model([_box("cube", [20, 20, 20])]))
    report = run_print_check(out)
    p = report["parts"]["cube"]
    assert p["watertight"] is True and p["fitsBed"] is True
    assert p["warnings"] == [] and p["overhangAreaRatio"] == 0
    assert p["minWallEstimated"] is True and p["minWallMm"] == pytest.approx(20, abs=0.5)
    assert report["printer"] == DEFAULT_PRINTER
    assert len(p["transform"]) == 4 and len(p["transform"][0]) == 4
    for f in ["cube.stl", "print-plate.3mf", "print-report.json", "print-report.md"]:
        assert (out / "print" / f).is_file(), f
    assert not (out / "print.tmp").exists()
    m = load_manifest(out)
    assert m["stages"]["print-check"] == "ok"
    assert m["print"]["files"] == ["print/cube.stl", "print/print-plate.3mf",
                                   "print/print-report.json", "print/print-report.md"]
    assert m["print"]["parts"]["cube"]["warnings"] == 0
    assert json.loads((out / "print" / "print-report.json").read_text()) == report
    md = (out / "print" / "print-report.md").read_text(encoding="utf-8")
    assert "cube" in md and "추정" in md


def test_stl_is_on_the_bed_origin(tmp_path):
    out = _built(tmp_path, _model([_box("cube", [20, 10, 5])]))
    run_print_check(out)
    mesh = _stl(out, "cube")
    assert mesh.bounds[0] == pytest.approx([0, 0, 0], abs=1e-6)
    assert mesh.is_watertight


def test_thin_wall_box_warns(tmp_path):
    shell = [{"kind": "shell", "thicknessMm": 0.5, "openFace": "+Z"}]
    out = _built(tmp_path, _model([_box("cup", [20, 20, 20], shell, "hollow")]))
    p = run_print_check(out)["parts"]["cup"]
    assert p["minWallMm"] < 0.8
    assert any("min wall" in w for w in p["warnings"])


def test_mushroom_orients_wide_face_down(tmp_path):
    model = _model([{"id": "cap", "construction": "solid", "features": [
        {"id": "stem", "operation": "base", "kind": "box", "originMm": [-3, -3, 0], "sizeMm": [6, 6, 20]},
        {"id": "top", "operation": "union", "kind": "box", "originMm": [-15, -15, 20], "sizeMm": [30, 30, 3]}]}])
    out = _built(tmp_path, model)
    p = run_print_check(out)["parts"]["cap"]
    assert p["overhangAreaRatio"] > 0          # as modeled, the cap hangs over the stem
    assert p["overhangAreaRatioOriented"] == 0  # after the chosen rotation it does not
    mesh = _stl(out, "cap")
    assert mesh.bounds[0][2] == pytest.approx(0, abs=1e-6)
    # cap-down: the wide 30x30 face is on the bed, the 23 mm tall part stands on it
    assert mesh.extents == pytest.approx([30, 30, 23], abs=0.01)
    assert np.ptp(mesh.vertices[mesh.vertices[:, 2] < 1e-6][:, :2], axis=0) == pytest.approx([30, 30], abs=0.01)
    t = np.array(p["transform"])
    assert t.shape == (4, 4) and np.linalg.det(t[:3, :3]) == pytest.approx(1)


def test_best_orientation_unit():
    stem = trimesh.creation.box([6, 6, 20], transform=trimesh.transformations.translation_matrix([0, 0, 10]))
    cap = trimesh.creation.box([30, 30, 3], transform=trimesh.transformations.translation_matrix([0, 0, 21.5]))
    mesh = trimesh.util.concatenate([stem, cap])
    t = best_orientation(mesh, 45)
    assert t.shape == (4, 4)
    moved = mesh.copy()
    moved.apply_transform(t)
    assert moved.bounds[0][2] == pytest.approx(0, abs=1e-6)
    assert moved.extents[2] == pytest.approx(23)


def test_estimate_min_wall_unit():
    thin = trimesh.creation.box([20, 20, 0.5])
    assert estimate_min_wall(thin) == pytest.approx(0.5, abs=0.05)


def test_oversize_part_warns_but_still_writes(tmp_path):
    out = _built(tmp_path, _model([_box("big", [300, 300, 300])]))
    p = run_print_check(out)["parts"]["big"]
    assert p["fitsBed"] is False
    assert any("bed" in w for w in p["warnings"])
    assert (out / "print" / "big.stl").is_file()
    assert (out / "print" / "print-plate.3mf").is_file()


def test_plate_has_both_parts_without_overlap(tmp_path):
    out = tmp_path / "two"
    run_build(FIX / "two_parts.json", out)
    run_print_check(out)
    scene = trimesh.load(out / "print" / "print-plate.3mf")
    assert len(scene.geometry) == 2
    boxes = sorted((g.bounds for g in scene.dump()), key=lambda bb: bb[0][0])
    a, b = boxes
    assert min(a[0][0], b[0][0]) >= -1e-6 and min(a[0][1], b[0][1]) >= -1e-6
    separated = any(a[1][i] <= b[0][i] or b[1][i] <= a[0][i] for i in (0, 1))
    assert separated
    # 5 mm gap between neighbours in a row
    gap = max(b[0][0] - a[1][0], a[0][0] - b[1][0])
    assert gap == pytest.approx(5, abs=1e-3)


def test_printer_file_partial_override(tmp_path):
    out = _built(tmp_path, _model([_box("cube", [20, 20, 20])]))
    report = run_print_check(out, {"bedMm": [10, 10, 10]})
    assert report["printer"] == {**DEFAULT_PRINTER, "bedMm": [10, 10, 10]}
    assert report["parts"]["cube"]["fitsBed"] is False


@pytest.mark.parametrize("printer", [
    {"colour": 1}, {"bedMm": [1, 2]}, {"bedMm": [1, 2, -3]}, {"nozzleMm": 0},
    {"minWallMm": "x"}, {"overhangDeg": 90}, ["bedMm"]])
def test_bad_printer_rejected(tmp_path, printer):
    out = _built(tmp_path, _model([_box("cube", [20, 20, 20])]))
    with pytest.raises(ModelError):
        run_print_check(out, printer)
    assert not (out / "print").exists()


def test_existing_print_folder_refused(tmp_path):
    out = _built(tmp_path, _model([_box("cube", [20, 20, 20])]))
    run_print_check(out)
    with pytest.raises(ModelError, match="already exists"):
        run_print_check(out)


def test_cli_print_check(run_cli, tmp_path):
    out = _built(tmp_path, _model([_box("cube", [20, 20, 20])]))
    pf = tmp_path / "printer.json"
    pf.write_text('{"bedMm": [100, 100, 100]}')
    r = run_cli("print-check", out, "--printer", pf)
    assert r.returncode == 0, r.stderr
    assert json.loads((out / "print" / "print-report.json").read_text())["printer"]["bedMm"] == [100, 100, 100]


def test_cli_bad_printer_file(run_cli, tmp_path):
    out = _built(tmp_path, _model([_box("cube", [20, 20, 20])]))
    pf = tmp_path / "printer.json"
    pf.write_text('{"bogus": 1}')
    r = run_cli("print-check", out, "--printer", pf)
    assert r.returncode == 2 and "bogus" in r.stderr and "Traceback" not in r.stderr


BED = [220, 220, 250]


def _oriented(mesh, t):
    m = mesh.copy()
    m.apply_transform(t)
    return m


def test_tall_part_that_fits_upright_stays_upright():
    mesh = trimesh.creation.box([30, 30, 240])
    moved = _oriented(mesh, best_orientation(mesh, 45, BED))
    assert moved.extents == pytest.approx([30, 30, 240])


def test_too_tall_part_is_laid_down_to_fit():
    mesh = trimesh.creation.box([40, 40, 300])
    bed = [320, 50, 50]
    moved = _oriented(mesh, best_orientation(mesh, 45, bed))
    assert all(e <= b + 1e-6 for e, b in zip(moved.extents, bed))
    assert moved.extents[2] == pytest.approx(40)


def test_part_that_cannot_fit_falls_back_and_still_exports(tmp_path):
    out = _built(tmp_path, _model([_box("rod", [300, 20, 20])]))
    p = run_print_check(out)["parts"]["rod"]
    assert p["fitsBed"] is False
    assert (out / "print" / "rod.stl").is_file()


def test_fit_is_part_of_report(tmp_path):
    out = _built(tmp_path, _model([_box("post", [40, 40, 300])]))
    report = run_print_check(out, {"bedMm": [320, 50, 50]})
    assert report["parts"]["post"]["fitsBed"] is True


def test_overhang_regions():
    stem = trimesh.creation.box([6, 6, 20], transform=trimesh.transformations.translation_matrix([0, 0, 10]))
    cap = trimesh.creation.box([30, 30, 3], transform=trimesh.transformations.translation_matrix([0, 0, 21.5]))
    mesh = trimesh.util.concatenate([stem, cap])
    mesh.merge_vertices()
    regions = _overhang_regions(mesh, 45)
    assert len(regions) == 1
    assert regions[0]["areaMm2"] == pytest.approx(30 * 30)  # fixture shells are not unioned
    assert regions[0]["centerMm"][2] == pytest.approx(20)


def test_report_has_overhang_regions_key(tmp_path):
    out = _built(tmp_path, _model([_box("cube", [20, 20, 20])]))
    assert run_print_check(out)["parts"]["cube"]["overhangRegionsMm"] == []


def test_plate_with_counterbores_and_fillet_is_watertight(tmp_path):
    from sketchcad.build import run_build as build
    out = tmp_path / "df"
    build(FIX / "drawing_features.json", out)
    report = run_print_check(out)
    p = report["parts"]["plate"]
    assert p["watertight"] is True
    assert not [w for w in p["warnings"] if "watertight" in w]
    mesh = _stl(out, "plate")
    assert mesh.is_watertight and mesh.is_winding_consistent


def test_open_mesh_still_flagged(tmp_path):
    from sketchcad import printing
    box = trimesh.creation.box([10, 10, 10])
    box.update_faces(np.arange(len(box.faces)) != 0)  # drop one triangle
    _, report = printing._check_part("open", box, dict(DEFAULT_PRINTER))
    assert report["watertight"] is False
    assert any("watertight" in w for w in report["warnings"])
