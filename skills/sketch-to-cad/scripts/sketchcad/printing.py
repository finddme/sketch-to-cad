"""Print-check stage: per-part mesh checks, print orientation, plate export (spec 6.6).

run_print_check(out) tessellates every part STEP, checks the mesh against the
printer profile, rotates each part into its best print orientation and writes
OUT/print/<id>.stl, print-plate.3mf, print-report.json and print-report.md.
Checks only warn; they never stop the files from being written.
"""
import json
import math
import shutil
from pathlib import Path

import numpy as np
import trimesh
from trimesh.ray.ray_triangle import RayMeshIntersector

from .build import load_manifest, load_part_shapes, write_manifest
from .errors import ModelError

DEFAULT_PRINTER = {"bedMm": [220, 220, 250], "nozzleMm": 0.4, "minWallMm": 0.8, "overhangDeg": 45}
LINEAR_DEFLECTION = 0.08   # same tolerances as the GLB export in build.py
ANGULAR_DEFLECTION = 0.15
CANDIDATES = 6             # largest convex-hull face directions tried as "down"
PLATE_GAP_MM = 5.0
CONTACT_TOL_MM = 1e-3
TIE_TOL = 1e-6
WALL_PERCENTILE = 5
RAY_NUDGE_MM = 1e-3        # start rays this far inside the surface
OVERHANG_WARN_RATIO = 0.1  # warn when this much of the surface still overhangs


# ---- printer profile ---------------------------------------------------------

def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _printer_profile(printer):
    if printer is None:
        printer = {}
    if not isinstance(printer, dict):
        raise ModelError("printer: must be a JSON object")
    unknown = sorted(set(printer) - set(DEFAULT_PRINTER))
    if unknown:
        raise ModelError(f"printer: unknown key(s) {', '.join(unknown)}; allowed: {', '.join(DEFAULT_PRINTER)}")
    profile = {**DEFAULT_PRINTER, **printer}
    bed = profile["bedMm"]
    if not (isinstance(bed, list) and len(bed) == 3 and all(_is_num(v) and v > 0 for v in bed)):
        raise ModelError("printer.bedMm: must be three positive numbers [x, y, z]")
    for key in ("nozzleMm", "minWallMm"):
        if not (_is_num(profile[key]) and profile[key] > 0):
            raise ModelError(f"printer.{key}: must be a positive number")
    if not (_is_num(profile["overhangDeg"]) and 0 < profile["overhangDeg"] < 90):
        raise ModelError("printer.overhangDeg: must be a number between 0 and 90 (exclusive)")
    profile["bedMm"] = list(bed)
    return profile


def validate_printer(printer):
    """Validate a printer profile dict (any subset of the defaults); returns the full profile."""
    return _printer_profile(printer)


# ---- mesh helpers ------------------------------------------------------------

def _tessellate(shape):
    verts, tris = shape.tessellate(LINEAR_DEFLECTION, ANGULAR_DEFLECTION)
    mesh = trimesh.Trimesh(np.array([[v.X, v.Y, v.Z] for v in verts], dtype=float),
                           np.array(tris, dtype=np.int64), process=False)
    mesh.merge_vertices()   # faces are tessellated separately; weld their shared edges
    # Welding collapses the zero-width triangles OCC emits at fillet corners/poles into
    # triangles with a repeated vertex; they carry no area but break manifold edges.
    f = mesh.faces
    mesh.update_faces((f[:, 0] != f[:, 1]) & (f[:, 1] != f[:, 2]) & (f[:, 0] != f[:, 2]))
    mesh.remove_unreferenced_vertices()
    return mesh


def _grounded(mesh, rotation):
    """4x4 matrix: rotate, then slide so the bounding box minimum is the origin."""
    t = np.eye(4)
    t[:3, :3] = rotation
    lo = (mesh.vertices @ rotation.T).min(axis=0)
    t[:3, 3] = -lo
    return t


