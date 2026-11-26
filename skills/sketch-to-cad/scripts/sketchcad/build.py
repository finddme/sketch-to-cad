"""Build stage: per-part STEP/GLB, placed assembly, manifest.json.

Later stages consume only the output folder via load_manifest,
write_manifest and load_part_shapes.
"""
import json
import os
import shutil
from pathlib import Path

import trimesh
from PIL import Image
from build123d import Axis, Compound, Pos, export_gltf, export_step, import_step

from . import schema
from .errors import ModelError
from .part import build_part

STEP_VOLUME_TOL = 1e-6
# The sketch named by model.json `source.images[0].file` is copied verbatim to
# OUT/source/ (the viewer opens its story on it). Skipped quietly when absent,
# outside the model folder, not an image Pillow can read, or larger than this.
SOURCE_IMAGE_MAX_BYTES = 25 * 1024 * 1024
GLB_BOUNDS_TOL_MM = 0.5
SPAN_TOL_MM = 0.5
SPAN_AXES = {"width": 0, "depth": 1, "height": 2}
GLB_UNIT = "m"
# build123d's glTF export converts the CAD Z-up frame to glTF's Y-up frame:
# (x, y, z)_cad -> (x, z, -y)_gltf, in meters.
GLB_UP_AXIS = "Y"


def load_manifest(out):
    return json.loads((Path(out) / "manifest.json").read_text(encoding="utf-8"))


def write_manifest(out, data):
    """Write manifest.json atomically (temp file + rename)."""
    out = Path(out)
    tmp = out / "manifest.json.tmp"
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, out / "manifest.json")


def load_part_shapes(out):
    """Re-import parts/<id>.step as {id: Solid}, in manifest part order."""
    out = Path(out)
    shapes = {}
    for pid, info in load_manifest(out)["parts"].items():
        solids = import_step(out / info["step"]).solids()
        if len(solids) != 1:
            raise ModelError(f"{out / info['step']}: expected one solid, found {len(solids)}")
        shapes[pid] = solids[0]
    return shapes


def _read_model(model_path):
    raw = Path(model_path).read_bytes()
    try:
        model = json.loads(raw)
    except ValueError as e:
        raise ModelError(f"{model_path}: invalid JSON ({e})") from e
    return raw, model


def _bounds(shape):
    bb = shape.bounding_box(optimal=True)
    return {"min": [bb.min.X, bb.min.Y, bb.min.Z], "max": [bb.max.X, bb.max.Y, bb.max.Z]}


def glb_bounds_mm(path):
    """GLB bounds converted to CAD frame and units: mm, Z-up."""
    lo, hi = trimesh.load(str(path), force="scene").bounds * 1000.0
    if GLB_UP_AXIS == "Y":  # (x, y, z)_gltf -> (x, -z, y)_cad
        lo, hi = (lo[0], -hi[2], lo[1]), (hi[0], -lo[2], hi[1])
    return {"min": [float(v) for v in lo], "max": [float(v) for v in hi]}


def _check_glb(label, glb, cad_bounds):
    mesh = glb_bounds_mm(glb)
    for key in ("min", "max"):
        for axis in range(3):
            if abs(mesh[key][axis] - cad_bounds[key][axis]) > GLB_BOUNDS_TOL_MM:
                raise ModelError(f"{label}: GLB bounds differ from CAD by more than {GLB_BOUNDS_TOL_MM} mm")


def _export(shape, label, step_path, glb_path):
    export_step(shape, step_path)
    export_gltf(shape, glb_path, binary=True, linear_deflection=0.08, angular_deflection=0.15)
    restored = import_step(step_path)
    err = abs(restored.volume - shape.volume) / shape.volume
    if not restored.is_valid or err > STEP_VOLUME_TOL:
        raise ModelError(f"{label}: STEP round trip failed (relative volume error {err:.2e})")
    _check_glb(label, glb_path, _bounds(shape))


def _check_spans(model, pid, bounds):
    """A dimension marked as a part's overall span must match that span."""
    for i, d in enumerate(model["dimensions"]):
        if d.get("part") != pid or "span" not in d:
            continue
        k = SPAN_AXES[d["span"]]
        actual = bounds["max"][k] - bounds["min"][k]
        if abs(actual - d["valueMm"]) > SPAN_TOL_MM:
            raise ModelError(
                f"dimensions[{i}] ({d['label']!r}): valueMm {d['valueMm']:g} differs from the "
                f"{d['span']} of part {pid}, which is {actual:.4g} mm, by more than {SPAN_TOL_MM} mm")


