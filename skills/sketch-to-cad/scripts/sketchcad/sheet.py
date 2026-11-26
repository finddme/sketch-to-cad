"""A4 landscape drawing sheet: frame, views, dimensions, notes, title block; PDF export."""
import datetime
import io
import math
import xml.etree.ElementTree as ET

from . import dims as D

PAGE_W, PAGE_H = 297.0, 210.0
FRAME = 10.0
SIDE_W = 80.0
TITLE_H = 36.0
SIDE_X = PAGE_W - FRAME - SIDE_W
DRAW_AREA = (FRAME + 5, FRAME + 5, SIDE_X - 5, PAGE_H - FRAME - 5)

VISIBLE_W, SMOOTH_W, HIDDEN_W, CENTER_W = 0.5, 0.2, 0.25, 0.18
HIDDEN_DASH, CENTER_DASH = "1.6 0.8", "4 0.8 0.8 0.8"
AXIS_OVER = 2.0  # centre lines run this far past the feature
NOTE_SIZE = 2.6
VIEW_TITLES = {"front": "정면도 FRONT", "top": "평면도 TOP", "right": "우측면도 RIGHT", "iso": "등각도 ISOMETRIC"}
PROJECTION_NAMES = {"third": "3각법 (third angle)", "first": "1각법 (first angle)"}
SVG_NS = "http://www.w3.org/2000/svg"


def _paper(box, p):
    u0, _v0, _u1, v1 = box["extent"]
    s, (px, py) = box["scale"], box["origin"]
    return (px + (p[0] - u0) * s, py + (v1 - p[1]) * s)


PAPER_TOL = 0.01  # mm on paper: polyline points closer than this to the simplified line are dropped


def simplify(points, tol=PAPER_TOL):
    """Ramer-Douglas-Peucker on paper coordinates: keeps the end points and every
    point that lies more than `tol` from the chord of its kept neighbours, so the
    drawn line moves by at most `tol` (far below the thinnest stroke)."""
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        i, j = stack.pop()
        (ax, ay), (bx, by) = points[i], points[j]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy)
        far, far_d = None, tol
        for k in range(i + 1, j):
            px, py = points[k]
            d = (abs(dx * (ay - py) - dy * (ax - px)) / norm) if norm > 0 else math.hypot(px - ax, py - ay)
            if d > far_d:
                far, far_d = k, d
        if far is not None:
            keep[far] = True
            stack += [(i, far), (far, j)]
    return [p for p, k in zip(points, keep) if k]


def _lines(box, lines, width, color=D.INK, dash=None):
    """One <g> carrying the stroke style around bare polylines (the style is not
    repeated on each of the thousands of lines an assembly view can have)."""
    if not lines:
        return []
    d = f' stroke-dasharray="{dash}"' if dash else ""
    out = [f'<g fill="none" stroke="{color}" stroke-width="{width:g}" stroke-linecap="round" '
           f'stroke-linejoin="round"{d}>']
    for ln in lines:
        pts = " ".join(f"{x:.3f},{y:.3f}" for x, y in simplify([_paper(box, p) for p in ln]))
        out.append(f'<polyline points="{pts}"/>')
    out.append("</g>")
    return out


def _view(name, view, placed):
    proj, box = view["proj"], view["box"]
    u0, v0, u1, v1 = box["extent"]
    s, (x0, y0) = box["scale"], box["origin"]
    x1, y1 = x0 + (u1 - u0) * s, y0 + (v1 - v0) * s
    out = [f'<g data-view="{name}" data-box="{x0:.3f} {y0:.3f} {x1:.3f} {y1:.3f}">']
    if name != "iso":
        out += _lines(box, proj["hidden"], HIDDEN_W, D.HIDDEN_INK, HIDDEN_DASH)
    out += _lines(box, proj.get("smooth", []), SMOOTH_W)
    out += _lines(box, proj["visible"], VISIBLE_W)
    if name != "iso":
        for cu, cv, r in proj["circles"]:
            (cx, cy), rp = _paper(box, (cu, cv)), r * s
            k = rp + 1.5
            out += [D.line(cx - k, cy, cx + k, cy, CENTER_W, D.HIDDEN_INK, CENTER_DASH),
                    D.line(cx, cy - k, cx, cy + k, CENTER_W, D.HIDDEN_INK, CENTER_DASH)]
    for a, b in view.get("axes", []):
        (ax, ay), (bx, by) = _paper(box, a), _paper(box, b)
        n = math.hypot(bx - ax, by - ay) or 1.0
        ux, uy = (bx - ax) / n * AXIS_OVER, (by - ay) / n * AXIS_OVER
        out.append(D.line(ax - ux, ay - uy, bx + ux, by + uy, CENTER_W, D.HIDDEN_INK, CENTER_DASH))
    bottom = max([y1 + (AXIS_OVER if view.get("axes") else 0.0)] + [p.bbox[3] for p in placed if p.bbox and p.dim.view == name])
    out.append(D.view_title((x0 + x1) / 2, bottom + 4.5, VIEW_TITLES[name]))
    out.append("</g>")
    return "".join(out)


