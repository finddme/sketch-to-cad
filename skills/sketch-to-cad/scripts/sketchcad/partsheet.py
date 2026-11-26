"""Parts sheet (부품 배치도): every unique part on its own, one common scale.

A3 landscape. Left: one view per BOM row (the row's first part, in its own part
frame, from the side that shows the largest silhouette, turned a quarter on paper
when it stands tall), shelf-packed at the largest standard scale that fits, each
with an item balloon (circle with the item number, leader with a dot to the part
outline, "×n" for repeated parts). Right: the BOM table (품번 · 품명 · 수량 ·
재질/마감 · 주요 치수 · 역할), a summary strip (parts, unique parts, overall size,
estimated mass) and the title block.

parts_sheet() returns the SVG and the layout record the manifest keeps, so the
viewer can lay each part's wire exactly onto its printed view:
  paper (x, y) = origin + ((u - u0), (v1 - v)) * scale,
  u = P . right, v = P . up (P: a point in the part's own frame, mm),
  extent = [u0, v0, u1, v1]; the side the view shows faces right x up.
"""
import math

from . import dims as D
from .bom import format_mass, mass_grams
from .drawing import SCALES, center_lines
from .errors import ModelError
from .project import project_view
from .sheet import AXIS_OVER, CENTER_DASH, CENTER_W, SMOOTH_W, SVG_NS, VISIBLE_W, _lines, title_block

SHEET_ID = "assembly-parts"
PAGE_W, PAGE_H = 420.0, 297.0  # A3 landscape
FRAME = 10.0
PANEL_W = 178.0
PANEL_X = PAGE_W - FRAME - PANEL_W
AREA = (FRAME + 7, FRAME + 6, PANEL_X - 7, PAGE_H - FRAME - 6)  # where the part views go
BALLOON_R = 2.6
BAND = 9.0        # room above each view for its balloon
GAP_X, GAP_Y = 7.0, 3.0
MAX_GAP_X, MAX_GAP_Y = 22.0, 26.0
ROTATE_RATIO = 1.3  # a view this much taller than wide is turned to lie along the sheet
LEADER_W = 0.18
DOT_R = 0.45
TITLE_H = 36.0
SUMMARY_H = 13.0
TABLE_TOP = FRAME + 11
COLUMNS = (("품번", "ITEM", 10), ("품명", "NAME", 40), ("수량", "QTY", 10),
           ("재질 · 마감", "MATERIAL / FINISH", 44), ("주요 치수", "SIZE (mm)", 26), ("역할", "ROLE", 48))
ROW_MAX = 6.4     # table row height (two text lines) ...
TEXT = 1.9        # ... and its text size; both shrink together for a long table
VIEW_AXES_3D = {"front": ((1, 0, 0), (0, 0, 1)), "top": ((1, 0, 0), (0, 1, 0)), "right": ((0, 1, 0), (0, 0, 1))}


ROUND_TOL = 0.03    # two bounding-box sides this close: a round (turned) part seen along its axis ...
FLAT_SHARE = 0.35   # ... shows its face only when it is this flat (a wheel, a disc); else its profile


def pick_view(bounds):
    """front / top / right: the side with the largest bounding-box silhouette, except that a
    round part that is not flat is drawn in profile rather than end-on (a knob, a weight)."""
    (x0, y0, z0), (x1, y1, z1) = bounds["min"], bounds["max"]
    dx, dy, dz = x1 - x0, y1 - y0, z1 - z0
    areas = {"front": dx * dz, "top": dx * dy, "right": dy * dz}
    across = {"front": (dx, dz, dy), "top": (dx, dy, dz), "right": (dy, dz, dx)}  # (screen, screen, depth)
    order = sorted(areas, key=lambda v: (-round(areas[v], 6), list(areas).index(v)))
    for v in order:
        a, b, depth = across[v]
        end_on = abs(a - b) <= ROUND_TOL * max(a, b) and depth >= FLAT_SHARE * max(a, b)
        if not end_on:
            return v
    return order[0]


def _turn(lines):
    return [[(v, -u) for u, v in ln] for ln in lines]


