import base64
import json
import re
from pathlib import Path

import numpy as np
import pytest
import trimesh

from sketchcad.build import load_manifest, load_part_shapes, place_shape, run_build
from sketchcad.drawing import run_drawing
from sketchcad.errors import ModelError
from sketchcad.viewer import PALETTE, placement_matrix, run_viewer
from sketchcad.wire import wire_guides

FIX = Path(__file__).parent / "fixtures"
DATA_RE = re.compile(
    r'<script type="application/json" id="sketchcad-data">(.*?)</script>', re.S)


def _build(tmp_path, fixture="two_parts.json"):
    out = tmp_path / "out"
    run_build(FIX / fixture, out)
    return out


def _data(html):
    m = DATA_RE.search(html)
    assert m, "sketchcad-data script missing"
    return json.loads(m.group(1))


def _points(wire):
    return np.array([p for g in wire for line in g["lines"] for p in line], dtype=float)


@pytest.fixture(scope="module")
def two_parts(tmp_path_factory):
    out = _build(tmp_path_factory.mktemp("v"))
    run_drawing(out)
    run_viewer(out)
    return out


@pytest.fixture(scope="module")
def html(two_parts):
    return (two_parts / "viewer" / "index.html").read_text(encoding="utf-8")


def test_viewer_is_single_small_file(two_parts):
    files = list((two_parts / "viewer").iterdir())
    assert [f.name for f in files] == ["index.html"]
    assert files[0].stat().st_size < 5 * 1024 * 1024
    assert not (two_parts / "viewer.tmp").exists()


def test_no_external_references(html):
    assert not re.search(r"""(?:src|href)\s*=\s*["']?\s*https?://""", html, re.I)
    assert "@import" not in html


def test_data_contract(html):
    data = _data(html)
    assert data["name"] == "box-with-lid"
    assert data["wireColor"] == "#6E6A68"
    assert [p["id"] for p in data["parts"]] == ["box", "lid"]
    for p in data["parts"]:
        assert base64.b64decode(p["glbBase64"])[:4] == b"glTF"
        assert p["wire"] and all(g["lines"] for g in p["wire"])
        assert len(p["placement"]) == 16
    assert [p["color"] for p in data["parts"]] == PALETTE[:2]  # default palette in part order
    assert [p["material"] for p in data["parts"]] == ["satin", "satin"]  # default finish
    assert data["partsSheetSvg"].lstrip().startswith("<svg")


def _glb_json(glb_bytes):
    length = int.from_bytes(glb_bytes[12:16], "little")
    return json.loads(glb_bytes[20:20 + length])


def test_viewer_meshes_are_retessellated_compactly(two_parts):
    """The viewer re-meshes each part for display: one primitive, positions only (the
    viewer derives normals), never the heavier build GLB verbatim."""
    data = _data((two_parts / "viewer" / "index.html").read_text(encoding="utf-8"))
    for p in data["parts"]:
        glb = base64.b64decode(p["glbBase64"])
        build_glb = (two_parts / "parts" / f"{p['id']}.glb").read_bytes()
        assert len(glb) < len(build_glb), p["id"]
        meta = _glb_json(glb)
        prims = [q for m in meta["meshes"] for q in m["primitives"]]
        assert len(prims) == 1 and set(prims[0]["attributes"]) == {"POSITION"}, p["id"]


def test_viewer_mesh_tolerance_scales_with_part_size():
    from build123d import Cylinder

    from sketchcad.viewer import viewer_glb

    def triangles(shape):
        meta = _glb_json(viewer_glb(shape))
        return meta["accessors"][meta["meshes"][0]["primitives"][0]["indices"]]["count"] // 3

    small, large = Cylinder(5, 10), Cylinder(500, 1000)  # same shape, 100x the size
    assert triangles(small) == pytest.approx(triangles(large), rel=0.3)
    assert triangles(small) >= 40  # curved surfaces stay smooth (angular tolerance)


def test_viewer_leaves_build_outputs_untouched(tmp_path):
    out = _build(tmp_path)
    before = {f: f.read_bytes() for f in (out / "parts").iterdir()}
    run_viewer(out)
    assert {f: f.read_bytes() for f in (out / "parts").iterdir()} == before


