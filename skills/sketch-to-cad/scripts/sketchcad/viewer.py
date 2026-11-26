"""Viewer stage: one self-contained OUT/viewer/index.html (spec 6.7).

run_viewer(out) inlines the pre-built three.js bundle (assets/viewer/viewer.bundle.js)
and a JSON data block into assets/viewer/template.html. The page needs no network
and no sibling files, so it opens from file:// and inside an iframe.

Data contract, <script type="application/json" id="sketchcad-data">:
  {"name": str,
   "wireColor": "#RRGGBB",
   "parts": [{"id": str,
              "color": "#RRGGBB",        # viewer.partColors[id], else PALETTE in part order
              "material": "matte" | "satin" | "gloss" | "metal",
                                         # viewer.partMaterials[id], else "satin"
              "glbBase64": str,          # viewer_glb(): the part re-meshed for display,
                                         # meters, Y-up (glTF frame), POSITION + indices
                                         # only (the page derives normals)
              "wire": [{"kind": "brep-edge" | "section", "lines": [[[x, y, z], ...], ...],
                        ...}],           # wire_guides() converted to the glTF frame:
                                         # (x, y, z)mm Z-up -> (x, z, -y) / 1000
              "placement": [16 numbers], # 4x4 matrix in the glTF frame, column-major
                                         # (three.js Matrix4.fromArray order)
              "drawingSvg": str | null}],# drawing/<id>.svg for a single-part model when
                                         # the drawing stage ran (a multi-part story
                                         # opens on the parts sheet instead)
   "sheetTopView": {"origin": [x, y],    # single part: the top view on its sheet (manifest
                    "scale": float,      # drawing.sheets[].topView): paper mm of the
                    "extent": [u0, v0, u1, v1]} | null,  # view's top-left, paper mm per
                                         # model mm, and the view's CAD X/Y bounds (mm);
                                         # paper (x, y) = origin + ((X - u0), (v1 - Y)) * scale
   "partsSheetSvg": str | null,          # drawing/assembly-parts.svg (two or more parts)
   "partsSheet": {"paper": [w, h],       # its layout (manifest sheet "parts", partsheet.py):
                  "items": [{"item": int, "parts": [id, ...], "view": str,
                             "right": [x, y, z], "up": [x, y, z],  # part-frame axes of paper
                             "origin": [x, y], "scale": float,     # right / paper up
                             "extent": [u0, v0, u1, v1]}],
                  # paper (x, y) = origin + ((P.right - u0), (v1 - P.up)) * scale
                  "stand": [x0, y0, x1, y1]} | null,  # paper area the assembly is built over
   "sketch": {"src": str,                # the source sketch (manifest sourceImage, copied
              "width": int,              # by the build stage) as a data: URL, downscaled
              "height": int} | null}     # to SKETCH_MAX_PX on the long side: WebP, or a
                                         # 16-level PNG when that is smaller (line art)

The story runs sketch -> drawing sheet -> wire -> solid; a missing sketch or sheet
skips its stage. With two or more parts the sheet is the parts sheet: each part's
wire lies on its own printed view, rises, and flies to its place in the assembly.

The build GLBs (parts/<id>.glb) carry UVs, normals and one primitive per face; the
viewer re-meshes each part from its STEP shape with tolerances relative to the part
size instead, which keeps a many-part page small. Wire guides are sampled on the
exact surfaces of the same shape, and every mesh triangle lies within the linear
deflection (max(MESH_LINEAR_MIN_MM, MESH_LINEAR_SHARE x part diagonal) mm) of them,
so wire and solid differ by at most that much (about 0.2 mm on a 200 mm part).
"""
import base64
import html
import io
import json
import math
import re
import shutil
import struct
from pathlib import Path

import numpy as np
from OCP.BRep import BRep_Tool
from PIL import Image
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepTools import BRepTools
from OCP.TopAbs import TopAbs_REVERSED
from OCP.TopLoc import TopLoc_Location

