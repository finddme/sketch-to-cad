"""Repository metadata: plugin manifests, README links, image budget, notices."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _json(rel):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def test_plugin_manifest():
    p = _json(".claude-plugin/plugin.json")
    assert p["name"] == "sketch-to-cad"
    assert re.fullmatch(r"\d+\.\d+\.\d+", p["version"])
    assert p["author"]["name"]
    assert p["license"] == "MIT"
    assert p["repository"].startswith("https://github.com/")
    assert p["description"]


def test_marketplace_manifest_matches_plugin():
    m = _json(".claude-plugin/marketplace.json")
    p = _json(".claude-plugin/plugin.json")
    assert m["name"] == "sketch-to-cad-marketplace"
    assert m["owner"]["name"]
    assert len(m["plugins"]) == 1
    entry = m["plugins"][0]
    assert entry["name"] == p["name"]
    assert entry["source"] == "./"
    assert "skills" not in entry  # would disable default skill discovery
    assert (ROOT / "skills" / p["name"] / "SKILL.md").is_file()


def test_readme_relative_links_exist():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    targets = re.findall(r"\]\(([^)\s]+)\)", text)
    assert targets
    for t in targets:
        if re.match(r"^[a-z]+:", t) or t.startswith("#"):
            continue
        assert (ROOT / t.split("#")[0]).exists(), f"README links to missing {t}"


def test_readme_images_are_in_docs_images():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    imgs = re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", text)
    assert "docs/images/hero.webp" in imgs
    assert all(i.startswith("docs/images/") for i in imgs)


def test_docs_images_within_budget():
    images = ROOT / "docs" / "images"
    showcase = images / "showcase"
    still = sum(f.stat().st_size for f in images.rglob("*") if f.is_file() and showcase not in f.parents)
    assert 0 < still <= 3 * 1024 * 1024
    # the showcase: two copies of the hero viewer (scroll, autoplay) and four recordings
    files = [f for f in showcase.rglob("*") if f.is_file()]
    assert {f.name for f in files} >= {"telescope-scroll.html", "telescope-autoplay.html"}
    assert all(f.stat().st_size <= 4_000_000 for f in files)
    assert sum(f.stat().st_size for f in files) <= 12 * 1024 * 1024


def test_third_party_mentions_three_js_and_examples():
    text = (ROOT / "THIRD_PARTY.md").read_text(encoding="utf-8")
    assert "three.js" in text
    for src in (ROOT / "examples").glob("*/SOURCE.md"):
        assert f"examples/{src.parent.name}/SOURCE.md" in text


def test_no_private_leaks():
    # Strings are split so this file does not match itself.
    pat = re.compile("/ho" + "me/(?!pptruser)|jo" + "mul|cera" + "mic", re.I)
    skip = {".git", ".superpowers", "dev", "out", "node_modules", ".venv", ".pytest_cache", "__pycache__"}
    hits = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or skip & set(path.relative_to(ROOT).parts):
            continue
        data = path.read_bytes()
        if b"\0" in data:
            continue
        for n, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
            if pat.search(line):
                hits.append(f"{path.relative_to(ROOT)}:{n}")
    assert not hits, hits


DOCS = ["README.md", "skills/sketch-to-cad/SKILL.md"]


def test_docs_state_the_tested_python_and_platforms():
    for rel in DOCS:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "3.10" not in text, rel
        assert "3.12+" in text and "tested on 3.12" in text, rel
    for rel in DOCS[:2]:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "macOS" in text and "WSL" in text, rel


def test_docs_cover_notes_sheets_resume_and_code_part_safety():
    skill = (ROOT / "skills/sketch-to-cad/SKILL.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for text in (skill, readme):
        assert "<id>-notes.svg" in text
    assert "`report OUT`" in skill or "report OUT" in skill
    assert "sha256" in skill and "untrusted" in skill
    assert "environment not ready" in skill
    assert "model.json" in readme and "Python code" in readme  # security note
    assert "/skill" in skill and "/skill" in readme  # docker fallback mounts
    schema = (ROOT / "skills/sketch-to-cad/references/model-schema.md").read_text(encoding="utf-8")
    assert "reserved" in schema and "-notes" in schema


def test_docs_name_the_linux_opengl_library():
    # without libGL.so.1 the CAD kernel cannot load; setup checks it and the docs must say how to install it
    for rel in DOCS:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "sudo apt install libgl1" in text and "mesa-libGL" in text, rel
