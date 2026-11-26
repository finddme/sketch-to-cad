import copy
import json
from pathlib import Path

import pytest

from sketchcad.errors import ModelError
from sketchcad.schema import validate

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def model():
    return json.loads((FIXTURES / "minimal.json").read_text())


def _set(m, fn):
    fn(m)
    return m


def test_minimal_valid_and_defaults(model, tmp_path):
    out = validate(model, tmp_path)
    assert out["placement"] == []
    assert out["parts"][0]["finish"] == model["parts"][0]["finish"]
    assert out["viewer"] == {"wireColor": "#6E6A68"}
    assert "viewer" not in model and "placement" not in model  # input untouched


def test_part_colors_valid(model, tmp_path):
    pid = model["parts"][0]["id"]
    model["viewer"] = {"partColors": {pid: "#A1b2C3"}}
    out = validate(model, tmp_path)
    assert out["viewer"] == {"wireColor": "#6E6A68", "partColors": {pid: "#A1b2C3"}}


def test_part_materials_valid(model, tmp_path):
    pid = model["parts"][0]["id"]
    for value in ("matte", "satin", "gloss", "metal"):
        model["viewer"] = {"partMaterials": {pid: value}}
        out = validate(model, tmp_path)
        assert out["viewer"] == {"wireColor": "#6E6A68", "partMaterials": {pid: value}}


def test_span_marked_dimension_valid(model, tmp_path):
    del model["dimensions"][0]["feature"]
    model["dimensions"][0]["span"] = "width"
    assert validate(model, tmp_path)["dimensions"][0]["span"] == "width"


def test_revolve_arcs_profile_valid(model, tmp_path):
    model["parts"][0]["features"].append(
        {"id": "rev", "operation": "union", "kind": "revolve",
         "profile": {"start": [0, -5], "segments": [{"to": [0, 5], "through": [5, 0]}, {"to": [0, -5]}]}})
    validate(model, tmp_path)


def test_finish_origin_valid(model, tmp_path):
    model["parts"][0]["finish"][0]["origin"] = "assumed"
    assert validate(model, tmp_path)["parts"][0]["finish"][0]["origin"] == "assumed"


def test_finish_defaults_to_empty(model, tmp_path):
    del model["parts"][0]["finish"]
    assert validate(model, tmp_path)["parts"][0]["finish"] == []


def _arcs(m, segments, **extra):
    m["parts"][0]["features"][0] = {
        "id": "base", "operation": "base", "kind": "extrude", "depthMm": 5,
        "profile": {"plane": "XY", "arcs": {"start": [0, 0], "segments": segments}, **extra},
    }


