import copy
import json
from pathlib import Path

import pytest

from sketchcad.bom import DENSITIES, bom_rows, format_mass, mass_grams
from sketchcad.errors import ModelError
from sketchcad.schema import validate

FIX = Path(__file__).parent / "fixtures"


def _model(ids):
    base = json.loads((FIX / "two_parts.json").read_text(encoding="utf-8"))
    box = base["parts"][0]
    base["parts"] = [dict(copy.deepcopy(box), id=i) for i in ids]
    base["placement"] = []
    base["dimensions"] = []
    return base


def _bounds(ids, size=(10, 20, 30)):
    return {i: {"min": [0, 0, 0], "max": list(size)} for i in ids}


IDS = ["base", "screw-1", "screw-2", "screw-3", "knob"]


def test_bom_is_optional(tmp_path):
    out = validate(_model(IDS), tmp_path)
    assert "bom" not in out


def test_bom_valid(tmp_path):
    m = _model(IDS)
    m["bom"] = [{"item": 1, "name": "받침", "nameEn": "base", "parts": ["base"], "material": "주철",
                 "kind": "cast-iron", "size": "Ø80 × 12", "role": "전체를 받친다"},
                {"item": 2, "name": "나사", "parts": ["screw-1", "screw-2", "screw-3"]}]
    assert validate(m, tmp_path)["bom"] == m["bom"]


@pytest.mark.parametrize("row, message", [
    ({"item": 1, "name": "x", "parts": ["nope"]}, "unknown part id 'nope'"),
    ({"item": 0, "name": "x", "parts": ["base"]}, "integer >= 1"),
    ({"item": 1, "name": "", "parts": ["base"]}, "non-empty string"),
    ({"item": 1, "name": "x", "parts": []}, "non-empty list"),
    ({"item": 1, "name": "x", "parts": ["base"], "kind": "unobtainium"}, "must be one of"),
    ({"item": 1, "name": "x", "parts": ["base"], "role": 3}, "must be a string"),
    ({"item": 1, "name": "x", "parts": ["base"], "colour": "red"}, "unknown field 'colour'"),
])
def test_bad_bom_row_rejected(tmp_path, row, message):
    m = _model(IDS)
    m["bom"] = [row]
    with pytest.raises(ModelError, match=message):
        validate(m, tmp_path)


def test_part_in_two_rows_rejected(tmp_path):
    m = _model(IDS)
    m["bom"] = [{"item": 1, "name": "a", "parts": ["base"]}, {"item": 2, "name": "b", "parts": ["knob", "base"]}]
    with pytest.raises(ModelError, match=r"bom\[1\]\.parts\[1\]: part 'base' is already in item 1"):
        validate(m, tmp_path)


def test_duplicate_item_rejected(tmp_path):
    m = _model(IDS)
    m["bom"] = [{"item": 1, "name": "a", "parts": ["base"]}, {"item": 1, "name": "b", "parts": ["knob"]}]
    with pytest.raises(ModelError, match=r"bom\[1\]\.item: duplicate item 1"):
        validate(m, tmp_path)


def test_derived_rows_group_numbered_copies():
    rows = bom_rows(_model(IDS), _bounds(IDS))
    assert [(r["item"], r["name"], r["parts"], r["qty"]) for r in rows] == [
        (1, "base", ["base"], 1), (2, "screw", ["screw-1", "screw-2", "screw-3"], 3), (3, "knob", ["knob"], 1)]
    assert all(r["derived"] and r["size"] == "10 × 20 × 30" and r["material"] == "" for r in rows)


def test_given_rows_come_first_and_the_rest_is_derived():
    m = _model(IDS)
    m["bom"] = [{"item": 5, "name": "손잡이", "parts": ["knob"], "size": "Ø12 × 9"},
                {"item": 2, "name": "받침", "parts": ["base"]}]
    rows = bom_rows(m, _bounds(IDS))
    assert [(r["item"], r["name"], r["qty"], r["derived"]) for r in rows] == [
        (2, "받침", 1, False), (5, "손잡이", 1, False), (6, "screw", 3, True)]
    assert rows[1]["size"] == "Ø12 × 9" and rows[0]["size"] == "10 × 20 × 30"


def test_lone_numbered_part_is_not_a_group():
    rows = bom_rows(_model(["arm-1", "arm-2", "pin-1"]), _bounds(["arm-1", "arm-2", "pin-1"]))
    assert [(r["name"], r["qty"]) for r in rows] == [("arm", 2), ("pin-1", 1)]


def test_mass_uses_the_material_density():
    m = _model(IDS)
    m["bom"] = [{"item": 1, "name": "a", "parts": ["base"], "kind": "steel"}]
    rows = bom_rows(m, _bounds(IDS))
    volumes = {i: 1000.0 for i in IDS}  # 1 cm3 each
    grams, defaulted = mass_grams(rows, volumes)
    assert grams == pytest.approx(DENSITIES["steel"] + 4 * DENSITIES["pla"])
    assert defaulted is True
    m["bom"] = [{"item": 1, "name": "a", "parts": IDS, "kind": "brass"}]
    grams, defaulted = mass_grams(bom_rows(m, _bounds(IDS)), volumes)
    assert grams == pytest.approx(5 * DENSITIES["brass"]) and defaulted is False


def test_format_mass():
    assert format_mass(812.4) == "약 810 g"
    assert format_mass(2440) == "약 2.4 kg"
    assert format_mass(7.26) == "약 7.3 g"