def _overhang_and_contact(mesh, overhang_deg):
    """(overhang area, contact area) of a mesh standing on z = 0."""
    nz = mesh.face_normals[:, 2]
    area = mesh.area_faces
    top = mesh.vertices[mesh.faces][:, :, 2].max(axis=1)
    lo = mesh.vertices[:, 2].min()
    on_bed = top <= lo + CONTACT_TOL_MM
    steep = nz < -math.sin(math.radians(overhang_deg))
    overhang = float(area[steep & ~on_bed].sum())
    contact = float(area[(nz < -0.999) & on_bed].sum())
    return overhang, contact


def _overhang_ratio(mesh, overhang_deg):
    total = float(mesh.area)
    return _overhang_and_contact(mesh, overhang_deg)[0] / total if total > 0 else 0.0


RZ90 = np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
REGIONS = 5


def _fits(extents, bed_mm):
    return bool(all(e <= b + TIE_TOL for e, b in zip(extents, bed_mm)))


def best_orientation(mesh, overhang_deg, bed_mm=None):
    """4x4 matrix (rotation + translation onto the bed origin) with the least overhang.

    Candidates turn each of the six largest convex-hull face directions to -Z,
    each also turned 90 degrees about Z. With bed_mm, only candidates that fit
    the bed compete (all of them when none fits). Ties on overhang area go to
    the larger bed-contact area.
    """
    hull = mesh.convex_hull
    keys, inverse = np.unique(np.round(hull.face_normals, 3), axis=0, return_inverse=True)
    areas = np.bincount(inverse.ravel(), weights=hull.area_faces)
    scored = []
    for i in np.argsort(-areas)[:CANDIDATES]:
        normal = keys[i] / np.linalg.norm(keys[i])
        down = trimesh.geometry.align_vectors(normal, [0, 0, -1])[:3, :3]
        for rotation in (down, RZ90 @ down):
            t = _grounded(mesh, rotation)
            moved = mesh.copy()
            moved.apply_transform(t)
            overhang, contact = _overhang_and_contact(moved, overhang_deg)
            fits = bed_mm is None or _fits(moved.extents, bed_mm)
            scored.append((t, fits, overhang, contact))
    pool = [c for c in scored if c[1]] or scored
    best = None
    for c in pool:
        if best is None or c[2] < best[2] - TIE_TOL or (abs(c[2] - best[2]) <= TIE_TOL and c[3] > best[3]):
            best = c
    return best[0]


def _overhang_regions(mesh, overhang_deg, limit=REGIONS):
    """Largest connected overhanging face groups of a mesh standing on z = 0."""
    area = mesh.area_faces
    top = mesh.vertices[mesh.faces][:, :, 2].max(axis=1)
    on_bed = top <= mesh.vertices[:, 2].min() + CONTACT_TOL_MM
    mask = (mesh.face_normals[:, 2] < -math.sin(math.radians(overhang_deg))) & ~on_bed
    idx = np.nonzero(mask)[0]
    if len(idx) == 0:
        return []
    adj = mesh.face_adjacency
    adj = adj[mask[adj[:, 0]] & mask[adj[:, 1]]]
    groups = trimesh.graph.connected_components(adj, nodes=idx, min_len=1)
    regions = []
    for g in groups:
        g = np.asarray(g)
        w = area[g]
        center = (mesh.triangles_center[g] * w[:, None]).sum(axis=0) / w.sum()
        regions.append({"centerMm": [round(float(v), 3) for v in center], "areaMm2": round(float(w.sum()), 3)})
    return sorted(regions, key=lambda r: -r["areaMm2"])[:limit]


def estimate_min_wall(mesh, samples=2000):
    """5th percentile of the distance from surface samples to the surface straight inside.

    An estimate: rays that leave the part without a second hit are ignored.
    Returns None when no ray hit anything.
    """
    points, face = trimesh.sample.sample_surface(mesh, samples, seed=0)
    normals = mesh.face_normals[face]
    origins = points - normals * RAY_NUDGE_MM
    hits, rays, _ = RayMeshIntersector(mesh).intersects_location(origins, -normals, multiple_hits=False)
    if len(rays) == 0:
        return None
    dist = np.linalg.norm(hits - points[rays], axis=1)
    return float(np.percentile(dist, WALL_PERCENTILE))