CASES = [
    ("units", lambda m: m.update(units="cm"), "units"),
    ("origin", lambda m: m["dimensions"][0].update(origin="guess"), r"dimensions\[0\]\.origin"),
    (
        "first-cut",
        lambda m: m["parts"][0]["features"][0].update(operation="cut"),
        r"parts\[0\]\.features\[0\]\.operation",
    ),
    (
        "dup-part",
        lambda m: m["parts"].append(copy.deepcopy(m["parts"][0])),
        r"parts\[1\]\.id.*duplicate",
    ),
    (
        "pattern-forward-ref",
        lambda m: m["parts"][0]["features"].insert(
            1,
            {"id": "p", "operation": "union", "kind": "pattern", "of": "cable",
             "linear": {"countX": 2, "stepMm": [10, 0, 0]}},
        ),
        r"parts\[0\]\.features\[1\]\.of",
    ),
    (
        "code-missing",
        lambda m: m["parts"].append(
            {"id": "knob", "construction": "code", "code": {"file": "parts/knob.py"}}
        ),
        r"parts\[1\]\.code\.file.*not found",
    ),
    (
        "code-escape",
        lambda m: m["parts"].append(
            {"id": "knob", "construction": "code", "code": {"file": "../evil.py"}}
        ),
        r"parts\[1\]\.code\.file.*escapes",
    ),
    (
        "code-with-features",
        lambda m: m["parts"].append(
            {"id": "knob", "construction": "code", "code": {"file": "k.py"}, "features": []}
        ),
        r"parts\[1\]\.features.*not allowed",
    ),
    (
        "hollow-no-shell",
        lambda m: m["parts"][0].update(construction="hollow"),
        r"parts\[0\]\.finish.*shell",
    ),
    (
        "edges-front",
        lambda m: m["parts"][0]["finish"][0].update(edges="front"),
        r"parts\[0\]\.finish\[0\]\.edges",
    ),
    (
        "edges-unknown-feature",
        lambda m: m["parts"][0]["finish"][0].update(edges="feature:nope"),
        r"finish\[0\]\.edges.*nope",
    ),
    (
        "radius-zero",
        lambda m: m["parts"][0]["finish"][0].update(radiusMm=0),
        r"parts\[0\]\.finish\[0\]\.radiusMm",
    ),
    ("wire-color", lambda m: m.update(viewer={"wireColor": "red"}), r"viewer\.wireColor"),
    ("part-colors-not-object", lambda m: m.update(viewer={"partColors": ["#112233"]}), r"viewer\.partColors"),
    (
        "part-colors-unknown-part",
        lambda m: m.update(viewer={"partColors": {"nope": "#112233"}}),
        r"viewer\.partColors\.nope: unknown part id",
    ),
    (
        "part-colors-bad-hex",
        lambda m: m.update(viewer={"partColors": {m["parts"][0]["id"]: "#12345"}}),
        r"viewer\.partColors\.\w+: must be #RRGGBB",
    ),
    (
        "part-materials-not-object",
        lambda m: m.update(viewer={"partMaterials": ["metal"]}),
        r"viewer\.partMaterials: ",
    ),
    (
        "part-materials-unknown-part",
        lambda m: m.update(viewer={"partMaterials": {"nope": "metal"}}),
        r"viewer\.partMaterials\.nope: unknown part id",
    ),
    (
        "part-materials-bad-value",
        lambda m: m.update(viewer={"partMaterials": {m["parts"][0]["id"]: "chrome"}}),
        r"viewer\.partMaterials\.\w+: must be one of matte, satin, gloss, metal, got 'chrome'",
    ),
    (
        "part-materials-not-string",
        lambda m: m.update(viewer={"partMaterials": {m["parts"][0]["id"]: 1}}),
        r"viewer\.partMaterials\.\w+: must be one of",
    ),
    ("name-not-slug", lambda m: m.update(name="Phone Stand"), "name"),
    ("empty-parts", lambda m: m.update(parts=[]), "parts"),
    (
        "nan-size",
        lambda m: m["parts"][0]["features"][0].update(sizeMm=[80, float("nan"), 6]),
        r"features\[0\]\.sizeMm\[1\]",
    ),
    (
        "zero-size",
        lambda m: m["parts"][0]["features"][0].update(sizeMm=[80, 0, 6]),
        r"features\[0\]\.sizeMm\[1\]",
    ),
    (
        "hole-no-depth",
        lambda m: m["parts"][0]["features"][1].pop("through"),
        r"features\[1\].*through, depthMm",
    ),
    (
        "placement-unknown-part",
        lambda m: m.update(placement=[{"part": "x", "translateMm": [0, 0, 0], "rotateDeg": [0, 0, 0]}]),
        r"placement\[0\]\.part",
    ),
    ("dim-unknown-part", lambda m: m["dimensions"][0].update(part="x"), r"dimensions\[0\]\.part"),
    (
        "dim-unknown-feature",
        lambda m: m["dimensions"][0].update(feature="x"),
        r"dimensions\[0\]\.feature",
    ),
    (
        "span-unknown",
        lambda m: m["dimensions"][0].pop("feature") and m["dimensions"][0].update(span="length"),
        r"dimensions\[0\]\.span: must be one of width, depth, height",
    ),
    (
        "span-and-feature",
        lambda m: m["dimensions"][0].update(span="width"),
        r"dimensions\[0\]: span and feature are mutually exclusive",
    ),
    (
        "span-without-part",
        lambda m: m["dimensions"][0].update(span="width", part=None) or m["dimensions"][0].pop("part")
        or m["dimensions"][0].pop("feature"),
        r"dimensions\[0\]\.span: requires 'part'",
    ),
    (
        "revolve-two-profiles",
        lambda m: m["parts"][0]["features"].append(
            {"id": "rev", "operation": "union", "kind": "revolve", "profileRzMm": [[0, 0], [5, 0], [5, 9]],
             "profile": {"start": [0, 0], "segments": [{"to": [5, 0]}, {"to": [5, 9]}]}}),
        r"features\[2\]: needs exactly one of profileRzMm, profile",
    ),
    (
        "revolve-arcs-negative-radius",
        lambda m: m["parts"][0]["features"].append(
            {"id": "rev", "operation": "union", "kind": "revolve",
             "profile": {"start": [0, 0], "segments": [{"to": [5, 0]}, {"to": [0, 9], "through": [-1, 4]}]}}),
        r"features\[2\]\.profile\.segments\[1\]\.through\[0\]: radius must be >= 0",
    ),
    (
        "finish-bad-origin",
        lambda m: m["parts"][0]["finish"][0].update(origin="guess"),
        r"parts\[0\]\.finish\[0\]\.origin: must be one of sketch, user, assumed",
    ),
    (
        "unknown-kind",
        lambda m: m["parts"][0]["features"][1].update(kind="spline"),
        r"features\[1\]\.kind",
    ),
    (
        "arcs-unknown-segment-key",
        lambda m: _arcs(m, segments=[{"to": [10, 0], "radius": 5}, {"to": [0, 10]}]),
        r"profile\.arcs\.segments\[0\].*unknown key",
    ),
    (
        "arcs-one-segment",
        lambda m: _arcs(m, segments=[{"to": [10, 0]}]),
        r"profile\.arcs\.segments.*at least 2",
    ),
    (
        "arcs-collinear",
        lambda m: _arcs(
            m, segments=[{"to": [10, 0]}, {"to": [10, 10]}, {"to": [10, 20], "through": [10, 15]}]
        ),
        r"profile\.arcs\.segments\[2\]: arc points are collinear",
    ),
    (
        "arcs-coincident",
        lambda m: _arcs(m, segments=[{"to": [10, 0], "through": [0, 0]}, {"to": [0, 10]}]),
        r"profile\.arcs\.segments\[0\]: arc points are collinear",
    ),
    (
        "profile-two-forms",
        lambda m: _arcs(m, [{"to": [10, 0]}, {"to": [0, 10]}], points=[[0, 0], [1, 0], [1, 1]]),
        r"profile: needs exactly one of points, circle, arcs",
    ),
    (
        "step-bad-sha",
        lambda m: m["parts"][0]["features"].append(
            {"id": "s", "operation": "union", "kind": "step", "file": "a.step", "sha256": "abc"}
        ),
        r"features\[2\]\.(sha256|file)",
    ),
]


