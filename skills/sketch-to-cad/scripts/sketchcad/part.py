"""Assemble one part: features, finishes (fillet/chamfer/shell) or a code part."""
import hashlib
import importlib.util
import itertools
import sys
from pathlib import Path

from build123d import GeomType, Kind, Part

from .edges import AXIS_VEC, select_edges
from .errors import ModelError
from .features import make_feature

VOLUME_EPS = 1e-7
BOUNDS_TOL_MM = 1e-4
_module_counter = itertools.count()


def build_part(part, base_dir):
    """Build one validated part dict. Returns (Part, checks).

    checks is a list of {id, operation, volumeMm3, changeMm3}, one per feature
    and finish step. ModelError messages are prefixed "part <id>: ".
    """
    pid = part["id"]
    try:
        if part["construction"] == "code":
            solid, checks = _code_part(part, Path(base_dir))
        else:
            solid, checks = _feature_part(part, Path(base_dir))
        return Part([solid]), checks
    except ModelError as exc:
        raise ModelError(f"part {pid}: {exc}") from exc


def _one_valid(shape, what):
    solids = shape.solids()
    if len(solids) != 1:
        raise ModelError(f"{what}: expected exactly one solid, got {len(solids)}")
    solid = solids[0]
    if not solid.is_valid or solid.volume <= 0:
        raise ModelError(f"{what}: result is not a valid solid with positive volume")
    return solid


def _feature_part(part, base_dir):
    model, checks, prior = None, [], {}
    for feat in part["features"]:
        fid, op = feat["id"], feat["operation"]
        tool = make_feature(feat, base_dir, prior)
        prior[fid] = tool
        before = model.volume if model is not None else 0.0
        if op == "base":
            model = _one_valid(tool, f"feature {fid}")
        else:
            try:
                combined = model.fuse(tool) if op == "union" else model.cut(tool)
            except Exception as exc:
                raise ModelError(f"feature {fid}: {op} failed ({type(exc).__name__}: {exc})") from exc
            model = _one_valid(combined, f"feature {fid}: expected one connected valid solid")
        change = model.volume - before
        if op == "cut" and change >= -VOLUME_EPS:
            raise ModelError(f"feature {fid}: ineffective cut")
        if op == "union" and change <= VOLUME_EPS:
            raise ModelError(f"feature {fid}: ineffective union")
        checks.append({"id": fid, "operation": op, "volumeMm3": model.volume, "changeMm3": change})
    for i, fin in enumerate(part.get("finish", [])):
        before = model.volume
        model = _apply_finish(model, fin, prior, f"finish[{i}] {fin['kind']}")
        checks.append({"id": f"finish[{i}]", "operation": fin["kind"],
                       "volumeMm3": model.volume, "changeMm3": model.volume - before})
    return model, checks


def _apply_finish(model, fin, prior, label):
    kind = fin["kind"]
    if kind == "shell":
        return _shell(model, fin, label)
    edges = select_edges(model, fin["edges"], prior)
    if kind == "fillet":
        hint = "reduce the radius"
    else:
        hint = "reduce the distance"
    try:
        # Solid methods, not the fillet()/chamfer() builders: those lose the
        # parent solid for solids taken out of a boolean result.
        if kind == "fillet":
            result = model.fillet(fin["radiusMm"], edges)
        else:
            result = model.chamfer(fin["distanceMm"], None, edges)
    except Exception as exc:
        raise ModelError(f"{label} failed ({type(exc).__name__}: {exc}); {hint}, "
                         "or move this part to a code part") from exc
    return _one_valid_after(result, label, hint)


def _one_valid_after(result, label, hint):
    try:
        return _one_valid(result, label)
    except ModelError as exc:
        raise ModelError(f"{exc}; {hint}, or move this part to a code part") from exc


def _shell(model, fin, label):
    """Inward uniform offset that removes one selected extremal planar face."""
    spec = fin["openFace"]
    axis = "XYZ".index(spec[1])
    box = model.bounding_box()
    extreme = list((box.min if spec[0] == "-" else box.max))[axis]
    want = [(-1 if spec[0] == "-" else 1) * c for c in AXIS_VEC[spec[1]]]
    faces = []
    for face in model.faces():
        if face.geom_type != GeomType.PLANE:
            continue
        fb = face.bounding_box()
        n = face.normal_at()
        if (all(abs(list(v)[axis] - extreme) < 1e-5 for v in (fb.min, fb.max))
                and all(abs(a - b) < 1e-6 for a, b in zip((n.X, n.Y, n.Z), want))):
            faces.append(face)
    if len(faces) != 1:
        raise ModelError(f"{label}: openFace {spec} must select exactly one extremal planar face, "
                         f"found {len(faces)}")
    try:
        result = model.offset_3d(faces, -fin["thicknessMm"], kind=Kind.INTERSECTION)
    except Exception as exc:
        raise ModelError(f"{label}: offset failed ({type(exc).__name__}: {exc}); "
                         "reduce the thickness or move this part to a code part") from exc
    result = _one_valid(result, label)
    if not result.volume < model.volume:
        raise ModelError(f"{label}: shell did not remove volume; check thickness and openFace")
    rb = result.bounding_box()
    if any(abs(a - b) > BOUNDS_TOL_MM for x, y in ((box.min, rb.min), (box.max, rb.max))
           for a, b in zip(x, y)):
        raise ModelError(f"{label}: shell changed the outer bounds; reduce the thickness")
    return result


def _code_part(part, base_dir):
    code = part["code"]
    name = code["file"]
    path = (base_dir / name).resolve()
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ModelError(f"{name}: cannot read file ({exc})") from exc
    if "sha256" in code and digest != code["sha256"].lower():
        raise ModelError(f"{name}: sha256 does not match the file")
    # The code runs with the user's permissions: always say what is about to run.
    note = "" if "sha256" in code else " (no sha256 recorded in model.json)"
    print(f"running code part {part['id']}: {name} sha256={digest}{note}", file=sys.stderr)
    try:
        spec = importlib.util.spec_from_file_location(f"sketchcad_code_part_{next(_module_counter)}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        shape = module.build()
    except Exception as exc:
        raise ModelError(f"{name}: build() failed: {exc}") from exc
    try:
        solid = _one_valid(shape, name)
    except AttributeError as exc:
        raise ModelError(f"{name}: build() must return a build123d solid ({exc})") from exc
    v = solid.volume
    return solid, [{"id": part["id"], "operation": "code", "volumeMm3": v, "changeMm3": v}]