from .build import load_manifest, load_part_shapes, write_manifest
from .errors import ModelError
from .schema import DEFAULT_PART_MATERIAL, DEFAULT_WIRE_COLOR
from .wire import wire_guides

ASSETS = Path(__file__).resolve().parents[2] / "assets" / "viewer"
PLACEHOLDER = re.compile(r"__SKETCHCAD_(TITLE|DATA|BUNDLE)__")
# CAD frame (mm, Z-up) -> glTF frame (Y-up): (x, y, z) -> (x, z, -y)
CAD_TO_GLTF = np.array([[1.0, 0, 0], [0, 0, 1.0], [0, -1.0, 0]])
DIGITS = 7  # 0.1 micrometer in meters
WIRE_DIGITS = 6  # 1 micrometer: far below a pixel, and shorter JSON for the drawn wire
# Display mesh tolerances: linear relative to the part diagonal (mm), angular in radians.
MESH_LINEAR_SHARE = 0.001
MESH_LINEAR_MIN_MM = 0.05
MESH_ANGULAR_RAD = 0.3
# Source sketch: long side in pixels (about 2x the largest card the page shows) and
# WebP quality.
SKETCH_MAX_PX = 1400
SKETCH_WEBP_QUALITY = 80
SKETCH_GRAY_SPREAD = 6  # mean channel spread below which the sketch is stored as grayscale
# Default part colors, assigned in part order (cycled): muted tones that sit on the
# viewer's warm off-white page and stay apart from each other and from the wire color.
PALETTE = ["#B9B1A8",  # warm grey
           "#94A894",  # sage
           "#BC8269",  # terracotta
           "#8193AB",  # slate blue
           "#D6C39E",  # sand
           "#5F5D5B",  # charcoal
           "#A9A07A",  # olive
           "#A88F9E"]  # mauve


def _num(v, digits=DIGITS):
    v = round(float(v), digits)
    return 0.0 if v == 0 else v  # no "-0.0" in the JSON


def to_viewer_point(p, digits=DIGITS):
    x, y, z = p
    return [_num(x / 1000.0, digits), _num(z / 1000.0, digits), _num(-y / 1000.0, digits)]