def item_view(part, shape, bounds):
    """The drawn view of one part: polylines and centre lines in (u, v), its axes and extent."""
    view = pick_view(bounds)
    proj = project_view(shape, view)
    right, up = VIEW_AXES_3D[view]
    u0, v0, u1, v1 = proj["extent"]
    visible, smooth = proj["visible"], proj.get("smooth", [])
    axes = [list(seg) for seg in center_lines(part, shape).get(view, [])]
    if v1 - v0 > ROTATE_RATIO * (u1 - u0):  # stands tall: turn it a quarter (view up -> paper right)
        visible, smooth, axes = _turn(visible), _turn(smooth), _turn(axes)
        right, up = up, tuple(-c for c in right)
        u0, v0, u1, v1 = v0, -u1, v1, -u0
    return {"view": view, "right": list(right), "up": list(up), "extent": [u0, v0, u1, v1],
            "visible": visible, "smooth": smooth, "axes": axes}


def _size(v, scale):
    u0, v0, u1, v1 = v["extent"]
    return (u1 - u0) * scale, (v1 - v0) * scale


def _cell_w(v, qty, scale):
    w, _h = _size(v, scale)
    tag = D.text_width(f"×{qty}", 2.4) + 1.5 if qty > 1 else 0.0
    return max(w, 2 * BALLOON_R + tag + 1)


def pack(views, qtys, scale, area=AREA):
    """Shelf packing at `scale`: rows of view indices (tallest first), or None if it does not fit."""
    aw, ah = area[2] - area[0], area[3] - area[1]
    order = sorted(range(len(views)), key=lambda i: (-_size(views[i], scale)[1], i))
    rows, cur, x = [], [], 0.0
    for i in order:
        w = _cell_w(views[i], qtys[i], scale)
        if w > aw:
            return None
        if cur and x + w > aw:
            rows.append(cur)
            cur, x = [], 0.0
        cur.append(i)
        x += w + GAP_X
    rows.append(cur)
    height = sum(max(_size(views[i], scale)[1] for i in r) + BAND for r in rows) + GAP_Y * (len(rows) - 1)
    return rows if height <= ah else None


def layout(views, qtys, area=AREA):
    """Largest standard scale that packs; returns (scale, label, origins) with each view's
    paper top-left, rows spread over the area and each row spread across it."""
    for scale, label in SCALES:
        rows = pack(views, qtys, scale, area)
        if rows:
            break
    else:
        raise ModelError(f"{SHEET_ID}: parts too large for the parts sheet at {SCALES[-1][1]}")
    aw, ah = area[2] - area[0], area[3] - area[1]
    heights = [max(_size(views[i], scale)[1] for i in r) + BAND for r in rows]
    gap_y = min(MAX_GAP_Y, GAP_Y + (ah - sum(heights) - GAP_Y * (len(rows) - 1)) / max(1, len(rows) - 1)) \
        if len(rows) > 1 else 0.0
    y = area[1] + (ah - sum(heights) - gap_y * (len(rows) - 1)) / 2
    origins = [None] * len(views)
    for r, h in zip(rows, heights):
        widths = [_cell_w(views[i], qtys[i], scale) for i in r]
        gap_x = min(MAX_GAP_X, (aw - sum(widths)) / max(1, len(r) - 1)) if len(r) > 1 else 0.0
        x = area[0] + (aw - sum(widths) - gap_x * (len(r) - 1)) / 2
        for i, w in zip(r, widths):
            vw, vh = _size(views[i], scale)
            origins[i] = (x + (w - vw) / 2, y + BAND + (h - BAND - vh) / 2)
            x += w + gap_x
        y += h + gap_y
    return scale, label, origins


# ---- drawing --------------------------------------------------------------------

def _box(v, scale, origin):
    return {"extent": v["extent"], "scale": scale, "origin": origin}


def _paper(box, p):
    u0, _v0, _u1, v1 = box["extent"]
    return (box["origin"][0] + (p[0] - u0) * box["scale"], box["origin"][1] + (v1 - p[1]) * box["scale"])


