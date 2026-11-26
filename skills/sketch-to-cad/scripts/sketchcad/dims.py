"""Text metrics and SVG drawing primitives for dimensioned sheets.

dim_h, dim_v, radius_leader, note_lines, table_lines and view_title are
adapted from enesozturk/dimensional-drawing (MIT, Copyright (c) 2026 Enes
Ozturk). See THIRD_PARTY.md.

Changes from upstream: geometry builders return their segments and bounding
boxes so the layout can test collisions before anything is drawn; arrows
are explicit polygons (PDF converters ignore SVG markers); notes and table
rows are returned as flowable lines so the sheet can continue them on a
notes sheet; all lengths are paper millimetres.
"""
import io
import math
from html import escape
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

FONT_DIR = Path(__file__).resolve().parents[2] / "assets" / "fonts"
LATIN_FONT, HANGUL_FONT = "Outfit", "GowunBatang"
FONT_FAMILY = "Outfit, 'Gowun Batang', sans-serif"
OUTFIT_WEIGHT = 400

INK, HIDDEN_INK, THIN_INK = "#1f1f1f", "#6b6b6b", "#3a3a3a"
DIM_TEXT = 3.0
ARROW_L, ARROW_W = 2.5, 0.9
EXT_GAP, EXT_OVER = 1.0, 1.5
TEXT_GAP = 0.8
DIM_STROKE = 0.18
BOX_PAD = 0.6
ASCENT, DESCENT = 0.8, 0.2  # text box above / below the baseline, x font size

_fonts_ready = False
_outfit_cmap = set()
_font_sources = {}


def ensure_fonts():
    """Register the bundled fonts with reportlab once.

    Outfit ships as a variable font whose default instance is Thin; a
    Regular (wght 400) instance is cut in memory so PDF text is legible.
    """
    global _fonts_ready, _outfit_cmap
    if _fonts_ready:
        return
    from fontTools.ttLib import TTFont as FTFont
    from fontTools.varLib.instancer import instantiateVariableFont

    var = FTFont(FONT_DIR / "outfit-variable.ttf")
    regular = instantiateVariableFont(var, {"wght": OUTFIT_WEIGHT})
    for rec in regular["name"].names:
        if rec.nameID == 6:
            rec.string = "Outfit-Regular"
    _outfit_cmap = set(regular.getBestCmap())
    buf = io.BytesIO()
    regular.save(buf)
    _font_sources[LATIN_FONT] = buf.getvalue()
    _font_sources[HANGUL_FONT] = str(FONT_DIR / "gowun-batang.ttf")
    for name in (LATIN_FONT, HANGUL_FONT):
        pdfmetrics.registerFont(TTFont(name, _source(name)))
    _fonts_ready = True


def _source(name):
    src = _font_sources[name]
    return io.BytesIO(src) if isinstance(src, bytes) else src


def font_source(name):
    """A fresh TTF source (path or file object) for a registered font name."""
    ensure_fonts()
    return _source(name)


def needs_hangul_font(s):
    """True when Outfit lacks a glyph in `s` (Hangul and other non-Latin text)."""
    ensure_fonts()
    return any(ord(c) not in _outfit_cmap for c in str(s) if not c.isspace())


def text_width(s, size):
    ensure_fonts()
    font = HANGUL_FONT if needs_hangul_font(s) else LATIN_FONT
    return pdfmetrics.stringWidth(str(s), font, size)


def fmt_mm(v, decimals=3):
    """40.0 -> '40', 2.5 -> '2.5' (at most `decimals` decimals, trailing zeros stripped).

    Values the model states keep up to 3 decimals; sizes measured from the
    geometry use decimals=1 (0.1 mm).
    """
    s = f"{float(v):.{decimals}f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


# ---- raw SVG -----------------------------------------------------------------

def text(x, y, value, size, anchor="start", rot=0, extra=""):
    t = (f'<text x="{x:.3f}" y="{y:.3f}" font-size="{size:g}" text-anchor="{anchor}"{extra}>'
         f'{escape(str(value))}</text>')
    return f'<g transform="rotate({rot:g} {x:.3f} {y:.3f})">{t}</g>' if rot else t


def line(x1, y1, x2, y2, width=DIM_STROKE, color=THIN_INK, dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<line x1="{x1:.3f}" y1="{y1:.3f}" x2="{x2:.3f}" y2="{y2:.3f}" '
            f'stroke="{color}" stroke-width="{width:g}"{d}/>')