def _rot(axis, deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    i, j = [(1, 2), (2, 0), (0, 1)][axis]
    r = np.eye(3)
    r[i, i], r[i, j], r[j, i], r[j, j] = c, -s, s, c
    return r


def placement_matrix(pl):
    """build.place_shape's transform as a column-major 4x4 in the glTF frame (meters)."""
    rx, ry, rz = pl["rotateDeg"]
    r = _rot(2, rz) @ _rot(1, ry) @ _rot(0, rx)  # X first, then Y, then Z
    m = np.eye(4)
    m[:3, :3] = CAD_TO_GLTF @ r @ CAD_TO_GLTF.T
    m[:3, 3] = CAD_TO_GLTF @ np.array(pl["translateMm"], dtype=float) / 1000.0
    return [_num(v) for v in m.T.flatten()]


def _viewer_wire(shape):
    groups = wire_guides(shape, {})
    for g in groups:
        g["lines"] = [[to_viewer_point(p, WIRE_DIGITS) for p in line] for line in g["lines"]]
    return groups


def _triangulate(shape):
    """Absolute-tolerance triangulation of every face: (vertices mm, triangles)."""
    BRepTools.Clean_s(shape.wrapped)
    linear = max(MESH_LINEAR_MIN_MM, MESH_LINEAR_SHARE * shape.bounding_box().diagonal)
    BRepMesh_IncrementalMesh(shape.wrapped, linear, False, MESH_ANGULAR_RAD, True)
    vertices, triangles = [], []
    for face in shape.faces():
        loc = TopLoc_Location()
        poly = BRep_Tool.Triangulation_s(face.wrapped, loc)
        if poly is None:
            continue
        trsf, offset = loc.Transformation(), len(vertices)
        for i in range(1, poly.NbNodes() + 1):
            p = poly.Node(i).Transformed(trsf)
            vertices.append((p.X(), p.Y(), p.Z()))
        flip = face.wrapped.Orientation() == TopAbs_REVERSED
        for t in poly.Triangles():
            a, b, c = (offset + k - 1 for k in t.Get())
            triangles.append((a, c, b) if flip else (a, b, c))
    BRepTools.Clean_s(shape.wrapped)
    return np.array(vertices, dtype=float).reshape(-1, 3), np.array(triangles, dtype=np.uint32).reshape(-1, 3)


def viewer_glb(shape):
    """The shape as a minimal binary glTF for the viewer: one primitive, POSITION
    (meters, Y-up) and triangle indices. Faces keep their own vertices, so normals
    the page derives stay sharp across edges and smooth within a curved face."""
    vertices, triangles = _triangulate(shape)
    positions = (vertices @ CAD_TO_GLTF.T / 1000.0).astype("<f4")
    index_type, index_code = ("<u2", 5123) if len(positions) < 65536 else ("<u4", 5125)
    indices = triangles.astype(index_type)
    pos_bytes, idx_bytes = positions.tobytes(), indices.tobytes()
    idx_bytes += b"\0" * (-len(idx_bytes) % 4)
    meta = {
        "asset": {"version": "2.0", "generator": "sketch-to-cad viewer"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "mode": 4}]}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": len(positions), "type": "VEC3",
             "min": positions.min(axis=0).tolist(), "max": positions.max(axis=0).tolist()},
            {"bufferView": 1, "componentType": index_code, "count": indices.size, "type": "SCALAR"}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(pos_bytes), "target": 34962},
            {"buffer": 0, "byteOffset": len(pos_bytes), "byteLength": indices.nbytes, "target": 34963}],
        "buffers": [{"byteLength": len(pos_bytes) + len(idx_bytes)}],
    }
    js = json.dumps(meta, separators=(",", ":")).encode("ascii")
    js += b" " * (-len(js) % 4)
    binary = pos_bytes + idx_bytes
    total = 12 + 8 + len(js) + 8 + len(binary)
    return (struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I4s", len(js), b"JSON") + js
            + struct.pack("<I4s", len(binary), b"BIN\0") + binary)


def _top_view(manifest, sheet_id):
    if manifest.get("stages", {}).get("drawing") != "ok":
        return None
    for sheet in manifest.get("drawing", {}).get("sheets", []):
        if sheet["id"] == sheet_id:
            return sheet.get("topView")
    return None


def _parts_sheet(manifest):
    if manifest.get("stages", {}).get("drawing") != "ok":
        return None
    for sheet in manifest.get("drawing", {}).get("sheets", []):
        if sheet["id"] == "assembly-parts":
            return sheet.get("parts")
    return None


def _svg(out, manifest, sheet_id):
    if manifest.get("stages", {}).get("drawing") != "ok":
        return None
    for sheet in manifest.get("drawing", {}).get("sheets", []):
        if sheet["id"] == sheet_id:
            return (out / sheet["svg"]).read_text(encoding="utf-8")
    return None


def sketch_image(path):
    """The sketch at `path` as {"src": data URL, "width", "height"}; None if unreadable."""
    try:
        with Image.open(path) as im:
            im.load()
            im = im.convert("RGBA")  # palette, 1-bit and alpha modes alike
    except Exception:  # Pillow raises many types for broken files
        return None
    page = Image.new("RGBA", im.size, "white")
    page.alpha_composite(im)
    rgb = page.convert("RGB")
    scale = min(1.0, SKETCH_MAX_PX / max(rgb.size))
    if scale < 1:
        rgb = rgb.resize((max(1, round(rgb.width * scale)), max(1, round(rgb.height * scale))),
                         Image.LANCZOS)
    a = np.asarray(rgb, dtype=np.int16)
    gray = float((a.max(axis=2) - a.min(axis=2)).mean()) < SKETCH_GRAY_SPREAD
    img = rgb.convert("L") if gray else rgb
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=SKETCH_WEBP_QUALITY, method=6)
    best = ("image/webp", buf.getvalue())
    if gray:
        buf = io.BytesIO()
        img.quantize(16).save(buf, "PNG", optimize=True)
        if len(buf.getvalue()) < len(best[1]):
            best = ("image/png", buf.getvalue())
    mime, raw = best
    return {"src": f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}",
            "width": img.width, "height": img.height}


