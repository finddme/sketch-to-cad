"""Report stage: OUT/report.md summarizing a finished build (spec 6.8).

Built only from files already on disk: model.json, manifest.json and
print/print-report.json.
"""
import json
import os
from pathlib import Path

from .build import load_manifest, write_manifest
from .errors import ModelError


def _num(v):
    return f"{float(v):g}"


def _files(out):
    return sorted(
        p.relative_to(out).as_posix()
        for p in out.rglob("*")
        if p.is_file() and p.name not in ("report.md", "report.md.tmp", "manifest.json.tmp")
    )


def _cell(text):
    return str(text).replace("|", "\\|")


def _assumed_finishes(model):
    """(label, value) for every finish marked assumed, valued as the drawing shows it."""
    for part in model["parts"]:
        for fin in part.get("finish", []):
            if fin.get("origin") != "assumed":
                continue
            kind = fin["kind"]
            if kind == "fillet":
                yield f"{part['id']}: fillet {fin['edges']}", f"R{_num(fin['radiusMm'])}"
            elif kind == "chamfer":
                yield f"{part['id']}: chamfer {fin['edges']}", f"C{_num(fin['distanceMm'])}"
            else:
                yield f"{part['id']}: shell thickness (open {fin['openFace']})", _num(fin["thicknessMm"])


def _dimension_rows(model):
    rows = ["| 라벨 | 값(mm) | 출처 |", "| --- | --- | --- |"]
    for d in model["dimensions"]:
        value = _num(d["valueMm"])
        if d["origin"] == "assumed":
            value = f"({value})*"
        rows.append(f"| {_cell(d['label'])} | {value} | {d['origin']} |")
    for label, value in _assumed_finishes(model):
        rows.append(f"| {_cell(label)} | ({value})* | assumed |")
    return rows


REQUIRED_STAGES = ("build", "drawing", "print-check", "viewer")


def write_report(out):
    """Write OUT/report.md (overwriting) and set manifest stages.report; returns its path.

    Every earlier stage must have completed; otherwise ModelError names the
    stages still to run.
    """
    out = Path(out)
    try:
        manifest = load_manifest(out)
        model = json.loads((out / "model.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ModelError(f"{out}: cannot build report ({e})") from e
    missing = [s for s in REQUIRED_STAGES if manifest.get("stages", {}).get(s) != "ok"]
    if missing:
        raise ModelError(f"{out}: cannot build report, stages not completed: {', '.join(missing)} "
                         "(run them first: run.py <stage> OUT)")
    try:
        print_report = json.loads((out / "print" / "print-report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ModelError(f"{out}: cannot build report ({e})") from e

    lines = [f"# {model['name']}", "", "## 생성 파일 (Files)", ""]
    lines += [f"- `{f}`" for f in _files(out)] + [""]
    lines += ["## 치수 (Dimensions)", ""] + _dimension_rows(model)
    lines += ["", "(v)* = 스케치에 없어 가정한 값 (assumed value)", ""]
    lines += ["## 가정 (Assumptions)", ""]
    lines += [f"- {a}" for a in model["assumptions"]] or ["없음"]
    lines += ["", "## 프린팅 경고 (Print warnings)", ""]
    for pid, p in print_report["parts"].items():
        lines.append(f"### {pid}")
        lines += [f"- {w}" for w in p["warnings"]] or ["없음"]
        lines.append("")
    plate = print_report.get("plate", {}).get("warnings", [])
    if plate:
        lines += ["### 플레이트 (plate)"] + [f"- {w}" for w in plate] + [""]
    lines += [
        "## 여는 방법 (How to open)", "",
        "- 뷰어: `viewer/index.html`을 브라우저에서 엽니다. 네트워크가 필요 없습니다.",
        '- 웹페이지에 삽입: `<iframe src="viewer/index.html?autoplay=1" loading="lazy"></iframe>`',
        "- 슬라이서: `print/print-plate.3mf`(전체 배치) 또는 `print/<id>.stl`(부품별)을 불러옵니다.",
        "",
    ]

    path = out / "report.md"
    tmp = out / "report.md.tmp"
    tmp.write_text("\n".join(lines), encoding="utf-8")
    os.replace(tmp, path)
    manifest["stages"]["report"] = "ok"
    write_manifest(out, manifest)
    return path