def place_shape(shape, pl):
    """Apply a placement entry: rotate X, then Y, then Z (degrees), then translate."""
    rx, ry, rz = pl["rotateDeg"]
    shape = shape.rotate(Axis.X, rx).rotate(Axis.Y, ry).rotate(Axis.Z, rz)
    return Pos(*pl["translateMm"]) * shape


def source_image_path(model, model_dir):
    """The model's first source image as a resolved path, or None. `source` is
    free-form and not validated, so anything unusable just yields None; a relative
    path may not leave the model folder."""
    try:
        rel = model["source"]["images"][0]["file"]
    except (KeyError, IndexError, TypeError):
        return None
    if not isinstance(rel, str) or not rel or Path(rel).is_absolute():
        return None
    base = Path(model_dir).resolve()
    target = (base / rel).resolve()
    if not target.is_relative_to(base) or not target.is_file():
        return None
    if target.stat().st_size > SOURCE_IMAGE_MAX_BYTES:
        return None
    try:
        with Image.open(target) as im:
            im.verify()
    except Exception:  # Pillow raises many types for unreadable or non-image files
        return None
    return target


def _copy_source_image(model, model_dir, stage):
    src = source_image_path(model, model_dir)
    if src is None:
        return None
    (stage / "source").mkdir()
    shutil.copyfile(src, stage / "source" / src.name)
    return f"source/{src.name}"


def _build_into(stage, model_path, raw, model):
    model = schema.validate(model, Path(model_path).parent)
    (stage / "parts").mkdir()
    (stage / "model.json").write_bytes(raw)
    source_image = _copy_source_image(model, Path(model_path).parent, stage)

    given = {pl["part"]: pl for pl in model["placement"]}
    parts, placement, placed = {}, [], []
    for p in model["parts"]:
        pid = p["id"]
        shape, checks = build_part(p, Path(model_path).parent)
        bounds = _bounds(shape)
        _check_spans(model, pid, bounds)
        _export(shape, f"part {pid}", stage / "parts" / f"{pid}.step", stage / "parts" / f"{pid}.glb")
        parts[pid] = {
            "step": f"parts/{pid}.step",
            "glb": f"parts/{pid}.glb",
            "volumeMm3": shape.volume,
            "boundsMm": bounds,
            "checks": checks,
        }
        pl = given.get(pid, {})
        pl = {"part": pid, "translateMm": pl.get("translateMm", [0, 0, 0]),
              "rotateDeg": pl.get("rotateDeg", [0, 0, 0])}
        placement.append(pl)
        placed.append(place_shape(shape, pl))

    assembly = Compound(placed)
    _export(assembly, "assembly", stage / "assembly.step", stage / "assembly.glb")
    manifest = {
        "schemaVersion": 1,
        "name": model["name"],
        "units": "mm",
        "glbUnit": GLB_UNIT,
        "glbUpAxis": GLB_UP_AXIS,
        "parts": parts,
        "assembly": {"step": "assembly.step", "glb": "assembly.glb", "placement": placement},
        "stages": {"build": "ok"},
    }
    if source_image:
        manifest["sourceImage"] = source_image
    write_manifest(stage, manifest)
    return manifest


def run_build(model_path, out):
    """Build parts and assembly into `out` (must not exist); returns the manifest."""
    model_path, out = Path(model_path), Path(out)
    if out.name in ("", ".", ".."):
        raise ModelError(f"{out}: not a usable output folder; name a new folder, e.g. out/<name>")
    stage = out.with_name(out.name + ".building")
    for p in (out, stage):
        if p.exists():
            raise ModelError(f"{p}: already exists")
    try:
        raw, model = _read_model(model_path)
    except OSError as e:
        raise ModelError(f"{model_path}: cannot read ({e.strerror or e})") from e
    stage.mkdir(parents=True)
    try:
        manifest = _build_into(stage, model_path, raw, model)
        stage.rename(out)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return manifest