def test_multi_part_viewer_embeds_only_the_parts_sheet(two_parts, html):
    """A multi-part story opens on the parts sheet; the assembly and per-part sheets stay
    PDF/SVG deliverables and are not embedded."""
    data = _data(html)
    parts_svg = (two_parts / "drawing" / "assembly-parts.svg").read_text(encoding="utf-8")
    assert data["partsSheetSvg"] == parts_svg
    assert (two_parts / "drawing" / "assembly.svg").read_text(encoding="utf-8") not in html
    assert all(p["drawingSvg"] is None for p in data["parts"])
    assert "assemblySvg" not in data and data["sheetTopView"] is None


def test_parts_sheet_layout_reaches_the_viewer(two_parts, html):
    sheet = next(s for s in load_manifest(two_parts)["drawing"]["sheets"] if s["id"] == "assembly-parts")
    layout = _data(html)["partsSheet"]
    assert layout == sheet["parts"]
    assert sorted(p for it in layout["items"] for p in it["parts"]) == ["box", "lid"]


def test_single_part_viewer_embeds_its_sheet(tmp_path):
    out = _build(tmp_path, "minimal.json")
    run_drawing(out)
    run_viewer(out)
    data = _data((out / "viewer" / "index.html").read_text(encoding="utf-8"))
    assert data["parts"][0]["drawingSvg"].lstrip().startswith("<svg")


def test_bundle_and_data_are_inlined(html):
    assert "__SKETCHCAD_" not in html  # every template placeholder replaced
    # the inlined bundle and JSON never close their <script> element early
    assert len(re.findall(r"</script", html, re.I)) == len(re.findall(r"<script", html, re.I))
    body = DATA_RE.sub("", html)
    assert "GLTFLoader" in body or "glTF" in body  # three.js bundle is inline


def test_wire_matches_glb_in_viewer_frame(html):
    """Wire points are Y-up meters, in the same frame the GLB loads in."""
    for p in _data(html)["parts"]:
        pts = _points(p["wire"])
        glb = trimesh.load(trimesh.util.wrap_as_stream(base64.b64decode(p["glbBase64"])),
                           file_type="glb", force="scene")
        assert np.allclose(pts.min(axis=0), glb.bounds[0], atol=0.0005), p["id"]
        assert np.allclose(pts.max(axis=0), glb.bounds[1], atol=0.0005), p["id"]


def test_placement_matrix_matches_place_shape():
    pl = {"part": "x", "translateMm": [12, -7, 30], "rotateDeg": [30, 45, 60]}
    from build123d import Box
    shape = Box(10, 20, 30)
    placed = place_shape(shape, pl)
    m = np.array(placement_matrix(pl)).reshape(4, 4).T  # column-major -> row-major
    for v_src, v_dst in zip(shape.vertices(), placed.vertices()):
        src = np.array([v_src.X, v_src.Z, -v_src.Y, 1000.0]) / 1000.0
        dst = np.array([v_dst.X, v_dst.Z, -v_dst.Y]) / 1000.0
        assert np.allclose((m @ src)[:3], dst, atol=1e-9)


def test_wire_guides_default_policy(two_parts):
    box = load_part_shapes(two_parts)["box"]
    groups = wire_guides(box, {})
    assert groups[0]["kind"] == "brep-edge" and len(groups[0]["lines"]) == 12
    sections = [g for g in groups if g["kind"] == "section"]
    assert sorted({g["axis"] for g in sections}) == ["X", "Y", "Z"]
    assert sum(g["axis"] == "Z" for g in sections) == 6
    assert sum(g["axis"] == "X" for g in sections) == 3
    lid = wire_guides(load_part_shapes(two_parts)["lid"], {})
    assert len(lid[0]["lines"]) == 2  # two circles; the cylinder seam is not a guide
    pts = _points(groups)
    assert np.allclose(pts.min(axis=0), [0, 0, 0], atol=1e-6)
    assert np.allclose(pts.max(axis=0), [40, 30, 20], atol=1e-6)


def test_manifest_updated(two_parts):
    m = load_manifest(two_parts)
    assert m["stages"]["viewer"] == "ok"
    assert m["viewer"] == {"file": "viewer/index.html"}
    assert m["stages"]["drawing"] == "ok"  # earlier stages kept