# ---- per part ----------------------------------------------------------------

def _fmt(v):
    return f"{v:.1f}".rstrip("0").rstrip(".")


def _check_part(pid, mesh, printer):
    try:
        transform = best_orientation(mesh, printer["overhangDeg"], printer["bedMm"])
    except Exception as e:
        raise ModelError(f"{pid}: cannot determine print orientation ({e})") from e
    oriented = mesh.copy()
    oriented.apply_transform(transform)
    extents = oriented.extents
    fits = bool(all(e <= b + TIE_TOL for e, b in zip(extents, printer["bedMm"])))
    wall = estimate_min_wall(oriented)
    ratio_oriented = _overhang_ratio(oriented, printer["overhangDeg"])
    watertight = bool(oriented.is_watertight and oriented.is_winding_consistent)

    warnings = []
    if not watertight:
        warnings.append("mesh is not watertight/manifold; slicers may fail or print gaps")
    if not fits:
        size = " x ".join(_fmt(e) for e in extents)
        bed = " x ".join(_fmt(b) for b in printer["bedMm"])
        warnings.append(f"does not fit the bed: {size} mm > {bed} mm")
    if wall is not None and wall < printer["minWallMm"]:
        warnings.append(f"min wall about {wall:.2f} mm (estimate) is below {printer['minWallMm']} mm")
    if ratio_oriented > OVERHANG_WARN_RATIO:
        warnings.append(f"{ratio_oriented * 100:.0f}% of the surface still overhangs in the best "
                        f"orientation; supports needed")
    report = {
        "watertight": watertight,
        "fitsBed": fits,
        "sizeMm": [round(float(e), 3) for e in extents],
        "minWallMm": None if wall is None else round(wall, 3),
        "minWallEstimated": True,
        "overhangAreaRatio": round(_overhang_ratio(mesh, printer["overhangDeg"]), 4),
        "overhangAreaRatioOriented": round(ratio_oriented, 4),
        "overhangRegionsMm": _overhang_regions(oriented, printer["overhangDeg"]),
        "transform": [[round(float(v), 6) for v in row] for row in transform],
        "warnings": warnings,
    }
    return oriented, report


def _orientation_note(transform):
    rot = np.array(transform)[:3, :3]
    return "회전 없음" if np.allclose(rot, np.eye(3), atol=1e-6) else "회전 적용 (transform 참고)"


# ---- plate -------------------------------------------------------------------

def _plate(oriented, printer):
    """Row layout, largest footprint first. Returns (scene, warnings)."""
    bed_x, bed_y = printer["bedMm"][:2]
    order = sorted(oriented, key=lambda pid: -(oriented[pid].extents[0] * oriented[pid].extents[1]))
    scene = trimesh.Scene()
    x = y = row_h = 0.0
    top = right = 0.0
    for pid in order:
        w, d = oriented[pid].extents[:2]
        if x > 0 and x + w > bed_x:
            x, y, row_h = 0.0, y + row_h + PLATE_GAP_MM, 0.0
        placed = oriented[pid].copy()
        placed.apply_translation([x, y, 0])
        scene.add_geometry(placed, geom_name=pid, node_name=pid)
        x += w + PLATE_GAP_MM
        row_h = max(row_h, d)
        right, top = max(right, x - PLATE_GAP_MM), max(top, y + d)
    warnings = []
    if right > bed_x + TIE_TOL or top > bed_y + TIE_TOL:
        warnings.append(f"plate layout {_fmt(right)} x {_fmt(top)} mm exceeds the {_fmt(bed_x)} x "
                        f"{_fmt(bed_y)} mm bed; print parts in separate runs")
    return scene, warnings


