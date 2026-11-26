"""report.md content, built from a hand-made output folder (no geometry needed)."""
import json

import pytest

from sketchcad.errors import ModelError
from sketchcad.report import write_report

STAGES = {"build": "ok", "drawing": "ok", "print-check": "ok", "viewer": "ok"}


def _out(tmp_path, model, stages=STAGES):
    out = tmp_path / "o"
    (out / "print").mkdir(parents=True)
    (out / "model.json").write_text(json.dumps(model), encoding="utf-8")
    (out / "manifest.json").write_text(json.dumps({"stages": dict(stages)}), encoding="utf-8")
    parts = {p["id"]: {"warnings": []} for p in model["parts"]}
    (out / "print" / "print-report.json").write_text(json.dumps({"parts": parts}), encoding="utf-8")
    return out


def _model(finish=(), dimensions=()):
    return {"name": "cup", "assumptions": [], "dimensions": list(dimensions), "parts": [
        {"id": "cup", "construction": "hollow", "finish": list(finish), "features": []}]}


def _rows(text):
    return [ln for ln in text.splitlines() if ln.startswith("| ")]


def test_assumed_finishes_are_listed_with_their_marked_value(tmp_path):
    model = _model(finish=[
        {"kind": "shell", "thicknessMm": 2.5, "openFace": "+Z", "origin": "assumed"},
        {"kind": "fillet", "radiusMm": 0.8, "edges": "face:+Z", "origin": "assumed"},
        {"kind": "chamfer", "distanceMm": 1, "edges": "face:-Z", "origin": "assumed"},
        {"kind": "fillet", "radiusMm": 3, "edges": "all-outer", "origin": "user"},
        {"kind": "chamfer", "distanceMm": 4, "edges": "all-outer"},
    ])
    text = write_report(_out(tmp_path, model)).read_text(encoding="utf-8")
    rows = _rows(text)
    assert any("cup" in r and "shell" in r and "| (2.5)* | assumed |" in r for r in rows)
    assert any("cup" in r and "fillet" in r and "face:+Z" in r and "| (R0.8)* | assumed |" in r for r in rows)
    assert any("cup" in r and "chamfer" in r and "face:-Z" in r and "| (C1)* | assumed |" in r for r in rows)
    assert not any("R3" in r or "C4" in r for r in rows)  # only assumed finishes are listed


def test_table_cells_escape_pipes(tmp_path):
    model = _model(dimensions=[{"label": "width | outer", "valueMm": 5, "origin": "assumed"}])
    text = write_report(_out(tmp_path, model)).read_text(encoding="utf-8")
    assert "| width \\| outer | (5)* | assumed |" in text


def test_report_refuses_incomplete_output(tmp_path):
    out = _out(tmp_path, _model(), stages={"build": "ok", "drawing": "ok"})
    with pytest.raises(ModelError) as e:
        write_report(out)
    assert "print-check" in str(e.value) and "viewer" in str(e.value)
    assert not (out / "report.md").exists()


def test_report_on_a_folder_that_is_not_an_output(tmp_path):
    with pytest.raises(ModelError, match="cannot build report"):
        write_report(tmp_path)