def test_existing_viewer_refused(two_parts):
    before = (two_parts / "viewer" / "index.html").read_bytes()
    with pytest.raises(ModelError, match="already exists"):
        run_viewer(two_parts)
    assert (two_parts / "viewer" / "index.html").read_bytes() == before


def test_viewer_without_drawing_has_no_sheets(tmp_path):
    out = _build(tmp_path, "minimal.json")
    run_viewer(out)
    data = _data((out / "viewer" / "index.html").read_text(encoding="utf-8"))
    assert len(data["parts"]) == 1
    assert data["parts"][0]["drawingSvg"] is None and data["partsSheetSvg"] is None
    assert data["partsSheet"] is None
    assert data["parts"][0]["color"] == PALETTE[0]  # single part: first tone


def test_palette_is_curated_and_distinct():
    assert len(PALETTE) >= 6 and len(set(PALETTE)) == len(PALETTE)
    assert all(re.fullmatch(r"#[0-9A-F]{6}", c) for c in PALETTE)


def test_part_colors_override_palette(tmp_path):
    model = json.loads((FIX / "drawing_features.json").read_text())
    model["viewer"] = {"partColors": {"bracket": "#123456"}}
    src = tmp_path / "model.json"
    src.write_text(json.dumps(model))
    out = tmp_path / "out"
    run_build(src, out)
    run_viewer(out)
    data = _data((out / "viewer" / "index.html").read_text(encoding="utf-8"))
    colors = {p["id"]: p["color"] for p in data["parts"]}
    assert colors == {"plate": PALETTE[0], "bracket": "#123456", "knob": PALETTE[2]}


def test_part_materials_pass_through(tmp_path):
    model = json.loads((FIX / "drawing_features.json").read_text())
    model["viewer"] = {"partMaterials": {"bracket": "metal", "knob": "gloss"}}
    src = tmp_path / "model.json"
    src.write_text(json.dumps(model))
    out = tmp_path / "out"
    run_build(src, out)
    run_viewer(out)
    data = _data((out / "viewer" / "index.html").read_text(encoding="utf-8"))
    materials = {p["id"]: p["material"] for p in data["parts"]}
    assert materials == {"plate": "satin", "bracket": "metal", "knob": "gloss"}


def test_failed_manifest_write_leaves_no_viewer(tmp_path, monkeypatch):
    import sketchcad.viewer as viewer

    out = _build(tmp_path, "minimal.json")
    before = (out / "manifest.json").read_bytes()

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(viewer, "write_manifest", boom)
    with pytest.raises(OSError):
        run_viewer(out)
    assert not (out / "viewer").exists() and not (out / "viewer.tmp").exists()
    assert (out / "manifest.json").read_bytes() == before


def test_cli_viewer(run_cli, tmp_path):
    out = tmp_path / "o"
    assert run_cli("build", FIX / "two_parts.json", "--out", out).returncode == 0
    r = run_cli("viewer", out)
    assert r.returncode == 0, r.stderr
    assert (out / "viewer" / "index.html").is_file()
    assert "viewer/index.html" in r.stdout
    r = run_cli("viewer", out)
    assert r.returncode == 2 and "already exists" in r.stderr


def test_scene_has_a_preset_for_every_part_material():
    from sketchcad.schema import DEFAULT_PART_MATERIAL, PART_MATERIALS
    src = (Path(__file__).parents[1] / "skills/sketch-to-cad/assets/viewer/src/scene.mjs").read_text()
    block = re.search(r"\n  materials: \{\n(.*?)\n  \},", src, re.S).group(1)
    assert tuple(re.findall(r"^    (\w+): \{", block, re.M)) == PART_MATERIALS
    assert f"defaultMaterial: '{DEFAULT_PART_MATERIAL}'" in src