@pytest.mark.parametrize("mutate,expected", [(c[1], c[2]) for c in CASES], ids=[c[0] for c in CASES])
def test_rejects(model, tmp_path, mutate, expected):
    mutate(model)
    with pytest.raises(ModelError, match=expected):
        validate(model, tmp_path)


def test_code_part_valid(model, tmp_path):
    (tmp_path / "parts").mkdir()
    (tmp_path / "parts" / "knob.py").write_text("")
    model["parts"].append(
        {"id": "knob", "construction": "code", "code": {"file": "parts/knob.py", "sha256": "a" * 64}}
    )
    model["placement"] = [{"part": "knob", "translateMm": [0, 0, 6], "rotateDeg": [0, 0, 0]}]
    assert validate(model, tmp_path)["parts"][1]["id"] == "knob"


def test_all_kinds_valid(model, tmp_path):
    (tmp_path / "a.step").write_text("")
    feats = model["parts"][0]["features"]
    feats += [
        {"id": "cyl", "operation": "union", "kind": "cylinder", "centerMm": [0, 0, 0], "radiusMm": 3, "heightMm": 5, "axis": "Z"},
        {"id": "sph", "operation": "union", "kind": "sphere", "centerMm": [0, 0, 0], "radiusMm": 3},
        {"id": "rev", "operation": "union", "kind": "revolve", "profileRzMm": [[0, 0], [5, 0], [5, 9]]},
        {"id": "lof", "operation": "union", "kind": "loft", "ruled": True, "sections": [
            {"kind": "ellipse", "plane": "XY", "originMm": [0, 0, 0], "radiusUMm": 5, "radiusVMm": 3},
            {"kind": "polygon", "pointsMm": [[0, 0, 9], [1, 0, 9], [1, 1, 9]]}]},
        {"id": "stp", "operation": "union", "kind": "step", "file": "a.step", "sha256": "f" * 64},
        {"id": "ext", "operation": "union", "kind": "extrude",
         "profile": {"plane": "XZ", "circle": {"centerMm": [0, 0], "radiusMm": 4}}, "depthMm": 3},
        {"id": "hol", "operation": "cut", "kind": "hole", "at": [1, 1, 0], "direction": "-Z",
         "diameterMm": 3, "depthMm": 2, "counterbore": {"diameterMm": 6, "depthMm": 1}},
        {"id": "lin", "operation": "union", "kind": "pattern", "of": "cyl",
         "linear": {"countX": 3, "countY": 2, "stepMm": [10, 10, 0]}},
        {"id": "pol", "operation": "union", "kind": "pattern", "of": "cyl",
         "polar": {"count": 4, "axis": "Z", "totalDeg": 360}},
    ]
    model["parts"][0]["finish"] += [
        {"kind": "chamfer", "distanceMm": 1, "edges": "feature:cyl"},
        {"kind": "fillet", "radiusMm": 1, "edges": "face:+Z"},
    ]
    model["parts"][0]["construction"] = "hollow"
    model["parts"][0]["finish"].append({"kind": "shell", "thicknessMm": 2, "openFace": "+Z"})
    validate(model, tmp_path)