def polyline(points, width, color=INK, dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    pts = " ".join(f"{x:.3f},{y:.3f}" for x, y in points)
    return (f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="{width:g}" '
            f'stroke-linecap="round" stroke-linejoin="round"{d}/>')


def arrow(tip, direction):
    """Filled arrowhead with its point at `tip`, pointing along `direction`."""
    dx, dy = direction
    n = math.hypot(dx, dy) or 1.0
    dx, dy = dx / n, dy / n
    bx, by = tip[0] - dx * ARROW_L, tip[1] - dy * ARROW_L
    px, py = -dy * ARROW_W / 2, dx * ARROW_W / 2
    pts = f"{tip[0]:.3f},{tip[1]:.3f} {bx + px:.3f},{by + py:.3f} {bx - px:.3f},{by - py:.3f}"
    return f'<polygon points="{pts}" fill="{THIN_INK}" stroke="none"/>'


# ---- dimension geometry --------------------------------------------------------

def _text_box(x, baseline, w, size, anchor):
    x0 = {"start": x, "middle": x - w / 2, "end": x - w}[anchor]
    return (x0, baseline - size * ASCENT, x0 + w, baseline + size * DESCENT)


def _union(boxes, pad=BOX_PAD):
    return (min(b[0] for b in boxes) - pad, min(b[1] for b in boxes) - pad,
            max(b[2] for b in boxes) + pad, max(b[3] for b in boxes) + pad)


def _seg_box(seg, half=ARROW_W / 2):
    (x1, y1), (x2, y2) = seg
    return (min(x1, x2) - half, min(y1, y2) - half, max(x1, x2) + half, max(y1, y2) + half)


def _geom(lines, ext, arrows, label, tx, ty, anchor, rot, tbox, size):
    return {"lines": lines, "ext": ext, "arrows": arrows, "guides": list(ext),
            "text": {"x": tx, "y": ty, "label": label, "anchor": anchor, "rot": rot, "size": size},
            "text_box": tbox, "bbox": _union([tbox, *(_seg_box(s) for s in lines)])}


def dim_h(x1, x2, y, fy1, fy2, label, size=DIM_TEXT):
    """Horizontal dimension: line at paper y, extension lines up/down to fy1/fy2."""
    (x1, fy1), (x2, fy2) = sorted([(x1, fy1), (x2, fy2)])
    ext = []
    for x, fy in ((x1, fy1), (x2, fy2)):
        s = 1 if y > fy else -1
        ext.append(((x, fy + s * EXT_GAP), (x, y + s * EXT_OVER)))
    w = text_width(label, size)
    baseline = y - TEXT_GAP
    if x2 - x1 >= w + 2 * ARROW_L + 2:
        lines = [((x1, y), (x2, y))]
        arrows = [((x1, y), (-1, 0)), ((x2, y), (1, 0))]
        tx, anchor = (x1 + x2) / 2, "middle"
    else:  # too short: arrows outside, value after the right arrow
        tx, anchor = x2 + ARROW_L + 1.5, "start"
        lines = [((x1 - ARROW_L - 1.5, y), (tx + w, y))]
        arrows = [((x1, y), (1, 0)), ((x2, y), (-1, 0))]
    tbox = _text_box(tx, baseline, w, size, anchor)
    return _geom(lines, ext, arrows, label, tx, baseline, anchor, 0, tbox, size)


def dim_v(y1, y2, x, fx1, fx2, label, size=DIM_TEXT):
    """Vertical dimension: line at paper x; value reads upward, left of the line."""
    (y1, fx1), (y2, fx2) = sorted([(y1, fx1), (y2, fx2)])
    ext = []
    for yy, fx in ((y1, fx1), (y2, fx2)):
        s = 1 if x > fx else -1
        ext.append(((fx + s * EXT_GAP, yy), (x + s * EXT_OVER, yy)))
    w = text_width(label, size)
    tx = x - TEXT_GAP
    if y2 - y1 >= w + 2 * ARROW_L + 2:
        lines = [((x, y1), (x, y2))]
        arrows = [((x, y1), (0, -1)), ((x, y2), (0, 1))]
        ty, anchor = (y1 + y2) / 2, "middle"
        tbox = (tx - size * ASCENT, ty - w / 2, tx + size * DESCENT, ty + w / 2)
    else:  # too short: arrows outside, value above the top arrow
        ty, anchor = y1 - ARROW_L - 1.5, "start"
        lines = [((x, ty - w), (x, y2 + ARROW_L + 1.5))]
        arrows = [((x, y1), (0, 1)), ((x, y2), (0, -1))]
        tbox = (tx - size * ASCENT, ty - w, tx + size * DESCENT, ty)
    return _geom(lines, ext, arrows, label, tx, ty, anchor, -90, tbox, size)


def radius_leader(px, py, ex, ey, label, size=DIM_TEXT):
    """Leader from an arrow at (px, py) on a circle/arc to an elbow and a shoulder.

    Used for both Ø (diameter) and R (radius) callouts.
    """
    sign = 1 if ex >= px else -1
    w = text_width(label, size)
    end = ex + sign * (w + 2)
    tx = ex + sign * 1
    anchor = "start" if sign > 0 else "end"
    baseline = ey - TEXT_GAP
    lines = [((px, py), (ex, ey)), ((ex, ey), (end, ey))]
    tbox = _text_box(tx, baseline, w, size, anchor)
    g = _geom(lines, [], [((px, py), (px - ex, py - ey))], label, tx, baseline, anchor, 0, tbox, size)
    g["bbox"] = _union([tbox, _seg_box(lines[1])])
    g["guides"] = [lines[0]]  # the slanted leader is collision-checked as a segment
    return g


def draw(g):
    """SVG for a geometry record built by dim_h, dim_v or radius_leader.

    Records hold lines/ext (drawn segments), arrows, text, text_box, bbox
    (dimension line + text) and guides (extension/leader segments that must
    not cross other text).
    """
    out = [line(*a, *b) for a, b in g["ext"] + g["lines"]]
    out += [arrow(tip, d) for tip, d in g["arrows"]]
    t = g["text"]
    out.append(text(t["x"], t["y"], t["label"], t["size"], t["anchor"], t["rot"]))
    return "".join(out)


# ---- notes area ------------------------------------------------------------------

def wrap(s, width, size):
    """Greedy wrap by measured width; words longer than a line are split by character."""
    lines, cur = [], ""
    for word in str(s).split(" "):
        cand = f"{cur} {word}" if cur else word
        if text_width(cand, size) <= width:
            cur = cand
            continue
        if cur:
            lines.append(cur)
        cur = ""
        for ch in word:
            if text_width(cur + ch, size) > width and cur:
                lines.append(cur)
                cur = ""
            cur += ch
    if cur or not lines:
        lines.append(cur)
    return lines


NOTE_LEAD = 1.45     # note line pitch, x font size
NOTE_GAP = 0.4       # extra space after each note, x font size
ROW_H = 1.9          # table row height for one line, x font size


def note_lines(rows, width, size, numbered=False):
    """Wrapped note rows as flowable lines: [(height, draw(x, top) -> svg, keep_with_next)].

    Each wrapped line is its own item so a long note can continue in the next
    column; the gap after a note belongs to its last line.
    """
    items, lead = [], size * NOTE_LEAD
    for i, row in enumerate(rows):
        prefix = f"{i + 1}. " if numbered else ""
        indent = text_width(prefix, size)
        chunks = wrap(row, width - indent, size)
        for j, chunk in enumerate(chunks):
            def draw(x, top, chunk=chunk, number=prefix.strip() if j == 0 else "", indent=indent):
                base = top + size
                out = text(x, base, number, size) if number else ""
                return out + text(x + indent, base, chunk, size)
            items.append((lead + (size * NOTE_GAP if j == len(chunks) - 1 else 0.0), draw, False))
    return items


def table_lines(title, header, rows, width, size):
    """Two-column table (name, value) under a title as flowable items.

    Long names wrap inside their row (the row grows) instead of being cut.
    """
    def draw_title(x, top):
        return text(x, top + 3 + size * 1.1, title, size * 1.1)

    items = [(3 + size * 2.0, draw_title, True)]  # True: keep with the next item
    for name, value in [header, *rows]:
        name_w = width - text_width(value, size) - 4
        names = wrap(name, name_w, size)
        h = size * ROW_H + (len(names) - 1) * size * NOTE_LEAD

        def draw(x, top, names=names, value=value, h=h):
            base = top + size * ROW_H * 0.68
            out = [line(x, top, x + width, top, 0.18), line(x, top + h, x + width, top + h, 0.18)]
            out += [text(x + 1, base + k * size * NOTE_LEAD, n, size) for k, n in enumerate(names)]
            out.append(text(x + width - 1, base, value, size, "end"))
            return "".join(out)
        items.append((h, draw, False))
    return items


def view_title(x, y, title, size=2.8):
    return text(x, y, title, size, "middle", extra=f' fill="{HIDDEN_INK}"')