def _model_with_sketch(tmp_path, rel, image=None, size=(2000, 1000), mode="L"):
    """minimal.json with source.images[0].file = rel; writes `image` there when given."""
    from PIL import Image

    model_dir = tmp_path / "m"
    model_dir.mkdir()
    model = json.loads((FIX / "minimal.json").read_text())
    model["source"] = {"images": [{"file": rel}]}
    if image == "png":
        img = Image.new(mode, size, "white")
        img.paste("black" if mode != "RGBA" else (0, 0, 0, 255), (100, 100, 400, 140))
        (model_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        img.save(model_dir / rel)
    elif image is not None:
        (model_dir / rel).write_bytes(image)
    src = model_dir / "model.json"
    src.write_text(json.dumps(model))
    return src


def test_sketch_is_copied_and_embedded(tmp_path):
    src = _model_with_sketch(tmp_path, "scans/sketch.png", "png")
    out = tmp_path / "out"
    run_build(src, out)
    m = load_manifest(out)
    assert m["sourceImage"] == "source/sketch.png"
    assert (out / "source" / "sketch.png").read_bytes() == (src.parent / "scans/sketch.png").read_bytes()
    run_viewer(out)
    sketch = _data((out / "viewer" / "index.html").read_text(encoding="utf-8"))["sketch"]
    assert (sketch["width"], sketch["height"]) == (1400, 700)  # long side capped, aspect kept
    mime, b64 = re.fullmatch(r"data:(image/(?:webp|png));base64,(.+)", sketch["src"]).groups()
    raw = base64.b64decode(b64)
    assert raw[:4] == (b"RIFF" if mime == "image/webp" else b"\x89PNG")
    assert len(raw) < 250_000


def test_sketch_with_alpha_is_put_on_white(tmp_path):
    src = _model_with_sketch(tmp_path, "s.png", "png", size=(300, 400), mode="RGBA")
    out = tmp_path / "out"
    run_build(src, out)
    run_viewer(out)
    sketch = _data((out / "viewer" / "index.html").read_text(encoding="utf-8"))["sketch"]
    assert (sketch["width"], sketch["height"]) == (300, 400)  # small images are not upscaled


@pytest.mark.parametrize("rel, image", [
    ("missing.png", None),           # file not there
    ("../outside.png", "png"),       # leaves the model folder
    ("notes.png", b"not an image"),  # Pillow cannot read it
])
def test_unusable_sketch_is_skipped(tmp_path, rel, image):
    if rel.startswith(".."):
        (tmp_path / "m").mkdir()
        from PIL import Image
        Image.new("L", (10, 10)).save(tmp_path / "outside.png")
        model = json.loads((FIX / "minimal.json").read_text())
        model["source"] = {"images": [{"file": rel}]}
        src = tmp_path / "m" / "model.json"
        src.write_text(json.dumps(model))
    else:
        src = _model_with_sketch(tmp_path, rel, image)
    out = tmp_path / "out"
    run_build(src, out)
    assert "sourceImage" not in load_manifest(out)
    assert not (out / "source").exists()
    run_viewer(out)
    assert _data((out / "viewer" / "index.html").read_text(encoding="utf-8"))["sketch"] is None


def test_viewer_without_source_has_no_sketch(html):
    assert _data(html)["sketch"] is None


def test_story_sheet_carries_its_top_view_layout(tmp_path):
    """A single part's story lays its sheet on the floor so the printed top view lies under
    the model: the layout it gets must match the view drawn in the SVG."""
    out = _build(tmp_path, "minimal.json")
    run_drawing(out)
    run_viewer(out)
    html = (out / "viewer" / "index.html").read_text(encoding="utf-8")
    sheet = load_manifest(out)["drawing"]["sheets"][0]
    top = sheet["topView"]
    assert _data(html)["sheetTopView"] == top
    svg = (out / sheet["svg"]).read_text(encoding="utf-8")
    x0, y0, x1, y1 = map(float, re.search(r'data-view="top" data-box="([^"]+)"', svg).group(1).split())
    u0, v0, u1, v1 = top["extent"]
    assert (x0, y0) == pytest.approx(tuple(top["origin"]), abs=1e-3)
    assert (x1, y1) == pytest.approx((x0 + (u1 - u0) * top["scale"], y0 + (v1 - v0) * top["scale"]), abs=2e-3)
    # the top view spans the part's X/Y bounds
    from sketchcad.build import glb_bounds_mm
    b = glb_bounds_mm(out / "assembly.glb")
    assert [u0, v0, u1, v1] == pytest.approx([b["min"][0], b["min"][1], b["max"][0], b["max"][1]], abs=0.1)


def test_viewer_without_drawing_has_no_top_view(tmp_path):
    out = _build(tmp_path, "minimal.json")
    run_viewer(out)
    assert _data((out / "viewer" / "index.html").read_text(encoding="utf-8"))["sheetTopView"] is None