def test_arcs_profile_valid(model, tmp_path):
    _arcs(model, segments=[{"to": [10, 0]}, {"to": [0, 10], "through": [8, 8]}, {"to": [0, 0]}])
    validate(model, tmp_path)


@pytest.mark.parametrize("linear", [{"countX": 1, "stepMm": [5, 0, 0]},
                                    {"countX": 1, "countY": 1, "stepMm": [5, 0, 0]}])
def test_linear_pattern_without_copies_rejected(model, tmp_path, linear):
    model["parts"][0]["features"].append(
        {"id": "pat", "operation": "union", "kind": "pattern",
         "of": model["parts"][0]["features"][0]["id"], "linear": linear})
    with pytest.raises(ModelError, match=r"linear: pattern must produce at least one copy"):
        validate(model, tmp_path)


def test_code_part_rejects_finish(model, tmp_path):
    (tmp_path / "k.py").write_text("")
    model["parts"].append({"id": "knob", "construction": "code", "code": {"file": "k.py"},
                           "finish": [{"kind": "fillet", "radiusMm": 1, "edges": "all-outer"}]})
    with pytest.raises(ModelError, match=r"parts\[1\]\.finish: code parts cannot have finishes"):
        validate(model, tmp_path)
    model["parts"][1]["finish"] = []
    validate(model, tmp_path)


@pytest.mark.parametrize("pid", ["assembly", "body-notes", "body-notes-2", "notes-notes"])
def test_reserved_part_ids(model, tmp_path, pid):
    model["parts"][0]["id"] = pid
    model["dimensions"][0]["part"] = pid
    with pytest.raises(ModelError, match=r"parts\[0\]\.id.*reserved"):
        validate(model, tmp_path)


@pytest.mark.parametrize("pid", ["notes", "notes-box", "assembly-base", "body-note"])
def test_part_ids_near_reserved_names_are_fine(model, tmp_path, pid):
    model["parts"][0]["id"] = pid
    model["dimensions"][0]["part"] = pid
    validate(model, tmp_path)


@pytest.mark.parametrize("where,expect", [
    (lambda m: m["dimensions"][0].update(label="width\nhidden"), r"dimensions\[0\]\.label"),
    (lambda m: m["assumptions"].append("ok\tthen tab"), r"assumptions\[1\]"),
    (lambda m: m["dimensions"][0].update(label="bell\x07"), r"dimensions\[0\]\.label"),
    (lambda m: m["parts"][0]["features"][0].update(id="base\x00"), r"parts\[0\]\.features\[0\]\.id"),
])
def test_control_characters_are_rejected(model, tmp_path, where, expect):
    where(model)
    with pytest.raises(ModelError, match=expect + ".*control character"):
        validate(model, tmp_path)
