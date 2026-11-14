"""
conduit_skin_base.py -- what a Conduit theme ("skin") is, and the default one (Studio).

A skin owns the whole look of the app:
  - the qt_ui_kit Style (colours / corner radius of every button, list, dialog) and the UI font
  - how the canvas is drawn: background, cells, ports, links, overlays
  - its own motion: what a new link, a block, live audio or a closed device look like
  - wording: how box titles are written

The graph editor (conduit_graph.py) never draws anything itself; it asks the
current skin.  Geometry (where ports are, how big a cell is) is shared by all
skins so that dragging, hit-testing and saved layouts work the same everywhere.

Animation clock: the editor sets ``skin.t`` (seconds) before each frame and
keeps a registry of one-shot effects; a cell asks ``it.fx("block")`` and a link
gets ``L.age`` -- seconds since it happened, or None.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QFontDatabase, QFontMetricsF, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPolygonF)

import qt_ui_kit as kit
from qt_ui_kit.style import Style

# ---- shared geometry (scene units)
CELL_W = 220
HEADER_H = 26
ROW_H = 20
PORT_W = 42
PORT_H = 16
BOTH_EXTRA = 32      # how far the Both pill reaches past L / R, into the cell
BOTTOM_PAD = 8
NS_GAP = 34

FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
_fonts_loaded = False


def load_fonts():
    """Register the bundled fonts (assets/fonts) once per process."""
    global _fonts_loaded
    if _fonts_loaded:
        return
    _fonts_loaded = True
    if FONT_DIR.is_dir():
        for f in sorted(FONT_DIR.glob("*.ttf")):
            QFontDatabase.addApplicationFont(str(f))


def F(family, px, bold=False, italic=False, weight=None, spacing=None, caps=False) -> QFont:
    f = QFont()
    f.setFamilies([family] if isinstance(family, str) else list(family))
    f.setPixelSize(max(1, int(round(px))))
    if weight is not None:
        f.setWeight(QFont.Weight(weight))
    elif bold:
        f.setBold(True)
    f.setItalic(italic)
    if spacing:
        f.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
    if caps:
        f.setCapitalization(QFont.AllUppercase)
    return f


def alpha(c, a) -> QColor:
    c = QColor(c)
    c.setAlpha(int(max(0, min(255, a))))
    return c


def mix(a, b, t) -> QColor:
    a, b = QColor(a), QColor(b)
    return QColor(int(a.red() + (b.red() - a.red()) * t), int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t), int(a.alpha() + (b.alpha() - a.alpha()) * t))


def hseed(s: str) -> int:
    return int(hashlib.md5(str(s).encode()).hexdigest()[:8], 16)


class Rng:
    """Tiny deterministic generator (so hand-drawn wobble doesn't shimmer between frames)."""

    def __init__(self, seed):
        self.s = (int(seed) & 0xFFFFFFFF) or 1

    def __call__(self) -> float:
        self.s = (self.s * 1664525 + 1013904223) & 0xFFFFFFFF
        return self.s / 4294967296.0


def elide(text, font, width) -> str:
    fm = QFontMetricsF(font)
    if fm.horizontalAdvance(text) <= width:
        return text
    while len(text) > 1 and fm.horizontalAdvance(text + "…") > width:
        text = text[:-1]
    return text + "…"


def text(p: QPainter, rect, s, font, color, align=Qt.AlignLeft | Qt.AlignVCenter, fit=True):
    p.setFont(font)
    p.setPen(QColor(color))
    rect = QRectF(rect)
    p.drawText(rect, int(align) | int(Qt.TextSingleLine), elide(s, font, rect.width()) if fit else s)


def bezier(a: QPointF, b: QPointF, k=0.5, min_dx=40.0) -> QPainterPath:
    path = QPainterPath(a)
    dx = max(min_dx, abs(b.x() - a.x()) * k)
    path.cubicTo(QPointF(a.x() + dx, a.y()), QPointF(b.x() - dx, b.y()), b)
    return path


def ortho(a: QPointF, b: QPointF, snap=0.0, bias=0.5) -> QPainterPath:
    """Horizontal - vertical - horizontal."""
    mx = a.x() + (b.x() - a.x()) * bias
    if snap:
        mx = round(mx / snap) * snap
    if b.x() - a.x() < 24:          # target is behind the source: loop around
        mx = max(a.x(), b.x()) + 26
        path = QPainterPath(a)
        path.lineTo(mx, a.y())
        my = (a.y() + b.y()) / 2 if abs(a.y() - b.y()) > 60 else min(a.y(), b.y()) - 46
        path.lineTo(mx, my)
        path.lineTo(min(a.x(), b.x()) - 26, my)
        path.lineTo(min(a.x(), b.x()) - 26, b.y())
        path.lineTo(b)
        return path
    path = QPainterPath(a)
    path.lineTo(mx, a.y())
    path.lineTo(mx, b.y())
    path.lineTo(b)
    return path


def partial(path: QPainterPath, frac: float, steps=48) -> QPainterPath:
    """The first ``frac`` of a path (for draw-on animations)."""
    frac = max(0.0, min(1.0, frac))
    if frac >= 1.0:
        return path
    out = QPainterPath(path.pointAtPercent(0))
    n = max(2, int(steps * frac) + 1)
    for i in range(1, n + 1):
        out.lineTo(path.pointAtPercent(frac * i / n))
    return out


def sample(path: QPainterPath, n: int):
    """n+1 (point, unit normal) pairs along a path."""
    out = []
    for i in range(n + 1):
        u = i / n
        pt = path.pointAtPercent(u)
        ang = math.radians(path.angleAtPercent(u))
        out.append((pt, QPointF(math.sin(ang), math.cos(ang))))
    return out


def arrow_head(p: QPainter, path: QPainterPath, color, size=9.0, filled=True):
    end = path.pointAtPercent(1.0)
    ang = math.radians(path.angleAtPercent(0.999))
    dx, dy = math.cos(ang), -math.sin(ang)
    back = QPointF(end.x() - dx * size, end.y() - dy * size)
    nx, ny = -dy, dx
    poly = QPolygonF([end, QPointF(back.x() + nx * size * 0.55, back.y() + ny * size * 0.55),
                      QPointF(back.x() - nx * size * 0.55, back.y() - ny * size * 0.55)])
    if filled:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        p.drawPolygon(poly)
        p.setBrush(Qt.NoBrush)
    else:
        p.drawPolyline(QPolygonF([poly[1], poly[0], poly[2]]))


def stroke(p: QPainter, path, color, width, style=Qt.SolidLine, cap=Qt.RoundCap, join=Qt.RoundJoin, dash=None):
    pen = QPen(QColor(color), width, style, cap, join)
    if dash:
        pen.setDashPattern(dash)
    p.strokePath(path, pen)


def ease_out_back(t: float, s=1.70158) -> float:
    t = max(0.0, min(1.0, t)) - 1
    return t * t * ((s + 1) * t + s) + 1


def port_label(ch: str) -> str:
    return {"*": "Both", "FL": "L", "FR": "R", "MONO": "M"}.get(ch, ch)


class LinkLook:
    """Everything a skin may want to know about one link while painting it."""
    __slots__ = ("key", "selected", "dashed", "label", "live", "blocked", "signal", "kind", "age",
                 "src_tint", "dst_tint", "width", "seed")

    def __init__(self, key="", **kw):
        self.key = key
        self.selected = False
        self.dashed = False       # ghost / internal / the link being dragged
        self.label = ""           # volume text
        self.live = True          # linked in PipeWire right now
        self.blocked = False
        self.signal = False       # audio is (probably) flowing
        self.kind = "direct"      # in | out | bypass | direct | ghost | internal | drag
        self.age = None           # seconds since the link was created, while its intro runs
        self.src_tint = None
        self.dst_tint = None
        self.width = None
        self.seed = hseed(key)
        for k, v in kw.items():
            setattr(self, k, v)


DEFAULT_CANVAS = {
    "link_in": "#3b82f6",       # into a virtual device
    "link_out": "#f97316",      # out of a virtual device
    "link_bypass": "#22c55e",   # app straight to a real speaker
    "link_direct": "#facc15",   # any other cell-to-cell link
    "link_pending": "#6e6e6e",  # configured, not live
    "link_blocked": "#6b1010",  # very dark red
    "closed": "#a855f7",        # closed device
    "fg": "#ffffff",            # selection glow, ghost / internal links, overlay text
    "link_width": 2.2,
}


class Skin:
    """The default look (Studio): black and white, coloured cables.  Other skins subclass this."""
    key = "studio"
    name = "Studio"
    blurb = "Pure black and white, coloured cables. No motion."
    animated = False             # needs frames while audio is flowing
    bg_animated = False          # paint_background changes over time (refreshed ~4x a second)
    anchor_inset = 0.0           # how far inside the port's outer edge links attach
    ui_font = None               # (family, point size) for the app's widgets; None = desktop default
    title_font = None            # QFont for box titles; None = bold UI font
    has_desk = False
    margin = 22                  # how far cell decorations may reach outside the body
    canvas = dict(DEFAULT_CANVAS)
    style_args: dict = {}
    # light skins can offer a dark variant: these override style_args / canvas / instance attributes
    light = False
    dark_style_args: dict = {}
    dark_canvas: dict = {}
    DARK: dict = {}

    def __init__(self, dark=False):
        self.t = 0.0
        self._geo = {}
        self.dark = bool(dark and self.light)
        self.canvas = {**DEFAULT_CANVAS, **type(self).canvas, **(type(self).dark_canvas if self.dark else {})}
        if self.dark:
            for k, v in type(self).DARK.items():
                setattr(self, k, v)

    def cached(self, key, path: QPainterPath, tag: str, make):
        """Per-link geometry that is costly to derive from a path (samples, wobble, dot positions),
        kept until the link's end points move."""
        a, b = path.pointAtPercent(0), path.pointAtPercent(1)
        k = (key, tag, round(a.x()), round(a.y()), round(b.x()), round(b.y()))
        hit = self._geo.get(k)
        if hit is None:
            if len(self._geo) > 800:
                self._geo.clear()
            hit = self._geo[k] = make()
        return hit

    # ---------------------------------------------------------------- chrome
    def style(self) -> Style:
        s = Style(**{**self.style_args, **(self.dark_style_args if self.dark else {})})
        s.extra = {"skin": self.key, "dark": self.dark}
        return s

    def box_title(self, label: str, index: int) -> str:
        return label

    def col(self, name) -> QColor:
        return QColor(self.canvas[name])

    def link_color(self, kind: str) -> QColor:
        return self.col({"in": "link_in", "out": "link_out", "bypass": "link_bypass"}.get(kind, "link_direct"))

    def tint(self, label: str) -> QColor:
        return QColor.fromHsv(hseed(label) % 360, 150, 235)

    # ---------------------------------------------------------------- background / overlay
    def paint_background(self, p: QPainter, rect: QRectF, view):
        th = kit.Theme()
        p.fillRect(rect, th.window())
        self.dot_grid(p, rect, alpha(self.col("fg"), 14), 40)

    def paint_foreground(self, p: QPainter, w: int, h: int, view):
        """Viewport coordinates, drawn over everything in a box."""

    def dot_grid(self, p, rect, color, step, size=1.6):
        if (rect.width() / step) * (rect.height() / step) > 20000:
            return
        p.setPen(QPen(color, size))
        x0 = math.floor(rect.left() / step) * step
        y0 = math.floor(rect.top() / step) * step
        pts = []
        x = x0
        while x < rect.right():
            y = y0
            while y < rect.bottom():
                pts.append(QPointF(x, y))
                y += step
            x += step
        p.drawPoints(pts)

    def line_grid(self, p, rect, color, step, width=1.0):
        if (rect.width() / step) + (rect.height() / step) > 1500:
            return
        p.setPen(QPen(color, width))
        lines = []
        x = math.floor(rect.left() / step) * step
        while x < rect.right():
            lines.append(QLineF(x, rect.top(), x, rect.bottom()))
            x += step
        y = math.floor(rect.top() / step) * step
        while y < rect.bottom():
            lines.append(QLineF(rect.left(), y, rect.right(), y))
            y += step
        p.drawLines(lines)

    # ---------------------------------------------------------------- cells
    def paint_cell(self, p: QPainter, it):
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.save()
        p.setOpacity(self.cell_opacity(it))
        if it.is_ns:
            self.paint_ns_connector(p, it)
        self.paint_body(p, it)
        self.paint_ports(p, it)
        self.paint_also(p, it)
        p.restore()
        self.paint_top_label(p, it)

    def cell_opacity(self, it) -> float:
        if it.ghost:
            return 0.5
        if it.status in ("closed", "inactive") or it.editor.outcome_box == it.box:
            return 0.55
        return 1.0

    def body(self, it) -> QRectF:
        return QRectF(0, 0, CELL_W, it.height())

    def mid(self, it) -> QRectF:
        """The free area between the two port columns."""
        def edge(chans):
            if not chans:
                return 8.0
            return PORT_W - 2.0 + (BOTH_EXTRA if len(chans) >= 2 else 0)
        left, right = edge(it.ins), CELL_W - edge(it.outs)
        return QRectF(left, HEADER_H, right - left, it.height() - HEADER_H - BOTTOM_PAD)

    def badges(self, it, speaker="SPK", auto="^", event="E") -> str:
        out = []
        if it.speaker:
            out.append(speaker)
        if it.auto_detect:
            out.append(auto)
        if it.events:
            out.append(f"{event}{it.events}")
        return " ".join(out)

    def status_word(self, it, live="live", idle="idle", blocked="blocked", closed="closed") -> str:
        if it.status == "blocked":
            return blocked
        if it.status == "closed":
            return closed
        return live if it.signal else idle

    def paint_ns_connector(self, p, it):
        p.setPen(QPen(alpha(self.col("fg"), 90), 2, Qt.DashLine))
        p.drawLine(QPointF(CELL_W / 2, -NS_GAP + 2), QPointF(CELL_W / 2, -2))

    def paint_body(self, p, it):
        th = kit.Theme()
        body = self.body(it)
        radius = min(th.corner_radius, 10)
        fill = QColor(th.surface())
        head = QColor(it.color) if it.color else QColor(th.accent())
        border = QColor(th.border())
        blocked_c, closed_c = self.col("link_blocked"), self.col("closed")
        if it.hub:
            border = QColor(th.outline())
            head = QColor(it.color) if it.color else QColor(th.secondary())
        if it.status == "blocked":
            fill, head, border = blocked_c.darker(160), blocked_c, blocked_c.lighter(150)
        elif it.status == "closed":
            border = closed_c
            head = QColor(closed_c).darker(220) if not it.color else head
        if it.editor.outcome_box == it.box:
            fill, head, border = QColor("#1c1c1c"), QColor("#2a2a2a"), QColor("#3a3a3a")
        path = kit.rounded_rect_path(body, radius)
        p.fillPath(path, fill)
        hp = kit.rounded_rect_path(QRectF(0, 0, CELL_W, HEADER_H), radius, bottom_left=False, bottom_right=False)
        p.fillPath(hp, head)
        pen = QPen(border, 1.6 if it.hub else 1.2)
        if it.ghost:
            pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        p.drawPath(path)
        if it.selected:
            p.setPen(QPen(self.col("fg"), 2))
            p.drawPath(path)
        f = QFont(p.font())
        f.setBold(True)
        f.setPixelSize(12)
        badge = self.badges(it)
        bw = QFontMetricsF(f).horizontalAdvance(badge) + 8 if badge else 0
        title_col = kit.contrast_text(head)
        if it.status == "closed" and not it.color:
            title_col = closed_c.lighter(140)
        text(p, QRectF(8, 0, CELL_W - 16 - bw, HEADER_H), it.title, f, title_col)
        if badge:
            text(p, QRectF(CELL_W - bw - 6, 0, bw, HEADER_H), badge, f, title_col, Qt.AlignRight | Qt.AlignVCenter)

    def paint_ports(self, p, it):
        for side in ("in", "out"):
            both = it.port_rect(side, "*")
            if both is not None:
                self.paint_both(p, it, side, both, it.hover_port == (side, "*"))
            for ch in it.chans(side):
                self.paint_port(p, it, side, ch, it.port_rect(side, ch), it.hover_port == (side, ch))
        p.setBrush(Qt.NoBrush)

    def both_tab(self, side, rect: QRectF) -> QRectF:
        """The part of the Both port that shows past L / R."""
        if side == "in":
            return QRectF(rect.right() - BOTH_EXTRA, rect.top(), BOTH_EXTRA, rect.height())
        return QRectF(rect.left(), rect.top(), BOTH_EXTRA, rect.height())

    def paint_both(self, p, it, side, rect, hovered):
        th = kit.Theme()
        fill = QColor(th.secondary()).lighter(135 if hovered else 100)
        p.setPen(QPen(QColor(th.border()).lighter(130), 1))
        p.setBrush(fill)
        p.drawRoundedRect(rect, 6, 6)
        f = QFont(p.font())
        f.setPixelSize(10)
        text(p, self.both_tab(side, rect), "Both", f, kit.contrast_text(fill), Qt.AlignCenter)

    def paint_port(self, p, it, side, ch, rect, hovered):
        th = kit.Theme()
        fill = QColor(th.accent()).lighter(170 if hovered else 125)
        p.setPen(QPen(QColor(th.border()).lighter(130), 1))
        p.setBrush(fill)
        p.drawRoundedRect(rect, 5, 5)
        f = QFont(p.font())
        f.setPixelSize(10)
        text(p, rect, port_label(ch), f, kit.contrast_text(fill), Qt.AlignCenter)

    def small_font(self) -> QFont:
        f = QFont()
        f.setPixelSize(10)
        return f

    def paint_also(self, p, it):
        if not it.also:
            return
        f = self.small_font()
        f.setItalic(True)
        text(p, QRectF(8, it.height() - 16, CELL_W - 16, 14), it.also, f, alpha(kit.Theme().text(), 130))

    def top_text(self, it):
        """(text, colour) drawn above a cell: the outcome badge in outcome mode, else its volume."""
        if it.editor.outcome_box == it.box and not it.hub:
            if it.outcome == "block":
                return "BLOCK", QColor("#ef4444")
            if it.outcome == "unblock":
                return "UNBLOCK", QColor("#22c55e")
            return "middle-click", alpha(kit.Theme().text(), 110)
        if it.volume_text:
            return it.volume_text, QColor(kit.Theme().text())
        return None, None

    def paint_top_label(self, p, it):
        s, col = self.top_text(it)
        if s:
            f = self.small_font()
            f.setBold(True)
            text(p, QRectF(0, -18, CELL_W, 16), s, f, col, Qt.AlignCenter)

    # ---------------------------------------------------------------- links
    def link_path(self, a: QPointF, b: QPointF, key: str = "") -> QPainterPath:
        return bezier(a, b)

    def link_state_color(self, color: QColor, L: LinkLook) -> QColor:
        return QColor(color)

    def paint_link(self, p: QPainter, path: QPainterPath, color: QColor, L: LinkLook):
        p.setRenderHint(QPainter.Antialiasing)
        width = L.width or self.canvas["link_width"]
        if L.selected:
            stroke(p, path, alpha(self.col("fg"), 200), width + 4)
        if L.dashed:
            stroke(p, path, color, width, Qt.DashLine)
            return
        if L.label:
            self.stroke_with_label(p, path, color, width, L.label, self.small_font())
        else:
            stroke(p, path, color, width)

    def stroke_with_label(self, p, path, color, width, label, font, text_color=None):
        """A gap in the line with the value filling it."""
        mid = path.pointAtPercent(0.5)
        fm = QFontMetricsF(font)
        w, h = fm.horizontalAdvance(label) + 10, fm.height() + 2
        gap = QRectF(mid.x() - w / 2, mid.y() - h / 2, w, h)
        clip = QPainterPath()
        clip.addRect(path.boundingRect().adjusted(-40, -40, 40, 40))
        hole = QPainterPath()
        hole.addRoundedRect(gap, 4, 4)
        p.save()
        p.setClipPath(clip.subtracted(hole), Qt.IntersectClip)
        stroke(p, path, color, width)
        p.restore()
        text(p, gap, label, font, text_color or QColor(color).lighter(130), Qt.AlignCenter, fit=False)

    # ---------------------------------------------------------------- misc
    def eq_colors(self) -> dict:
        """Colours of the equaliser graph."""
        th = kit.Theme()
        return {"bg": th.base(), "grid": alpha(th.text(), 28), "text": alpha(th.text(), 150),
                "curve": self.col("link_out"), "fill": alpha(self.col("link_out"), 46),
                "spectrum": alpha(self.col("link_in"), 90), "handle": QColor(th.text()),
                "handle_sel": self.col("link_bypass"), "band": alpha(th.text(), 60)}
