import itertools
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest
from reportlab.graphics import renderPM

from sketchcad.build import load_manifest, load_part_shapes, run_build
from sketchcad.dims import fmt_mm, text_width
from sketchcad.drawing import Dim, arrange, collect_dims, layout_dims, run_drawing
from sketchcad.errors import ModelError
from sketchcad.project import project_view
from sketchcad.sheet import svg_drawing

FIX = Path(__file__).parent / "fixtures"
SVG_NS = "{http://www.w3.org/2000/svg}"
WALL = {"label": "벽 두께", "valueMm": 4, "origin": "assumed", "part": "box"}
WALL_NOTE = "벽 두께는 스케치에 없어 4mm로 가정"


def _build(tmp_path, fixture, edit=None):
    model = json.loads((FIX / fixture).read_text(encoding="utf-8"))
    if edit:
        edit(model)
    src = tmp_path / "model.json"
    src.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "out"
    run_build(src, out)
    return out


def _add_wall(model):
    model["dimensions"].append(dict(WALL))
    model["assumptions"].append(WALL_NOTE)


@pytest.fixture(scope="module")
def two_parts(tmp_path_factory):
    out = _build(tmp_path_factory.mktemp("d"), "two_parts.json", _add_wall)
    run_drawing(out)
    return out


@pytest.fixture(scope="module")
def features(tmp_path_factory):
    out = _build(tmp_path_factory.mktemp("f"), "drawing_features.json")
    model = json.loads((out / "model.json").read_text(encoding="utf-8"))
    return out, model, load_part_shapes(out)


def _part(model, pid):
    return next(p for p in model["parts"] if p["id"] == pid)


def _texts(svg):
    return [t.text or "" for t in ET.fromstring(svg).iter(f"{SVG_NS}text")]