def _fit(s, width, size):
    lines = D.wrap(s, width, size)
    return lines[0] + ("…" if len(lines) > 1 else "")


def title_block(title, subtitle, scale, projection, x0=SIDE_X, x1=PAGE_W - FRAME, bottom=PAGE_H - FRAME):
    """Title block of TITLE_H between x0 and x1 ending at `bottom` (paper mm)."""
    y0 = bottom - TITLE_H
    mid = (x0 + x1) / 2
    out = [f'<rect x="{x0:g}" y="{y0:g}" width="{x1 - x0:g}" height="{TITLE_H}" fill="none" '
           f'stroke="{D.INK}" stroke-width="0.35"/>']
    rows = [(12, [("이름 NAME", title, x0, x1, 4.2)]),
            (8, [("부품 PART", subtitle, x0, x1, 3.0)]),
            (8, [("단위 UNIT", "mm", x0, mid, 3.0), ("축척 SCALE", scale, mid, x1, 3.0)]),
            (8, [("날짜 DATE", datetime.date.today().isoformat(), x0, mid, 3.0),
                 ("투상법 PROJECTION", PROJECTION_NAMES[projection], mid, x1, 3.0)])]
    y = y0
    for i, (h, cells) in enumerate(rows):
        if i:
            out.append(D.line(x0, y, x1, y, 0.25, D.INK))
        for j, (cap, value, cx0, cx1, size) in enumerate(cells):
            if j:
                out.append(D.line(cx0, y, cx0, y + h, 0.25, D.INK))
            out.append(D.text(cx0 + 1.5, y + 2.6, cap, 1.8, extra=f' fill="{D.HIDDEN_INK}"'))
            out.append(D.text(cx0 + 1.5, y + h - 1.6, _fit(value, cx1 - cx0 - 3, size), size))
        y += h
    return "".join(out)


NOTE_SIZES = (NOTE_SIZE, 2.4, 2.2, 2.0)  # notes shrink down to the last size before they continue
NOTE_W = SIDE_W - 6
NOTES_TOP = FRAME + 13          # top of the first note line (under the NOTES heading)
NOTES_BOTTOM = PAGE_H - FRAME - TITLE_H - 4
CONTINUED = "→ 계속 (continued): {}"


def _note_items(notes, rows, size):
    items = D.note_lines(notes, NOTE_W, size, numbered=True)
    if rows:
        items += D.table_lines("치수표 DIMENSIONS", ("항목", "mm"), rows, NOTE_W, size)
    return items


def _take(items, capacity):
    """Split off the leading items that fit `capacity` (at least one, so flowing always ends).

    An item marked keep-with-next (the table title) never ends a column.
    """
    used, n = 0.0, 0
    for h, _draw, _keep in items:
        if n and used + h > capacity:
            break
        used, n = used + h, n + 1
    while n > 1 and n < len(items) and items[n - 1][2]:
        n -= 1
    return items[:n], items[n:]


# Columns of a notes sheet: (x, top, bottom). Two in the drawing area, one above the title block.
NOTES_PAGE_COLUMNS = ((FRAME + 5, NOTES_TOP, PAGE_H - FRAME - 5),
                      (FRAME + 5 + NOTE_W + 12, NOTES_TOP, PAGE_H - FRAME - 5),
                      (SIDE_X + 3, NOTES_TOP, NOTES_BOTTOM))


def flow_notes(notes, rows):
    """Lay out notes + dimension table: (size, main_items, pages).

    The main sheet's notes column takes everything if it fits at some size
    in NOTE_SIZES. Otherwise, at NOTE_SIZE, it takes what fits plus a
    continuation line, and the rest flows into notes sheets (pages: lists of
    columns, each a list of items) laid out as NOTES_PAGE_COLUMNS. Nothing is
    dropped.
    """
    main_cap = NOTES_BOTTOM - NOTES_TOP
    for size in NOTE_SIZES:
        items = _note_items(notes, rows, size)
        if sum(item[0] for item in items) <= main_cap:
            return size, items, []
    size = NOTE_SIZE
    cont_h = size * D.NOTE_LEAD
    main, rest = _take(_note_items(notes, rows, size), main_cap - cont_h)
    pages = []
    while rest:
        cols = []
        for k, (_x, top, bottom) in enumerate(NOTES_PAGE_COLUMNS):
            col, after = _take(rest, bottom - top)
            if after and k == len(NOTES_PAGE_COLUMNS) - 1:
                col, after = _take(rest, bottom - top - cont_h)
            cols.append(col)
            rest = after
            if not rest:
                break
        pages.append(cols)
    return size, main, pages