def _balloon(item, qty, v, box):
    """Item balloon above the view's top-left corner, leader to the nearest outline point."""
    x0, y0 = box["origin"]
    cx, cy = x0 + BALLOON_R, y0 - BAND + BALLOON_R + 1.0
    pts = [_paper(box, p) for ln in v["visible"] for p in ln] or [(x0, y0)]
    tx, ty = min(pts, key=lambda p: math.dist(p, (cx, cy)))
    d = math.dist((tx, ty), (cx, cy))
    out = [f'<g data-balloon="{item}">',
           f'<circle cx="{cx:.3f}" cy="{cy:.3f}" r="{BALLOON_R}" fill="#ffffff" stroke="{D.INK}" stroke-width="0.3"/>',
           D.text(cx, cy + 1.0, item, 2.8, "middle")]
    if d > BALLOON_R + 0.6:
        sx, sy = cx + (tx - cx) / d * BALLOON_R, cy + (ty - cy) / d * BALLOON_R
        out.append(D.line(sx, sy, tx, ty, LEADER_W, D.INK))
        out.append(f'<circle cx="{tx:.3f}" cy="{ty:.3f}" r="{DOT_R}" fill="{D.INK}"/>')
    if qty > 1:
        out.append(D.text(cx + BALLOON_R + 1.2, cy + 0.9, f"×{qty}", 2.4))
    out.append("</g>")
    return "".join(out)


def _item_svg(row, v, box):
    x0, y0 = box["origin"]
    w, h = _size(v, box["scale"])
    out = [f'<g data-item="{row["item"]}" data-box="{x0:.3f} {y0:.3f} {x0 + w:.3f} {y0 + h:.3f}">']
    out += _lines(box, v["smooth"], SMOOTH_W)
    out += _lines(box, v["visible"], VISIBLE_W)
    for a, b in v["axes"]:
        (ax, ay), (bx, by) = _paper(box, a), _paper(box, b)
        n = math.hypot(bx - ax, by - ay) or 1.0
        ux, uy = (bx - ax) / n * AXIS_OVER, (by - ay) / n * AXIS_OVER
        out.append(D.line(ax - ux, ay - uy, bx + ux, by + uy, CENTER_W, D.HIDDEN_INK, CENTER_DASH))
    out.append("</g>")
    out.append(_balloon(row["item"], row["qty"], v, box))
    return "".join(out)


def _cell_lines(s, width, size, limit=2):
    lines = D.wrap(s, width, size) if s else [""]
    if len(lines) > limit:
        lines = lines[:limit]
        while lines[-1] and D.text_width(lines[-1] + "…", size) > width:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "…"
    return lines


def _table(rows, top, bottom):
    x0, x1 = PANEL_X, PAGE_W - FRAME
    head_h = 7.0
    row_h = min(ROW_MAX, (bottom - top - head_h) / max(1, len(rows)))
    size = TEXT * row_h / ROW_MAX
    out = [D.text(x0 + 3, top - 2.6, "부품표 BILL OF MATERIALS", 3.2)]
    out.append(D.line(x0, top, x1, top, 0.35, D.INK))
    x = x0
    for k, (ko, en, w) in enumerate(COLUMNS):
        if k:
            out.append(D.line(x, top, x, top + head_h + row_h * len(rows), 0.18, D.INK))
        out.append(D.text(x + w / 2, top + 3.1, ko, 2.0, "middle"))
        out.append(D.text(x + w / 2, top + 5.6, en, 1.4, "middle", extra=f' fill="{D.HIDDEN_INK}"'))
        x += w
    y = top + head_h
    out.append(D.line(x0, y, x1, y, 0.25, D.INK))
    lead = size * 1.25
    for r in rows:
        cells = [[str(r["item"])], None, [str(r["qty"])], r["material"] or "-", r["size"], r["role"] or "-"]
        x = x0
        for k, ((_ko, _en, w), cell) in enumerate(zip(COLUMNS, cells)):
            if k == 1:  # Korean name over the English one
                name = _cell_lines(r["name"], w - 2, size * 1.05, 1)[0]
                base = y + row_h / 2 - (0.15 if r["nameEn"] else -0.35) * size * 2
                out.append(D.text(x + 1, base, name, size * 1.05))
                if r["nameEn"]:
                    en = _cell_lines(r["nameEn"], w - 2, size * 0.8, 1)[0]
                    out.append(D.text(x + 1, base + lead, en, size * 0.8, extra=f' fill="{D.HIDDEN_INK}"'))
            elif k in (0, 2):
                out.append(D.text(x + w / 2, y + row_h / 2 + size * 0.35, cell[0], size * 1.05, "middle"))
            else:
                lines = _cell_lines(cell, w - 2, size)
                base = y + row_h / 2 + size * 0.35 - (len(lines) - 1) * lead / 2
                out += [D.text(x + 1, base + j * lead, s, size) for j, s in enumerate(lines)]
            x += w
        y += row_h
        out.append(D.line(x0, y, x1, y, 0.12, D.HIDDEN_INK))
    out.append(D.line(x0, y, x1, y, 0.35, D.INK))
    return "".join(out)