def _sketch(out, manifest):
    rel = manifest.get("sourceImage")
    if not isinstance(rel, str):
        return None
    base = Path(out).resolve()
    path = (base / rel).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        return None
    return sketch_image(path)


def _data(out, manifest, model):
    shapes = load_part_shapes(out)
    placement = {pl["part"]: pl for pl in manifest["assembly"]["placement"]}
    viewer = model.get("viewer", {})
    colors = viewer.get("partColors", {})
    materials = viewer.get("partMaterials", {})
    parts = []
    single = len(shapes) == 1
    for i, (pid, shape) in enumerate(shapes.items()):
        parts.append({
            "id": pid,
            "color": colors.get(pid, PALETTE[i % len(PALETTE)]),
            "material": materials.get(pid, DEFAULT_PART_MATERIAL),
            "glbBase64": base64.b64encode(viewer_glb(shape)).decode("ascii"),
            "wire": _viewer_wire(shape),
            "placement": placement_matrix(placement[pid]),
            "drawingSvg": _svg(out, manifest, pid) if single else None,
        })
    return {
        "name": manifest.get("name", ""),
        "wireColor": viewer.get("wireColor", DEFAULT_WIRE_COLOR),
        "parts": parts,
        "sketch": _sketch(out, manifest),
        "sheetTopView": None if len(parts) >= 2 else _top_view(manifest, parts[0]["id"]),
        "partsSheetSvg": _svg(out, manifest, "assembly-parts") if len(parts) >= 2 else None,
        "partsSheet": _parts_sheet(manifest) if len(parts) >= 2 else None,
    }


def render_html(data):
    template = (ASSETS / "template.html").read_text(encoding="utf-8")
    bundle = (ASSETS / "viewer.bundle.js").read_text(encoding="utf-8")
    bundle = re.sub(r"</(script)", r"<\\/\1", bundle, flags=re.I)
    if "<!--" in bundle:
        raise RuntimeError("viewer.bundle.js contains '<!--', which cannot be inlined in <script>")
    values = {
        "TITLE": html.escape(data["name"] or "sketch-to-cad"),
        "DATA": json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c"),
        "BUNDLE": bundle,
    }
    return PLACEHOLDER.sub(lambda m: values[m.group(1)], template)  # one pass, no re-scan


def run_viewer(out):
    """Write OUT/viewer/index.html (built in viewer.tmp, then renamed) and update the manifest."""
    out = Path(out)
    try:
        manifest = load_manifest(out)
        model = json.loads((out / "model.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ModelError(f"{out}: not a build output folder ({e})") from e
    if manifest.get("stages", {}).get("build") != "ok":
        raise ModelError(f"{out}: build stage has not completed")
    final, stage = out / "viewer", out / "viewer.tmp"
    for p in (final, stage):
        if p.exists():
            raise ModelError(f"{p}: already exists")
    page = render_html(_data(out, manifest, model))
    stage.mkdir()
    try:
        (stage / "index.html").write_text(page, encoding="utf-8")
        stage.rename(final)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    manifest["stages"]["viewer"] = "ok"
    manifest["viewer"] = {"file": "viewer/index.html"}
    try:
        write_manifest(out, manifest)
    except BaseException:  # never leave viewer/ behind without its manifest entry
        shutil.rmtree(final, ignore_errors=True)
        raise
    return final / "index.html"