def _column(x, top, items, size, continued):
    out, y = [], top
    for h, draw, _keep in items:
        out.append(draw(x, y))
        y += h
    if continued:
        out.append(D.text(x, y + size, CONTINUED.format(continued), size))
    return "".join(out)


def _frame():
    return [f'<svg xmlns="{SVG_NS}" width="{PAGE_W:g}mm" height="{PAGE_H:g}mm" '
            f'viewBox="0 0 {PAGE_W:g} {PAGE_H:g}">',
            f'<rect x="0" y="0" width="{PAGE_W:g}" height="{PAGE_H:g}" fill="#ffffff"/>',
            f'<g font-family="{D.FONT_FAMILY}" fill="{D.INK}">',
            f'<rect x="{FRAME}" y="{FRAME}" width="{PAGE_W - 2 * FRAME}" height="{PAGE_H - 2 * FRAME}" '
            f'fill="none" stroke="{D.INK}" stroke-width="0.5"/>',
            D.line(SIDE_X, FRAME, SIDE_X, PAGE_H - FRAME, 0.35, D.INK)]


def render_sheet(title, views, placed, notes, projection, *, scale, subtitle="", notes_sheet_name=None):
    """Return the sheet SVGs: [main sheet, notes sheet 1, ...].

    views: {name: {"proj": project_view(...), "box": layout view box,
                   "axes": optional centre-line segments in view coordinates}};
    placed: drawing.layout_dims output (bbox None rows go to the table).
    Notes and the table that do not fit the notes column continue on notes
    sheets; notes_sheet_name(k) (k = 1, 2, ...) names them in the
    continuation line.
    """
    rows = [(p.dim.name or "-", p.dim.label) for p in placed if p.bbox is None]
    size, main, pages = flow_notes(notes, rows)
    name = notes_sheet_name or (lambda k: f"notes-{k}")
    out = _frame()
    out += [_view(n, v, placed) for n, v in views.items()]
    out.append('<g data-dims="1">')
    out += [D.draw(p.geom) for p in placed if p.geom is not None]
    out.append("</g>")
    out.append(D.text(SIDE_X + 3, FRAME + 7, "주석 NOTES", 3.2))
    out.append(_column(SIDE_X + 3, NOTES_TOP, main, size, name(1) if pages else None))
    out.append(title_block(title, subtitle, scale, projection))
    out.append("</g></svg>")
    sheets = ["".join(out)]
    for k, cols in enumerate(pages, start=1):
        out = _frame()
        out.append(D.text(FRAME + 5, FRAME + 7, "주석 NOTES (계속 continued)", 3.2))
        for i, ((x, top, _bottom), col) in enumerate(zip(NOTES_PAGE_COLUMNS, cols)):
            last = i == len(cols) - 1 and k < len(pages)
            out.append(_column(x, top, col, size, name(k + 1) if last else None))
        out.append(title_block(title, f"{subtitle} · 주석 NOTES", scale, projection))
        out.append("</g></svg>")
        sheets.append("".join(out))
    return sheets


# ---- PDF -----------------------------------------------------------------------------

_svglib_ready = False


def _register_svglib_fonts():
    global _svglib_ready
    if _svglib_ready:
        return
    from svglib.fonts import register_font

    # svglib maps an SVG family to a reportlab font only by (re)registering a TTF.
    register_font("Outfit", D.font_source(D.LATIN_FONT), rlgFontName=D.LATIN_FONT)
    register_font("Gowun Batang", D.font_source(D.HANGUL_FONT), rlgFontName=D.HANGUL_FONT)
    _svglib_ready = True


def svg_drawing(svg):
    """svglib Drawing of a sheet. Text that Outfit cannot show (Hangul) is
    switched wholesale to Gowun Batang, since svglib does not fall back
    per glyph."""
    from svglib.svglib import svg2rlg

    _register_svglib_fonts()
    ET.register_namespace("", SVG_NS)
    root = ET.fromstring(svg)
    for t in root.iter(f"{{{SVG_NS}}}text"):
        t.set("font-family", "Gowun Batang" if D.needs_hangul_font(t.text or "") else "Outfit")
    return svg2rlg(io.BytesIO(ET.tostring(root, encoding="utf-8")))


def svg_to_pdf(svg, out):
    from reportlab.graphics import renderPDF

    renderPDF.drawToFile(svg_drawing(svg), str(out))