def _summary(cells, top):
    x0, x1 = PANEL_X, PAGE_W - FRAME
    out = [f'<rect x="{x0:g}" y="{top:.3f}" width="{x1 - x0:g}" height="{SUMMARY_H}" fill="none" '
           f'stroke="{D.INK}" stroke-width="0.35"/>']
    widths = [34, 34, 62, 48]
    x = x0
    for k, ((cap, value), w) in enumerate(zip(cells, widths)):
        if k:
            out.append(D.line(x, top, x, top + SUMMARY_H, 0.25, D.INK))
        out.append(D.text(x + 1.5, top + 3.0, cap, 1.8, extra=f' fill="{D.HIDDEN_INK}"'))
        out.append(D.text(x + 1.5, top + SUMMARY_H - 2.6, value, 3.6))
        x += w
    return "".join(out)


def _overall(bounds):
    return " × ".join(f"{hi - lo:.0f}" for lo, hi in zip(bounds["min"], bounds["max"])) + " mm"


def parts_sheet(model, rows, shapes, bounds, volumes, assembly_bounds, projection):
    """(svg, record): the parts sheet and its manifest layout (items, paper, stand)."""
    parts = {p["id"]: p for p in model["parts"]}
    views = [item_view(parts[r["parts"][0]], shapes[r["parts"][0]], bounds[r["parts"][0]]) for r in rows]
    qtys = [r["qty"] for r in rows]
    scale, label, origins = layout(views, qtys)

    title_top = PAGE_H - FRAME - TITLE_H
    grams, defaulted = mass_grams(rows, volumes)
    notes = ["무게: 모델 부피 × 재질 밀도" + (" (재질이 없는 부품은 PLA 기준)" if defaulted else "")]
    if model.get("assumptions"):
        notes.append("치수·재질은 가정값을 포함 (조립도 ASSEMBLY 주석의 가정 참조)")
    notes_top = title_top - 2.0 - 2.6 * len(notes)
    summary_top = notes_top - 1.5 - SUMMARY_H
    table_bottom = summary_top - 4.0

    out = [f'<svg xmlns="{SVG_NS}" width="{PAGE_W:g}mm" height="{PAGE_H:g}mm" viewBox="0 0 {PAGE_W:g} {PAGE_H:g}">',
           f'<rect x="0" y="0" width="{PAGE_W:g}" height="{PAGE_H:g}" fill="#ffffff"/>',
           f'<g font-family="{D.FONT_FAMILY}" fill="{D.INK}">',
           f'<rect x="{FRAME:g}" y="{FRAME:g}" width="{PAGE_W - 2 * FRAME:g}" height="{PAGE_H - 2 * FRAME:g}" '
           f'fill="none" stroke="{D.INK}" stroke-width="0.5"/>',
           D.line(PANEL_X, FRAME, PANEL_X, PAGE_H - FRAME, 0.35, D.INK)]
    items = []
    for row, v, origin in zip(rows, views, origins):
        box = _box(v, scale, origin)
        out.append(_item_svg(row, v, box))
        items.append({"item": row["item"], "parts": row["parts"], "view": v["view"], "right": v["right"],
                      "up": v["up"], "origin": [round(origin[0], 4), round(origin[1], 4)], "scale": scale,
                      "extent": [round(c, 6) for c in v["extent"]]})
    out.append(_table(rows, TABLE_TOP, table_bottom))
    out.append(_summary([("총 부품 수 PARTS", f"{sum(qtys)}"), ("고유 부품 UNIQUE", f"{len(rows)}종"),
                         ("전체 크기 OVERALL", _overall(assembly_bounds)), ("추정 무게 MASS", format_mass(grams))],
                        summary_top))
    for k, n in enumerate(notes):
        out.append(D.text(PANEL_X + 1.5, notes_top + 2.2 + 2.6 * k, n, 1.8, extra=f' fill="{D.HIDDEN_INK}"'))
    out.append(title_block(model["name"], "부품 배치도 PARTS", label, projection, PANEL_X, PAGE_W - FRAME,
                           PAGE_H - FRAME))
    out.append("</g></svg>")
    record = {"paper": [PAGE_W, PAGE_H], "items": items,
              # where the assembly stands when the viewer builds it on the laid sheet: over the table
              "stand": [PANEL_X, TABLE_TOP, PAGE_W - FRAME, table_bottom]}
    return "".join(out), record, label

