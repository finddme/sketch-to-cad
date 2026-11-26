import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from sketchcad.build import load_manifest, load_part_shapes, run_build
from sketchcad.drawing import run_drawing
from sketchcad.partsheet import ROTATE_RATIO, item_view, pick_view

FIX = Path(__file__).parent / "fixtures"
SVG_NS = "{http://www.w3.org/2000/svg}"
PEG = {"construction": "solid", "features": [
    {"id": "peg", "operation": "base", "kind": "cylinder", "centerMm": [0, 0, 0], "radiusMm": 2, "heightMm": 16,
     "axis": "Z"}]}


def _with_pegs(model):
    model["parts"] += [dict(PEG, id="peg-1"), dict(PEG, id="peg-2")]
    model["placement"] += [{"part": "peg-1", "translateMm": [5, 5, 20], "rotateDeg": [0, 0, 0]},
                           {"part": "peg-2", "translateMm": [35, 5, 20], "rotateDeg": [0, 0, 0]}]
    model["bom"] = [{"item": 1, "name": "상자", "nameEn": "box", "parts": ["box"], "material": "참나무, 기름 마감",
                     "kind": "hardwood", "role": "물건을 담는다"}]


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    model = json.loads((FIX / "two_parts.json").read_text(encoding="utf-8"))
    _with_pegs(model)
    src = tmp_path_factory.mktemp("p") / "model.json"
    src.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
    o = src.parent / "out"
    run_build(src, o)
    run_drawing(o)
    return o


def _sheet(out):
    return next(s for s in load_manifest(out)["drawing"]["sheets"] if s["id"] == "assembly-parts")


def _texts(svg):
    return [t.text or "" for t in ET.fromstring(svg).iter(f"{SVG_NS}text")]


def test_parts_sheet_written_with_its_layout(out):
    sheet = _sheet(out)
    assert (out / sheet["svg"]).is_file() and (out / sheet["pdf"]).read_bytes().startswith(b"%PDF")
    rec = sheet["parts"]
    assert rec["paper"] == [420.0, 297.0]
    assert [(i["item"], i["parts"]) for i in rec["items"]] == [(1, ["box"]), (2, ["lid"]), (3, ["peg-1", "peg-2"])]
    assert len({i["scale"] for i in rec["items"]}) == 1  # one common scale
    x0, y0, x1, y1 = rec["stand"]
    assert x1 > x0 and y1 > y0


def test_items_sit_where_the_svg_draws_them(out):
    sheet = _sheet(out)
    svg = (out / sheet["svg"]).read_text(encoding="utf-8")
    for it in sheet["parts"]["items"]:
        box = re.search(rf'data-item="{it["item"]}" data-box="([^"]+)"', svg).group(1)
        bx0, by0, bx1, by1 = map(float, box.split())
        u0, v0, u1, v1 = it["extent"]
        assert (bx0, by0) == pytest.approx(tuple(it["origin"]), abs=1e-3)
        assert (bx1 - bx0, by1 - by0) == pytest.approx(((u1 - u0) * it["scale"], (v1 - v0) * it["scale"]), abs=1e-3)


def test_item_extent_is_the_part_seen_along_its_axes(out):
    """u = P . right and v = P . up over the part's own bounding box give the printed extent."""
    manifest = load_manifest(out)
    for it in _sheet(out)["parts"]["items"]:
        b = manifest["parts"][it["parts"][0]]["boundsMm"]
        corners = [(x, y, z) for x in (b["min"][0], b["max"][0]) for y in (b["min"][1], b["max"][1])
                   for z in (b["min"][2], b["max"][2])]
        us = [sum(c * r for c, r in zip(p, it["right"])) for p in corners]
        vs = [sum(c * u for c, u in zip(p, it["up"])) for p in corners]
        assert [min(us), min(vs), max(us), max(vs)] == pytest.approx(it["extent"], abs=0.05)


def test_balloons_table_and_summary(out):
    svg = (out / _sheet(out)["svg"]).read_text(encoding="utf-8")
    texts = _texts(svg)
    assert svg.count("data-balloon=") == 3
    assert "×2" in texts                      # the two pegs are one row
    for word in ("상자", "box", "참나무, 기름 마감", "물건을 담는다", "lid", "peg", "부품 배치도 PARTS"):
        assert word in texts, word
    assert "4" in texts and "3종" in texts    # 4 parts, 3 unique
    assert any(t.startswith("약 ") and t.endswith("g") for t in texts)
    assert "40 × 30 × 20" in texts           # a row without a size shows its part's bounding box
    assert "40 × 30 × 36 mm" in texts        # overall size of the assembly


def test_assembly_sheet_points_at_the_parts_sheet(out):
    texts = " ".join(_texts((out / "drawing" / "assembly.svg").read_text(encoding="utf-8")))
    assert "assembly-parts" in texts and "peg-1, peg-2" not in texts


def test_single_part_has_no_parts_sheet(tmp_path):
    o = tmp_path / "o"
    run_build(FIX / "minimal.json", o)
    run_drawing(o)
    assert [s["id"] for s in load_manifest(o)["drawing"]["sheets"]] == ["part"] or \
        all(s["id"] != "assembly-parts" for s in load_manifest(o)["drawing"]["sheets"])


@pytest.mark.parametrize("size, view", [((30, 30, 4), "top"), ((4, 30, 4), "top"), ((4, 4, 30), "front"),
                                        ((30, 4, 4), "front"), ((4, 20, 30), "right"),
                                        ((50, 50, 40), "front"),   # round knob: profile, not end-on
                                        ((24, 24, 3), "top")])     # wheel: face on
def test_view_with_the_largest_silhouette(size, view):
    assert pick_view({"min": [0, 0, 0], "max": list(size)}) == view


def test_tall_view_is_turned_to_lie_along_the_sheet(out):
    shapes = load_part_shapes(out)
    b = load_manifest(out)["parts"]["peg-1"]["boundsMm"]
    v = item_view({"id": "peg-1", **PEG}, shapes["peg-1"], b)
    u0, v0, u1, v1 = v["extent"]
    assert v["view"] == "front" and u1 - u0 > ROTATE_RATIO * (v1 - v0)
    assert v["right"] == [0, 0, 1] and v["up"] == [-1, 0, 0]
