import json
from pathlib import Path

from sketchcad.build import load_manifest

FIX = Path(__file__).parent / "fixtures"


def _model(tmp_path):
    m = json.loads((FIX / "two_parts.json").read_text(encoding="utf-8"))
    m["assumptions"] = ["lid height not in the sketch, assumed 5 mm"]
    m["dimensions"].append(
        {"label": "lid height", "valueMm": 5, "origin": "assumed", "part": "lid", "feature": "disc"})
    p = tmp_path / "model.json"
    p.write_text(json.dumps(m), encoding="utf-8")
    return p


def test_all_builds_everything_and_reports(run_cli, tmp_path):
    out = tmp_path / "o"
    r = run_cli("all", _model(tmp_path), "--out", out)
    assert r.returncode == 0, r.stderr
    for rel in ["model.json", "manifest.json", "assembly.step", "assembly.glb",
                "parts/box.step", "parts/lid.glb", "drawing/box.svg", "drawing/assembly.pdf",
                "print/box.stl", "print/print-plate.3mf", "print/print-report.json",
                "viewer/index.html", "report.md"]:
        assert (out / rel).is_file(), rel
    assert str(out / "report.md") in r.stdout
    text = (out / "report.md").read_text(encoding="utf-8")
    assert "assumed" in text and "(5)*" in text
    assert "print-plate.3mf" in text and "viewer/index.html" in text
    assert "lid height not in the sketch" in text
    heads = [ln for ln in text.splitlines() if ln.startswith("## ")]
    assert len(heads) == 5
    assert load_manifest(out)["stages"] == {
        "build": "ok", "drawing": "ok", "print-check": "ok", "viewer": "ok", "report": "ok"}


def test_report_is_rewritable(run_cli, tmp_path):
    from sketchcad.report import write_report
    out = tmp_path / "o"
    assert run_cli("all", _model(tmp_path), "--out", out).returncode == 0
    first = (out / "report.md").read_text(encoding="utf-8")
    assert write_report(out) == out / "report.md"
    assert (out / "report.md").read_text(encoding="utf-8") == first


def test_bad_projection_fails_before_build(run_cli, tmp_path):
    out = tmp_path / "o"
    r = run_cli("all", FIX / "two_parts.json", "--out", out, "--projection", "second")
    assert r.returncode == 2
    assert "projection" in r.stderr
    assert not out.exists() and not (tmp_path / "o.building").exists()


def _runmod():
    import importlib.util
    from conftest import RUN_PY
    spec = importlib.util.spec_from_file_location("sketchcad_run_all", RUN_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_failed_stage_keeps_earlier_output_and_can_be_resumed(tmp_path, monkeypatch, capsys):
    import sketchcad.printing as printing
    from sketchcad.errors import ModelError
    run = _runmod()
    real = printing.run_print_check

    def broken(*a, **k):
        raise ModelError("injected print-check failure")

    monkeypatch.setattr(printing, "run_print_check", broken)
    out = tmp_path / "o"
    assert run.main(["all", str(_model(tmp_path)), "--out", str(out)]) == 2
    assert "injected" in capsys.readouterr().err
    m = load_manifest(out)
    assert m["stages"] == {"build": "ok", "drawing": "ok"}
    assert (out / "drawing" / "box.svg").is_file() and not (out / "report.md").exists()

    # report refuses an output whose stages are incomplete, naming what is missing
    assert run.main(["report", str(out)]) == 2
    err = capsys.readouterr().err
    assert "print-check" in err and "viewer" in err and "Traceback" not in err

    monkeypatch.setattr(printing, "run_print_check", real)
    assert run.main(["print-check", str(out)]) == 0
    assert run.main(["viewer", str(out)]) == 0
    assert run.main(["report", str(out)]) == 0
    assert str(out / "report.md") in capsys.readouterr().out
    assert "(5)*" in (out / "report.md").read_text(encoding="utf-8")
    assert load_manifest(out)["stages"]["report"] == "ok"


def test_bad_printer_fails_before_build(run_cli, tmp_path):
    out = tmp_path / "o"
    bad = tmp_path / "p.json"
    bad.write_text("{nope", encoding="utf-8")
    r = run_cli("all", FIX / "two_parts.json", "--out", out, "--printer", bad)
    assert r.returncode == 2
    assert not out.exists()


import pytest


@pytest.mark.parametrize("content", ['{"nozzle": 1}', '{"bedMm": [0, 1, 1]}'])
def test_invalid_printer_profile_fails_before_build(run_cli, tmp_path, content):
    out = tmp_path / "o"
    bad = tmp_path / "p.json"
    bad.write_text(content, encoding="utf-8")
    r = run_cli("all", FIX / "two_parts.json", "--out", out, "--printer", bad)
    assert r.returncode == 2
    assert "printer" in r.stderr
    assert not out.exists()
    assert not (tmp_path / "o.building").exists()
