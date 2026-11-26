import json
import re
from pathlib import Path

import pytest

from sketchcad.schema import validate

SKILL = Path(__file__).resolve().parent.parent / "skills" / "sketch-to-cad"
REFS = SKILL / "references"
FENCE = re.compile(r"^```json\n(.*?)^```", re.S | re.M)


def _frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, "SKILL.md needs YAML frontmatter"
    return dict(line.split(": ", 1) for line in m.group(1).splitlines() if ": " in line)


def test_skill_frontmatter():
    fm = _frontmatter((SKILL / "SKILL.md").read_text())
    assert fm["name"] == "sketch-to-cad"
    assert "hand sketch image" in fm["description"] and "single-file viewer" in fm["description"]


def test_skill_md_is_short_and_uses_skill_dir_paths():
    text = (SKILL / "SKILL.md").read_text()
    assert len(text.splitlines()) <= 300
    assert "${CLAUDE_SKILL_DIR}/scripts/run.py" in text
    assert "--venv" in text and "${CLAUDE_PLUGIN_DATA}" in text


def test_linked_references_exist():
    text = (SKILL / "SKILL.md").read_text()
    links = set(re.findall(r"\]\((references/[^)#]+)\)", text))
    assert {"references/intake-guide.md", "references/model-schema.md",
            "references/code-parts.md", "references/printing.md"} <= links
    for rel in links:
        assert (SKILL / rel).is_file(), rel


def _blocks(name):
    return FENCE.findall((REFS / name).read_text())


def test_model_schema_json_blocks_validate(tmp_path):
    blocks = _blocks("model-schema.md")
    assert len(blocks) >= 5
    for rel in ("parts/knob.py", "parts/ring.step"):
        (tmp_path / rel).parent.mkdir(exist_ok=True)
        (tmp_path / rel).write_text("")
    for i, block in enumerate(blocks):
        try:
            validate(json.loads(block), tmp_path)
        except Exception as e:
            raise AssertionError(f"json block {i} of model-schema.md: {e}") from e


def test_reference_example_models_build(tmp_path):
    pytest.importorskip("build123d")
    from sketchcad.build import run_build

    (tmp_path / "parts").mkdir()
    (tmp_path / "parts" / "knob.py").write_text(_python_block("code-parts.md"))
    for i, block in enumerate(_blocks("model-schema.md")):
        data = json.loads(block)
        if data["name"] == "knob-on-ring":  # needs a real STEP file
            continue
        model = tmp_path / f"m{i}.json"
        model.write_text(json.dumps(data))
        run_build(model, tmp_path / f"out{i}")


def _python_block(name):
    m = re.search(r"^```python\n(.*?)^```", (REFS / name).read_text(), re.S | re.M)
    assert m
    return m.group(1)


def test_code_part_example_builds(tmp_path):
    pytest.importorskip("build123d")
    from sketchcad.build import run_build

    (tmp_path / "parts").mkdir()
    (tmp_path / "parts" / "knob.py").write_text(_python_block("code-parts.md"))
    model = {"schemaVersion": 1, "units": "mm", "upAxis": "Z", "name": "knob", "assumptions": [],
             "parts": [{"id": "knob", "construction": "code", "code": {"file": "parts/knob.py"}}],
             "dimensions": []}
    (tmp_path / "m.json").write_text(json.dumps(model))
    run_build(tmp_path / "m.json", tmp_path / "out")