def _intersects(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


# ---- outputs ---------------------------------------------------------------

def test_run_drawing_writes_sheets_and_manifest(two_parts):
    d = two_parts / "drawing"
    for name in ["box.svg", "box.pdf", "lid.svg", "lid.pdf", "assembly.svg", "assembly.pdf"]:
        assert (d / name).is_file(), name
    assert not (two_parts / "drawing.tmp").exists()
    m = load_manifest(two_parts)
    assert m["stages"] == {"build": "ok", "drawing": "ok"}
    assert m["drawing"]["projection"] == "third"
    assert [s["id"] for s in m["drawing"]["sheets"]] == ["box", "lid", "assembly", "assembly-parts"]
    assert m["drawing"]["sheets"][0]["svg"] == "drawing/box.svg"
    assert m["drawing"]["sheets"][0]["pdf"] == "drawing/box.pdf"
    assert m["parts"]["box"]["volumeMm3"] == pytest.approx(24000)  # other keys kept


def test_box_sheet_has_overall_dims_and_title_block(two_parts):
    svg = (two_parts / "drawing" / "box.svg").read_text(encoding="utf-8")
    texts = _texts(svg)
    for value in ("40", "30", "20"):
        assert value in texts, value
    # model dimension "box width 40" sits on the overall width span: drawn once in front
    assert texts.count("40") == 1
    assert "Outfit, 'Gowun Batang', sans-serif" in svg
    joined = " ".join(texts)
    scale = load_manifest(two_parts)["drawing"]["sheets"][0]["scale"]
    assert scale in texts
    for word in ("box-with-lid", "mm", "3각법"):
        assert word in joined, word


def test_lid_sheet_shows_cylinder_diameter(two_parts):
    texts = _texts((two_parts / "drawing" / "lid.svg").read_text(encoding="utf-8"))
    assert "Ø30" in texts and "5" in texts


def test_assumed_value_marked_and_listed(two_parts):
    svg = (two_parts / "drawing" / "box.svg").read_text(encoding="utf-8")
    texts = _texts(svg)
    assert "(4)*" in texts
    joined = " ".join(texts)
    assert "가정값" in joined and "벽 두께" in joined and WALL_NOTE in joined


def test_assembly_sheet_overall_dims_only(two_parts):
    texts = _texts((two_parts / "drawing" / "assembly.svg").read_text(encoding="utf-8"))
    assert {"40", "30", "25"} <= set(texts)
    assert "Ø30" not in texts


# ---- Hangul in PDF / raster -----------------------------------------------

def test_pdf_embeds_gowun_batang(two_parts):
    data = (two_parts / "drawing" / "box.pdf").read_bytes()
    assert data.startswith(b"%PDF")
    assert b"GowunBatang" in data


def _text_bbox_px(svg, needle, dpi):
    for t in ET.fromstring(svg).iter(f"{SVG_NS}text"):
        if t.text == needle:
            size = float(t.get("font-size"))
            x, y = float(t.get("x")), float(t.get("y"))
            w = text_width(needle, size)
            x0 = {"start": x, "middle": x - w / 2, "end": x - w}[t.get("text-anchor", "start")]
            k = dpi / 25.4
            return [round(v * k) for v in (x0, y - size * 0.8, x0 + w, y + size * 0.2)]
    raise AssertionError(f"{needle!r} not found")


def test_hangul_glyphs_render_not_tofu(two_parts):
    svg = (two_parts / "drawing" / "box.svg").read_text(encoding="utf-8")
    dpi = 200
    x0, y0, x1, y1 = _text_bbox_px(svg, "벽 두께", dpi)

    def crop(text):
        img = renderPM.drawToPIL(svg_drawing(text), dpi=dpi).convert("L")
        return np.asarray(img)[y0:y1, x0:x1].astype(float)

    real = crop(svg)
    tofu = crop(svg.replace("벽 두께", "□□□□"))
    assert real.size > 0
    assert (real < 128).mean() >= 0.02
    assert np.abs(real - tofu).mean() > 5


# ---- projection --------------------------------------------------------------

def test_project_view_uses_model_axes(features):
    _, _, shapes = features
    plate = shapes["plate"]
    top = project_view(plate, "top")
    assert top["extent"] == pytest.approx([0, 0, 80, 50], abs=1e-3)
    centers = {(round(c[0], 3), round(c[1], 3)) for c in top["circles"] if abs(c[2] - 2.5) < 1e-6}
    assert {(10, 10), (40, 10), (70, 10), (10, 40), (40, 40), (70, 40)} <= centers
    front = project_view(plate, "front")
    assert front["extent"] == pytest.approx([0, 0, 80, 6], abs=1e-3)
    assert front["hidden"], "through holes show as hidden lines in front"
    right = project_view(plate, "right")
    assert right["extent"] == pytest.approx([0, 0, 50, 6], abs=1e-3)
    iso = project_view(plate, "iso")
    assert iso["visible"] and len(iso["extent"]) == 4


# ---- dimension collection ----------------------------------------------------

def _labels(dims):
    return {(d.view, d.kind, d.label) for d in dims}


def test_collect_dims_feature_parameters(features):
    _, model, shapes = features
    plate = collect_dims(_part(model, "plate"), model, shapes["plate"])
    assert ("front", "h", "80") in _labels(plate)
    assert ("top", "v", "50") in _labels(plate)
    assert ("front", "v", "6") in _labels(plate)
    hole = [d for d in plate if d.kind == "dia"]
    assert [(d.view, d.label) for d in hole] == [("top", "6× Ø5")]
    assert hole[0].a == pytest.approx((10, 10))
    assert any(d.kind == "r" and d.label == "R1" for d in plate)
    drawn = [d.label for d in plate if d.view != "table"]
    assert drawn.count("80") == 1
    # a model dim without `feature` is never merged into a span by value: it is listed by name
    assert [(d.name, d.label) for d in plate if d.view == "table"] == [("plate width", "80")]

    bracket = collect_dims(_part(model, "bracket"), model, shapes["bracket"])
    labels = _labels(bracket)
    assert ("top", "v", "20") in labels  # extrude depth (XZ plane -> +Y), also overall depth
    assert ("front", "h", "Ø12") in labels  # boss cylinder
    assert ("front", "v", "(8)*") in labels  # assumed model dim at the boss bbox
    assert ("top", "dia", "Ø4") in labels

    knob = collect_dims(_part(model, "knob"), model, shapes["knob"])
    labels = _labels(knob)
    assert ("front", "h", "Ø24") in labels and ("front", "v", "20") in labels
    assert [d.label for d in knob].count("20") == 1


def test_revolve_arcs_profile_dims_and_axis():
    from sketchcad.drawing import center_lines
    from sketchcad.part import build_part
    c = 5 * 0.5 ** 0.5
    part = {"id": "ball", "construction": "solid", "finish": [], "features": [
        {"id": "head", "operation": "base", "kind": "revolve", "profile": {"start": [0, 0], "segments": [
            {"to": [5, 5], "through": [c, 5 - c]}, {"to": [0, 10], "through": [c, 5 + c]}]}}]}
    shape, _ = build_part(part, Path("."))
    dims = collect_dims(part, {"parts": [part], "dimensions": []}, shape)
    assert {"Ø10", "10"} <= {d.label for d in dims}
    assert center_lines(part, shape)["front"] == [((0.0, 0.0), (0.0, 10.0))]


def test_unmatched_model_dimension_goes_to_table(features):
    _, model, shapes = features
    m = json.loads(json.dumps(model))
    m["dimensions"].append({"label": "rib", "valueMm": 2.5, "origin": "user", "part": "plate"})
    dims = collect_dims(_part(m, "plate"), m, shapes["plate"])
    rib = [d for d in dims if d.name == "rib"]
    assert len(rib) == 1 and rib[0].view == "table" and rib[0].label == "2.5"


def test_overall_dims_show_measured_size_to_a_tenth():
    from build123d import Solid
    part = {"id": "p", "construction": "code"}
    model = {"parts": [part], "dimensions": []}
    dims = collect_dims(part, model, Solid.make_box(77.6064, 22.001, 10.26))
    assert {d.label for d in dims} == {"77.6", "22", "10.3"}


def test_stated_value_on_an_overall_span_keeps_its_precision():
    from sketchcad.part import build_part
    part = {"id": "p", "construction": "solid", "finish": [], "features": [
        {"id": "plate", "operation": "base", "kind": "extrude", "depthMm": 2.25,
         "profile": {"plane": "XY", "points": [[0, 0], [30, 0], [30, 20], [0, 20]]}}]}
    shape, _ = build_part(part, Path("."))
    dims = collect_dims(part, {"parts": [part], "dimensions": []}, shape)
    assert "2.25" in {d.label for d in dims} and "2.2" not in {d.label for d in dims}


def test_span_marked_dimension_marks_the_overall_span(tmp_path):
    def edit(model):
        model["dimensions"].append({"label": "box depth (reference)", "valueMm": 30, "origin": "assumed",
                                    "part": "box", "span": "depth"})
    out = _build(tmp_path, "two_parts.json", edit)
    model = json.loads((out / "model.json").read_text(encoding="utf-8"))
    dims = collect_dims(_part(model, "box"), model, load_part_shapes(out)["box"])
    drawn = [(d.view, d.kind, d.label) for d in dims if d.view != "table"]
    assert ("top", "v", "(30)*") in drawn and ("top", "v", "30") not in drawn
    assert [(d.name, d.label) for d in dims if d.view == "table"] == [("box depth (reference)", "(30)*")]


def _many_notes(model, notes=40, rows=15):
    filler = "this assumption is long enough to wrap over two or three lines of the notes column"
    model["assumptions"] += [f"note-{i:02d} {filler}" for i in range(notes)]
    model["dimensions"] += [{"label": f"dim-{i:02d}", "valueMm": i + 1, "origin": "assumed", "part": "box"}
                            for i in range(rows)]


def _all_texts(paths):
    return [t for p in paths for t in _texts(p.read_text(encoding="utf-8"))]


def test_notes_that_do_not_fit_continue_on_a_notes_sheet(tmp_path):
    out = _build(tmp_path, "two_parts.json", _many_notes)
    run_drawing(out)
    d = out / "drawing"
    sheet = next(s for s in load_manifest(out)["drawing"]["sheets"] if s["id"] == "box")
    extra = [out / n["svg"] for n in sheet["notesSheets"]]
    assert extra and extra[0] == d / "box-notes.svg"
    assert all(p.is_file() and p.with_suffix(".pdf").is_file() for p in extra)
    main = _texts((d / "box.svg").read_text(encoding="utf-8"))
    assert any("계속" in t and "box-notes.svg" in t for t in main)
    texts = _all_texts([d / "box.svg", *extra])
    assert not [t for t in texts if "…" in t]
    joined = " ".join(texts)
    for i in range(40):
        assert f"note-{i:02d}" in joined, i
    numbers = [t for t in texts if t[:-1].isdigit() and t.endswith(".")]
    assert numbers == [f"{i}." for i in range(1, len(numbers) + 1)] and len(numbers) >= 40
    for i in range(15):
        assert f"dim-{i:02d}" in texts, i


def test_notes_shrink_before_they_continue(tmp_path):
    out = _build(tmp_path, "two_parts.json", lambda m: _many_notes(m, notes=18, rows=0))
    run_drawing(out)
    sheet = next(s for s in load_manifest(out)["drawing"]["sheets"] if s["id"] == "box")
    assert sheet.get("notesSheets", []) == []
    root = ET.fromstring((out / "drawing" / "box.svg").read_text(encoding="utf-8"))
    sizes = {float(t.get("font-size")) for t in root.iter(f"{SVG_NS}text") if "note-0" in (t.text or "")}
    assert sizes and max(sizes) < 2.6
    texts = _texts((out / "drawing" / "box.svg").read_text(encoding="utf-8"))
    assert not [t for t in texts if "…" in t] and "note-17" in " ".join(texts)


def test_span_marking_reaches_the_same_span_in_every_view(tmp_path):
    # a Y-axis cylinder: its diameter is drawn in the top view and spans the same X as the overall width
    from sketchcad.part import build_part
    part = {"id": "drum", "construction": "solid", "finish": [], "features": [
        {"id": "body", "operation": "base", "kind": "cylinder", "centerMm": [0, 0, 0], "radiusMm": 20,
         "heightMm": 30, "axis": "Y"}]}
    model = {"parts": [part], "dimensions": [
        {"label": "drum width (reference)", "valueMm": 40, "origin": "assumed", "part": "drum", "span": "width"}]}
    shape, _ = build_part(part, Path("."))
    drawn = {(d.view, d.kind, d.label) for d in collect_dims(part, model, shape) if d.view != "table"}
    assert ("front", "h", "(40)*") in drawn and ("top", "h", "(Ø40)*") in drawn
    assert ("top", "h", "Ø40") not in drawn and ("front", "h", "40") not in drawn


def _finish_part(finish):
    return {"id": "p", "construction": "solid", "finish": finish, "features": [
        {"id": "b", "operation": "base", "kind": "box", "originMm": [0, 0, 0], "sizeMm": [30, 20, 10]}]}


def test_assumed_finishes_are_marked():
    from sketchcad.part import build_part
    part = _finish_part([{"kind": "fillet", "radiusMm": 0.8, "edges": "face:+Z", "origin": "assumed"},
                         {"kind": "chamfer", "distanceMm": 1, "edges": "face:-Z", "origin": "assumed"}])
    shape, _ = build_part(part, Path("."))
    labels = {d.label for d in collect_dims(part, {"parts": [part], "dimensions": []}, shape)}
    assert {"(R0.8)*", "(C1)*"} <= labels and not labels & {"R0.8", "C1"}


def test_finishes_without_origin_stay_unmarked():
    from sketchcad.part import build_part
    part = _finish_part([{"kind": "fillet", "radiusMm": 0.8, "edges": "face:+Z"},
                         {"kind": "chamfer", "distanceMm": 1, "edges": "face:-Z"}])
    shape, _ = build_part(part, Path("."))
    labels = {d.label for d in collect_dims(part, {"parts": [part], "dimensions": []}, shape)}
    assert {"R0.8", "C1"} <= labels


def test_dropped_iso_view_leaves_a_working_sheet_viewer_and_report(tmp_path, monkeypatch):
    import sketchcad.drawing as drawing
    from sketchcad.printing import run_print_check
    from sketchcad.report import write_report
    from sketchcad.viewer import run_viewer
    monkeypatch.setattr(drawing, "ISO_FACTORS", ())   # never fits with the iso in the grid ...
    monkeypatch.setattr(drawing, "ISO_MIN_FACTOR", 2.0)  # ... and never fits shrunk: dropped
    out = _build(tmp_path, "two_parts.json", _add_wall)
    run_drawing(out)
    svg = (out / "drawing" / "box.svg").read_text(encoding="utf-8")
    views = {g.get("data-view") for g in ET.fromstring(svg).iter(f"{SVG_NS}g")} - {None}
    assert views == {"front", "top", "right"}
    assert "(4)*" in _texts(svg)
    run_print_check(out)
    run_viewer(out)
    write_report(out)
    assert "data-item" in (out / "viewer" / "index.html").read_text(encoding="utf-8")  # the parts sheet
    assert load_manifest(out)["stages"]["report"] == "ok"


def test_table_title_is_never_left_alone_at_a_column_end():
    from sketchcad.sheet import flow_notes
    for n in range(30, 90):
        _size, main, pages = flow_notes([f"short note {i}" for i in range(n)], [("a", "1"), ("b", "2")])
        for col in [main] + [c for page in pages for c in page]:
            assert not col[-1][2], n  # last item of a column must not be a keep-with-next title


def test_format_values():
    assert fmt_mm(40.0) == "40" and fmt_mm(2.5) == "2.5" and fmt_mm(0.1 + 0.2) == "0.3"


# ---- layout ------------------------------------------------------------------

def test_hole_pattern_layout_has_no_overlaps(features):
    _, model, shapes = features
    for pid in ("plate", "bracket", "knob"):
        views = {v: project_view(shapes[pid], v) for v in ("front", "top", "right", "iso")}
        dims = collect_dims(_part(model, pid), model, shapes[pid])
        _boxes, placed, _scale = arrange(views, dims, "third")
        boxes = [p.bbox for p in placed if p.bbox is not None]
        assert len(boxes) == len([d for d in dims if d.view != "table"]), pid
        for a, b in itertools.combinations(boxes, 2):
            assert not _intersects(a, b), (pid, a, b)


def test_crowded_view_stacks_then_overflows_to_table():
    box = {"front": {"extent": [0, 0, 100, 20], "scale": 1.0, "origin": (50, 50)}}
    dims = [Dim("front", "h", (0, 0), (100, 0), "100", "geometry") for _ in range(20)]
    placed = layout_dims(dims, box)
    drawn = [p for p in placed if p.bbox is not None]
    tabled = [p for p in placed if p.bbox is None]
    assert drawn and tabled and len(drawn) + len(tabled) == 20
    assert all(p.side == "table" for p in tabled)
    for a, b in itertools.combinations([p.bbox for p in drawn], 2):
        assert not _intersects(a, b)


def _view_box(svg, view):
    for g in ET.fromstring(svg).iter(f"{SVG_NS}g"):
        if g.get("data-view") == view:
            return [float(v) for v in g.get("data-box").split()]
    raise AssertionError(view)


def test_projection_first_angle_moves_views(tmp_path):
    out = _build(tmp_path, "two_parts.json")
    run_drawing(out, projection="first")
    svg = (out / "drawing" / "box.svg").read_text(encoding="utf-8")
    front, top, right = (_view_box(svg, v) for v in ("front", "top", "right"))
    assert top[1] > front[3] and right[2] < front[0]
    assert "1각법" in " ".join(_texts(svg))
    assert load_manifest(out)["drawing"]["projection"] == "first"


def test_third_angle_view_positions(two_parts):
    svg = (two_parts / "drawing" / "box.svg").read_text(encoding="utf-8")
    front, top, right = (_view_box(svg, v) for v in ("front", "top", "right"))
    assert top[3] < front[1] and right[0] > front[2]
    assert top[0] == pytest.approx(front[0]) and right[1] == pytest.approx(front[1])


def test_bad_projection_rejected(two_parts):
    with pytest.raises(ModelError, match="projection: must be first or third"):
        run_drawing(two_parts, projection="second")


def test_existing_drawing_refused(two_parts):
    before = sorted(p.name for p in (two_parts / "drawing").iterdir())
    with pytest.raises(ModelError, match="already exists"):
        run_drawing(two_parts)
    assert sorted(p.name for p in (two_parts / "drawing").iterdir()) == before


def test_cli_drawing(run_cli, tmp_path):
    out = tmp_path / "o"
    assert run_cli("build", FIX / "two_parts.json", "--out", out).returncode == 0
    r = run_cli("drawing", out, "--projection", "sideways")
    assert r.returncode == 2 and "projection: must be first or third" in r.stderr
    r = run_cli("drawing", out, "--projection", "first")
    assert r.returncode == 0, r.stderr
    assert (out / "drawing" / "assembly.pdf").is_file()


def test_no_feature_dim_equal_to_overall_goes_to_table(tmp_path):
    def edit(model):
        model["dimensions"].append({"label": "벽 두께", "valueMm": 20, "origin": "assumed", "part": "box"})
    out = _build(tmp_path, "two_parts.json", edit)
    run_drawing(out)
    texts = _texts((out / "drawing" / "box.svg").read_text(encoding="utf-8"))
    assert "20" in texts and "(20)*" in texts and "벽 두께" in texts
    assert texts.index("(20)*") == texts.index("벽 두께") + 1  # same table row: name, value


def _views(front, top, right, iso):
    return {"front": {"extent": front}, "top": {"extent": top}, "right": {"extent": right},
            "iso": {"extent": iso}}


def test_scale_steps_through_one_to_two_and_a_half():
    views = _views([0, 0, 180, 20], [0, 0, 180, 20], [0, 0, 180, 20], [0, 0, 20, 20])
    _boxes, _placed, scale = arrange(views, [], "third")
    assert scale == "1:2.5"


def test_scale_follows_the_orthographic_views_and_the_iso_view_shrinks():
    from sketchcad.drawing import paper_box
    from sketchcad.sheet import DRAW_AREA
    views = _views([0, 0, 50, 50], [0, 0, 50, 50], [0, 0, 50, 50], [0, 0, 500, 500])
    boxes, _placed, scale = arrange(views, [], "third")
    assert scale == "1:1"
    assert boxes["iso"]["scale"] < 0.5
    iso = paper_box(boxes["iso"])
    ax0, ay0, ax1, ay1 = DRAW_AREA
    assert ax0 <= iso[0] and iso[2] <= ax1 and ay0 <= iso[1] and iso[3] <= ay1
    for v in ("front", "top", "right"):
        assert not _intersects(iso, paper_box(boxes[v])), v


def test_part_too_large_for_sheet_raises():
    huge = {"extent": [0, 0, 1e6, 1e6]}
    views = {v: dict(huge) for v in ("front", "top", "right", "iso")}
    with pytest.raises(ModelError, match="big: part too large to fit a drawing sheet at 1:200"):
        arrange(views, [], "third", "big")


def test_failed_manifest_write_leaves_no_drawing(tmp_path, monkeypatch):
    import sketchcad.drawing as drawing

    out = _build(tmp_path, "two_parts.json")
    before = (out / "manifest.json").read_bytes()

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(drawing, "write_manifest", boom)
    with pytest.raises(OSError):
        run_drawing(out)
    assert not (out / "drawing").exists() and not (out / "drawing.tmp").exists()
    assert (out / "manifest.json").read_bytes() == before


def test_no_private_project_references():
    root = Path(__file__).resolve().parent.parent
    banned = ["jo" + "mul", "cera" + "mic"]
    hits = []
    for base in ("skills", "tests"):
        for f in (root / base).rglob("*"):
            if f.is_file() and f.suffix in {".py", ".md", ".json", ".txt", ".mjs", ".js", ".html"}:
                text = f.read_text(encoding="utf-8", errors="ignore").lower()
                hits += [f"{f.relative_to(root)}: {w}" for w in banned if w in text]
    assert hits == []


def test_simplify_drops_only_points_within_tolerance():
    from sketchcad.sheet import simplify
    straight = [(x / 10, 0.0) for x in range(11)]
    assert simplify(straight) == [(0.0, 0.0), (1.0, 0.0)]
    corner = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]
    assert simplify(corner) == corner
    arc = [(10 * np.cos(t), 10 * np.sin(t)) for t in np.linspace(0, np.pi / 2, 200)]
    kept = simplify(arc, tol=0.01)
    assert kept[0] == arc[0] and kept[-1] == arc[-1] and 3 < len(kept) < len(arc)
    a, b = np.array(kept[:-1]), np.array(kept[1:])
    for p in np.array(arc):  # every dropped point stays within the tolerance of the kept polyline
        t = np.clip(np.einsum("ij,ij->i", p - a, b - a) / np.einsum("ij,ij->i", b - a, b - a), 0, 1)
        assert np.min(np.linalg.norm(a + (b - a) * t[:, None] - p, axis=1)) <= 0.01 + 1e-9


def test_short_arc_pieces_keep_the_full_turn_resolution():
    from build123d import Edge
    from sketchcad.project import CURVE_SEGMENTS, _sample
    full = Edge.make_circle(0.5)  # short enough that the per-mm rate stays below the floor
    sliver = Edge.make_circle(0.5, start_angle=0, end_angle=15)
    assert len(_sample(full, (0, 0))) - 1 == CURVE_SEGMENTS[0]
    assert 2 <= len(_sample(sliver, (0, 0))) - 1 < CURVE_SEGMENTS[0]


def test_sheet_groups_line_styles_instead_of_repeating_them(two_parts):
    root = ET.fromstring((two_parts / "drawing" / "box.svg").read_text(encoding="utf-8"))
    lines = list(root.iter(f"{SVG_NS}polyline"))
    assert lines and all(pl.get("stroke") is None for pl in lines[:20])