# ---- reports -----------------------------------------------------------------

def _markdown(name, report):
    pr = report["printer"]
    lines = [f"# 프린트 검사 — {name}", "",
             "> 최소 벽 두께는 표면에서 안쪽으로 쏜 광선으로 잰 **추정**값입니다.", ""]
    for pid, p in report["parts"].items():
        wall = "측정 불가" if p["minWallMm"] is None else f"{p['minWallMm']} mm (추정)"
        lines += [
            f"## {pid}", "",
            f"- 베드 안에 들어감: {'예' if p['fitsBed'] else '아니오'} (크기 {' x '.join(_fmt(e) for e in p['sizeMm'])} mm)",
            f"- 닫힌 메시(watertight): {'예' if p['watertight'] else '아니오'}",
            f"- 최소 벽 두께: {wall}",
            f"- 오버행 면적 비율: 모델 방향 {p['overhangAreaRatio'] * 100:.1f}% -> 제안 방향 {p['overhangAreaRatioOriented'] * 100:.1f}%",
            f"- 출력 방향: {_orientation_note(p['transform'])}; 가장 낮은 지점이 z=0",
        ]
        for r in p["overhangRegionsMm"]:
            lines.append(f"  - 오버행 영역: 중심 ({', '.join(_fmt(v) for v in r['centerMm'])}) mm, 면적 {_fmt(r['areaMm2'])} mm2")
        if p["warnings"]:
            lines += ["- 경고:"] + [f"  - {w}" for w in p["warnings"]]
        else:
            lines.append("- 경고: 없음")
        lines.append("")
    if report["plate"]["warnings"]:
        lines += ["## 플레이트", ""] + [f"- {w}" for w in report["plate"]["warnings"]] + [""]
    lines += ["## 프린터 프로필", "",
              f"- 베드: {' x '.join(_fmt(b) for b in pr['bedMm'])} mm",
              f"- 노즐: {pr['nozzleMm']} mm",
              f"- 최소 벽: {pr['minWallMm']} mm",
              f"- 오버행 기준: {pr['overhangDeg']} deg", ""]
    return "\n".join(lines)


def run_print_check(out, printer=None):
    """Write OUT/print/ (built in print.tmp, then renamed), update the manifest, return the report."""
    out = Path(out)
    profile = _printer_profile(printer)
    try:
        manifest = load_manifest(out)
    except (OSError, ValueError) as e:
        raise ModelError(f"{out}: not a build output folder ({e})") from e
    if manifest.get("stages", {}).get("build") != "ok":
        raise ModelError(f"{out}: build stage has not completed")
    final, stage = out / "print", out / "print.tmp"
    for p in (final, stage):
        if p.exists():
            raise ModelError(f"{p}: already exists")
    shapes = load_part_shapes(out)
    stage.mkdir()
    try:
        oriented, parts = {}, {}
        for pid, shape in shapes.items():
            oriented[pid], parts[pid] = _check_part(pid, _tessellate(shape), profile)
            oriented[pid].export(stage / f"{pid}.stl")
        scene, plate_warnings = _plate(oriented, profile)
        scene.export(stage / "print-plate.3mf")
        report = {"printer": profile, "parts": parts, "plate": {"warnings": plate_warnings}}
        (stage / "print-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        (stage / "print-report.md").write_text(_markdown(manifest.get("name", ""), report), encoding="utf-8")
        stage.rename(final)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    manifest["stages"]["print-check"] = "ok"
    manifest["print"] = {
        "files": [f"print/{pid}.stl" for pid in shapes] + [
            "print/print-plate.3mf", "print/print-report.json", "print/print-report.md"],
        "parts": {pid: {"warnings": len(p["warnings"])} for pid, p in parts.items()},
    }
    try:
        write_manifest(out, manifest)
    except BaseException:  # never leave print/ behind without its manifest entry
        shutil.rmtree(final, ignore_errors=True)
        raise
    return report
