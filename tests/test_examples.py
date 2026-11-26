"""Full pipeline on the sketch examples (slow: run with `tools/test.sh -m slow tests/test_examples.py`)."""
import itertools
import json
from pathlib import Path

import pytest

from sketchcad.build import load_manifest, load_part_shapes, place_shape

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
MIN_PARTS = {"saegmuller-telescope": 10}
VIEWER_MAX_BYTES = 5 * 1024 * 1024
OVERLAP_MAX_MM3 = 1e-3


@pytest.mark.slow
@pytest.mark.parametrize("name", sorted(MIN_PARTS))
def test_example_records_every_size_as_assumed(name):
    # The examples have no user to answer the intake questions: every number is a guess.
    model = json.loads((EXAMPLES / name / "model.json").read_text(encoding="utf-8"))
    assert model["assumptions"]
    assert model["dimensions"]
    for row in model["dimensions"]:
        assert row["origin"] == "assumed", row["label"]
    for part in model["parts"]:
        for fin in part.get("finish", []):
            assert fin.get("origin") == "assumed", (part["id"], fin)


@pytest.mark.slow
@pytest.mark.parametrize("name", sorted(MIN_PARTS))
def test_example_runs_end_to_end(run_cli, tmp_path, name):
    out = tmp_path / name
    r = run_cli("all", EXAMPLES / name / "model.json", "--out", out)
    assert r.returncode == 0, r.stderr

    manifest = load_manifest(out)
    assert len(manifest["parts"]) >= MIN_PARTS[name]

    report = json.loads((out / "print" / "print-report.json").read_text(encoding="utf-8"))
    for pid, part in report["parts"].items():
        assert part["watertight"], pid
        assert not [w for w in part["warnings"] if "watertight" in w], pid
        assert part["fitsBed"], f"{pid} does not fit the default bed"

    assert (out / "viewer" / "index.html").stat().st_size < VIEWER_MAX_BYTES

    placement = {p["part"]: p for p in manifest["assembly"]["placement"]}
    placed = {pid: place_shape(s, placement[pid]) for pid, s in load_part_shapes(out).items()}
    for a, b in itertools.combinations(placed, 2):
        common = placed[a].intersect(placed[b])
        overlap = sum(s.volume for s in common.solids()) if common is not None else 0.0
        assert overlap <= OVERLAP_MAX_MM3, f"{a} and {b} overlap by {overlap:.3f} mm3"


HERO = "saegmuller-telescope"
HERO_VIEWER_MAX_BYTES = 4_000_000


@pytest.mark.slow
def test_hero_viewer_fits_the_portfolio_budget(run_cli, tmp_path):
    out = tmp_path / HERO
    assert run_cli("all", EXAMPLES / HERO / "model.json", "--out", out).returncode == 0
    size = (out / "viewer" / "index.html").stat().st_size
    assert size <= HERO_VIEWER_MAX_BYTES, f"{HERO} viewer is {size / 1e6:.2f} MB"
    sheets = {s["id"]: s for s in load_manifest(out)["drawing"]["sheets"]}
    num, den = (float(v) for v in sheets["assembly"]["scale"].split(":"))
    assert num / den >= 0.4, f"assembly sheet at {sheets['assembly']['scale']}, want 1:2.5 or larger"
    assert not any("notesSheets" in s for s in sheets.values())


@pytest.mark.slow
@pytest.mark.parametrize("name", sorted(MIN_PARTS))
def test_example_marks_its_reference_size_as_a_span(name):
    model = json.loads((EXAMPLES / name / "model.json").read_text(encoding="utf-8"))
    assert any("span" in row for row in model["dimensions"])
