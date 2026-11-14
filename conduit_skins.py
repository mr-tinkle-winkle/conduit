"""
conduit_skins.py -- Conduit's ten themes (plus the plain Studio look in conduit_skin_base).

  rack       Patch Rack     engraved modules, jack sockets, cables that hang and swing
  scope      Oscilloscope   phosphor CRT; links are live traces
  metro      Transit Map    45-degree lines, stations, trains
  schematic  Schematic      circuit symbols, orthogonal nets, DNP stamps
  tui        Terminal       box drawing, monospace, F-keys
  glass      Liquid Glass   frosted cards over a drifting aurora
  swiss      Swiss Poster   black, white, one red; nothing eases
  desk       Mixing Desk    channel strips (see conduit_desk.py) + a hardware-style patch view
  sketch     Notebook       sticky notes and pen arrows on lined paper
  arcade     Arcade         8-bit sprites, dotted chase-light links, a score HUD

Each skin draws cells / ports / links its own way and has its own motion for:
a new link (L.age), a block (it.fx("block")), live audio (it.signal / L.signal)
and a closed device (it.status == "closed").
"""
from __future__ import annotations

import math

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPen,
                           QPixmap, QPolygonF, QRadialGradient)

from conduit_skin_base import (BOTH_EXTRA, CELL_W, HEADER_H, NS_GAP, PORT_H, PORT_W, ROW_H, F, LinkLook, Rng, Skin,
                               alpha, arrow_head, bezier, ease_out_back, elide, hseed, load_fonts, mix, ortho,
                               partial, port_label, sample, stroke, text)

C, V, L_ = Qt.AlignCenter, Qt.AlignVCenter, Qt.AlignLeft
R_ = Qt.AlignRight


def _hash2(x: int, y: int) -> int:
    return ((x * 73856093) ^ (y * 19349663)) & 0xFFFF


def _tag(p, path, label, font, bg, fg, radius=3.0, border=None):
    mid = path.pointAtPercent(0.5)
    w = QFontMetricsF(font).horizontalAdvance(label) + 10
    h = QFontMetricsF(font).height() + 2
    r = QRectF(mid.x() - w / 2, mid.y() - h / 2, w, h)
    p.setPen(QPen(QColor(border), 1) if border else Qt.NoPen)
    p.setBrush(QColor(bg))
    p.drawRoundedRect(r, radius, radius)
    p.setBrush(Qt.NoBrush)
    text(p, r, label, font, fg, C, fit=False)


# =====================================================================================
# 1 · PATCH RACK
# =====================================================================================

class RackSkin(Skin):
    key, name = "rack", "Patch Rack"
    blurb = "A 19-inch rack: engraved modules, jack sockets, cables that hang and swing."
    animated = True
    anchor_inset = 12.0
    ui_font = ("Oswald", 10)
    style_args = dict(window="#17181b", base="#1d1e22", surface="#2b2d31", accent="#3a3c41", secondary="#5a5d64",
                      border="#55585f", outline="#e7dfcc", text="#e7dfcc", corner_radius=3)
    canvas = {"link_in": "#c63a2b", "link_out": "#e3ad2f", "link_bypass": "#2c74c4", "link_direct": "#3f9a5c",
              "link_pending": "#5a5650", "link_blocked": "#4a1512", "closed": "#7a7468", "fg": "#f2d38a",
              "link_width": 6.0}
    MONO = "Share Tech Mono"

    def __init__(self):
        super().__init__()
        self.title_font = F("Oswald", 17, weight=600, spacing=2.5, caps=True)
        self._tex = None

    def box_title(self, label, index):
        return f"RACK {chr(65 + index % 26)} · {label}"

    def paint_background(self, p, rect, view):
        p.fillRect(rect, QColor("#17181b"))
        if self._tex is None:
            self._tex = QPixmap(4, 4)
            self._tex.fill(Qt.transparent)
            tp = QPainter(self._tex)
            tp.fillRect(0, 0, 4, 1, QColor(255, 255, 255, 7))
            tp.end()
        p.fillRect(rect, QBrush(self._tex))

    def _screw(self, p, x, y):
        p.setPen(QPen(QColor("#2a2b2f"), 1))
        p.setBrush(QColor("#8d8f94"))
        p.drawEllipse(QPointF(x, y), 3.2, 3.2)
        p.setPen(QPen(QColor("#3a3b40"), 1.2))
        p.drawLine(QPointF(x - 2, y - 1.4), QPointF(x + 2, y + 1.4))

    def paint_body(self, p, it):
        b = self.body(it)
        closed = it.status == "closed"
        g = QLinearGradient(0, 0, 0, b.height())
        top, bot = ("#4d483c", "#2f2c25") if it.hub else ("#45474d", "#2b2d31")
        if closed:
            top, bot = "#2a2b2e", "#1c1d20"
        g.setColorAt(0, QColor(top))
        g.setColorAt(1, QColor(bot))
        pen = QPen(QColor("#0b0b0c"), 2)
        if it.ghost:
            pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        p.setBrush(g)
        p.drawRoundedRect(b, 3, 3)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 26), 1))
        p.drawLine(QPointF(3, 1.5), QPointF(CELL_W - 3, 1.5))
        if it.selected:
            p.setPen(QPen(self.col("fg"), 2))
            p.drawRoundedRect(b.adjusted(-2, -2, 2, 2), 4, 4)
        for x, y in ((6, 6), (CELL_W - 6, 6), (6, b.height() - 6), (CELL_W - 6, b.height() - 6)):
            self._screw(p, x, y)
        tf = F("Oswald", 12, weight=500, spacing=1.4, caps=True)
        ink = "#7a7468" if closed else "#e7dfcc"
        tr = QRectF(15, 1, CELL_W - 54, HEADER_H - 2)
        text(p, tr.translated(1, 1), it.title, tf, "#0d0d0e")
        text(p, tr, it.title, tf, ink)
        if it.color:
            p.fillRect(QRectF(15, HEADER_H - 3.5, CELL_W - 54, 2.2), QColor(it.color))
        badge = self.badges(it)
        if badge:
            text(p, QRectF(CELL_W - 74, 2, 44, HEADER_H - 4), badge, F(self.MONO, 9), "#9a9384", R_ | V)
        # status LED
        led = QColor("#2a1b1b")
        if it.status == "blocked":
            age = it.fx("block")
            led = QColor("#ff3b2f") if age is None or int(age * 10) % 2 == 0 else QColor("#3a1010")
        elif it.signal:
            led = QColor("#5dff7a") if int(self.t * 6) % 2 == 0 else QColor("#1f6a2b")
        elif not closed:
            led = QColor("#1f6a2b")
        p.setPen(QPen(QColor("#0b0b0c"), 1))
        p.setBrush(led)
        p.drawEllipse(QPointF(CELL_W - 20, HEADER_H / 2 + 1), 4, 4)
        p.setBrush(Qt.NoBrush)
        m = self.mid(it)
        if it.is_ns:
            text(p, m, "DENOISE", F(self.MONO, 10), "#9a9384", C)
        elif it.hub:
            self._vu(p, m, it)
        elif not it.ghost:
            self._toggle(p, m, it)

    def _vu(self, p, m, it):
        w, h = min(60.0, m.width() - 6), max(22.0, min(34.0, m.height() - 2))
        r = QRectF(m.center().x() - w / 2, m.center().y() - h / 2, w, h)
        p.setPen(QPen(QColor("#0b0b0c"), 1))
        p.setBrush(QColor("#efe6c8"))
        p.drawRoundedRect(r, 2, 2)
        p.setBrush(Qt.NoBrush)
        pivot = QPointF(r.center().x(), r.bottom() - 3)
        rad = h - 7
        p.setPen(QPen(QColor("#7a6f55"), 1))
        p.drawArc(QRectF(pivot.x() - rad, pivot.y() - rad, 2 * rad, 2 * rad), 40 * 16, 100 * 16)
        p.setPen(QPen(QColor("#c63a2b"), 2))
        p.drawArc(QRectF(pivot.x() - rad, pivot.y() - rad, 2 * rad, 2 * rad), 40 * 16, 22 * 16)
        ang = -42.0
        if it.signal:
            t = self.t
            ang = 2 + 18 * math.sin(t * 5.1) + 9 * math.sin(t * 11.7 + 1) + 5 * math.sin(t * 23.0)
        a = math.radians(ang)
        p.setPen(QPen(QColor("#1b1b1b"), 1.5))
        p.drawLine(pivot, QPointF(pivot.x() + math.sin(a) * rad, pivot.y() - math.cos(a) * rad))

    def _toggle(self, p, m, it):
        cx, cy = m.center().x(), m.center().y() + 5
        blocked = it.status == "blocked"
        target = 32.0 if blocked else -32.0
        age = it.fx("block") if blocked else it.fx("unblock")
        if age is not None and age < 0.45:
            target = -target + 2 * target * ease_out_back(age / 0.45)
        p.setPen(QPen(QColor("#6d6f74"), 1))
        p.setBrush(QColor("#18191b"))
        p.drawRoundedRect(QRectF(cx - 12, cy - 6, 24, 12), 3, 3)
        a = math.radians(target)
        p.setPen(QPen(QColor("#d6d8dc"), 4.5, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(cx, cy), QPointF(cx + math.sin(a) * 15, cy - math.cos(a) * 15))
        f = F(self.MONO, 8)
        text(p, QRectF(cx - 40, cy - 6, 24, 12), "ON", f, "#9a9384" if blocked else "#5dff7a", R_ | V)
        text(p, QRectF(cx + 16, cy - 6, 26, 12), "OFF", f, "#ff6b5a" if blocked else "#9a9384")

    def paint_port(self, p, it, side, ch, rect, hovered):
        cx = rect.left() + 12 if side == "in" else rect.right() - 12
        cy = rect.center().y()
        p.setPen(QPen(QColor("#f2d38a" if hovered else "#8a8c91"), 2.4))
        p.setBrush(QColor("#141416"))
        p.drawEllipse(QPointF(cx, cy), 6.6, 6.6)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#000000"))
        p.drawEllipse(QPointF(cx, cy), 2.8, 2.8)
        lr = QRectF(cx + 9, cy - 7, 16, 14) if side == "in" else QRectF(cx - 25, cy - 7, 16, 14)
        text(p, lr, port_label(ch), F(self.MONO, 9), "#bdb6a6", C)

    def paint_both(self, p, it, side, rect, hovered):
        p.setPen(QPen(QColor("#f2d38a" if hovered else "#0b0b0c"), 1.2))
        p.setBrush(QColor("#1b1c1f"))
        p.drawRoundedRect(rect, 4, 4)
        text(p, self.both_tab(side, rect), "BOTH", F(self.MONO, 8), "#f2d38a" if hovered else "#9a9384", C)

    @staticmethod
    def _cable(a, b, sag):
        path = QPainterPath(a)
        path.cubicTo(QPointF(a.x() + 60, a.y() + sag), QPointF(b.x() - 60, b.y() + sag), b)
        return path

    @staticmethod
    def _sag(a, b):
        return 46 + abs(b.y() - a.y()) * 0.15 + abs(b.x() - a.x()) * 0.06

    def link_path(self, a, b, key=""):
        return self._cable(a, b, self._sag(a, b))

    def paint_link(self, p, path, color, L):
        p.setRenderHint(QPainter.Antialiasing)
        if L.dashed:
            stroke(p, path, color, 2, Qt.DashLine)
            return
        a, b = path.pointAtPercent(0), path.pointAtPercent(1)
        if L.age is not None and L.age < 1.6:      # dropped in from above: swings until it settles
            s0 = self._sag(a, b)
            path = self._cable(a, b, s0 - (s0 + 80) * math.exp(-4.2 * L.age) * math.cos(11 * L.age))
        w = L.width or 6.0
        if L.selected:
            stroke(p, path, alpha(self.col("fg"), 190), w + 8)
        stroke(p, path.translated(2, 5), QColor(0, 0, 0, 80), w + 2)
        stroke(p, path, "#0b0b0b", w + 3)
        stroke(p, path, color, w)
        stroke(p, path.translated(0, -1.6), QColor(255, 255, 255, 50), 1.5)
        for pt in (a, b):
            p.setPen(QPen(QColor("#d9d9d9"), 2))
            p.setBrush(QColor(color))
            p.drawEllipse(pt, 7.0, 7.0)
        p.setBrush(Qt.NoBrush)
        if L.label:
            _tag(p, path, L.label, F(self.MONO, 10), "#efe6c8", "#1b1b1b", 2, "#0b0b0c")

    def eq_colors(self):
        return {"bg": QColor("#101114"), "grid": QColor(231, 223, 204, 30), "text": QColor("#9a9384"),
                "curve": QColor("#e3ad2f"), "fill": QColor(227, 173, 47, 40), "spectrum": QColor(93, 255, 122, 70),
                "handle": QColor("#efe6c8"), "handle_sel": QColor("#c63a2b"), "band": QColor(231, 223, 204, 60)}


# =====================================================================================
# 2 · OSCILLOSCOPE
# =====================================================================================

class ScopeSkin(Skin):
    key, name = "scope", "Oscilloscope"
    blurb = "Phosphor CRT: a graticule, glowing traces that wave with the audio."
    animated = True
    ui_font = ("VT323", 13)
    style_args = dict(window="#020b06", base="#03100a", surface="#06180f", accent="#0b2a19", secondary="#155a33",
                      border="#1d6b3f", outline="#39ff88", text="#b8ffd0", corner_radius=2)
    canvas = {"link_in": "#7dff9b", "link_out": "#c6ff5a", "link_bypass": "#5affe0", "link_direct": "#e9ff70",
              "link_pending": "#2f6a44", "link_blocked": "#8a3a30", "closed": "#3c7a55", "fg": "#d6ffe3",
              "link_width": 2.0}
    PH = "#8dffb0"

    def __init__(self):
        super().__init__()
        self.title_font = F("VT323", 24, spacing=1.5, caps=True)
        self._scan = None
        self._vig = {}

    def box_title(self, label, index):
        return f"CH {chr(65 + index % 26)} · {label}"

    def paint_background(self, p, rect, view):
        p.fillRect(rect, QColor("#020b06"))
        self.line_grid(p, rect, QColor(29, 90, 51, 110), 50, 1.0 / max(view.zoom, 0.3))

    def paint_foreground(self, p, w, h, view):
        ov = self._vig.get((w, h))
        if ov is None:                      # scanlines + vignette, rendered once per viewport size
            if len(self._vig) > 8:
                self._vig.clear()
            ov = QPixmap(max(1, w), max(1, h))
            ov.fill(Qt.transparent)
            op = QPainter(ov)
            scan = QPixmap(1, 3)
            scan.fill(Qt.transparent)
            sp = QPainter(scan)
            sp.fillRect(0, 0, 1, 1, QColor(0, 0, 0, 70))
            sp.end()
            op.fillRect(ov.rect(), QBrush(scan))
            g = QRadialGradient(w / 2, h / 2, max(w, h) * 0.72)
            g.setColorAt(0.55, QColor(0, 0, 0, 0))
            g.setColorAt(1.0, QColor(0, 0, 0, 150))
            op.fillRect(ov.rect(), g)
            op.end()
            self._vig[(w, h)] = ov
        p.drawPixmap(0, 0, ov)
        age = view.editor.fx_age(("boot", view.box))
        if age is not None and age < 0.7:              # power-on: a line that opens into the picture
            if age < 0.3:
                p.fillRect(QRectF(0, 0, w, h), QColor("#020b06"))
                lw = w * (age / 0.3)
                p.fillRect(QRectF((w - lw) / 2, h / 2 - 1, lw, 2), QColor("#d6ffe3"))
            else:
                k = (age - 0.3) / 0.4
                cover = h / 2 * (1 - k)
                p.fillRect(QRectF(0, 0, w, cover), QColor("#020b06"))
                p.fillRect(QRectF(0, h - cover, w, cover), QColor("#020b06"))
                p.fillRect(QRectF(0, cover - 1, w, 2), alpha("#d6ffe3", 255 * (1 - k)))
                p.fillRect(QRectF(0, h - cover - 1, w, 2), alpha("#d6ffe3", 255 * (1 - k)))

    def _glow_text(self, p, rect, s, font, color, align=L_ | V):
        halo = alpha(color, 46)
        for dx, dy in ((-1.2, 0), (1.2, 0), (0, -1.2), (0, 1.2)):
            text(p, QRectF(rect).translated(dx, dy), s, font, halo, align)
        text(p, rect, s, font, color, align)

    def cell_opacity(self, it):
        if it.status == "closed":
            age = it.fx("closed")
            return 0.3 if age is None else max(0.3, 1.0 - age / 2.0 * 0.7)     # phosphor afterglow
        return super().cell_opacity(it)

    def paint_body(self, p, it):
        b = self.body(it)
        col = QColor(it.color) if it.color else QColor(self.PH)
        if it.status == "blocked":
            col = QColor("#ff6b5a")
        age = it.fx("block")
        if age is not None and age < 0.5:
            col = mix("#ffffff", col, age / 0.5)
        p.fillRect(b, QColor(2, 11, 6, 215))
        a = 9.0
        x0, y0, x1, y1 = b.left(), b.top(), b.right(), b.bottom()
        br = QPainterPath()
        for (cx, cy, sx, sy) in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x1, y1, -1, -1), (x0, y1, 1, -1)):
            br.moveTo(cx, cy + sy * a)
            br.lineTo(cx, cy)
            br.lineTo(cx + sx * a, cy)
        stroke(p, br, alpha(col, 60), 5 if it.hub else 4)
        pen = QPen(col, 2 if it.hub else 1.5)
        if it.ghost:
            pen.setStyle(Qt.DotLine)
        p.strokePath(br, pen)
        if it.selected:
            p.setPen(QPen(self.col("fg"), 1, Qt.DashLine))
            p.drawRect(b.adjusted(-3, -3, 3, 3))
        tag = "NR" if it.is_ns else "Σ" if it.hub else "OUT" if it.speaker else f"CH{it.index + 1}"
        f = F("VT323", 19)
        badge = self.badges(it, "SPK", "^", "E")
        bw = QFontMetricsF(f).horizontalAdvance(badge) + 6 if badge else 0
        self._glow_text(p, QRectF(8, 1, CELL_W - 16 - bw, HEADER_H), f"{tag} {it.title.upper()}", f, col)
        if badge:
            text(p, QRectF(CELL_W - bw - 6, 1, bw, HEADER_H), badge, f, alpha(col, 170), R_ | V)
        m = self.mid(it)
        sf = F("VT323", 16)
        if it.status == "blocked":
            if int(self.t * 2) % 2 == 0:
                self._glow_text(p, m, "SIG LOST", sf, "#ff6b5a", C)
        elif it.status == "closed":
            text(p, m, "NO SIGNAL", sf, alpha(col, 160), C)
        elif it.signal:
            db = -12 + 4 * math.sin(self.t * 3.1 + it.index) + 2 * math.sin(self.t * 7.3)
            self._glow_text(p, m, f"▲ {db:+.0f} dB", sf, col, C)
        else:
            text(p, m, "- - -", sf, alpha(col, 120), C)

    def paint_port(self, p, it, side, ch, rect, hovered):
        r = QRectF(rect.left() + (2 if side == "in" else PORT_W - 24), rect.top(), 22, rect.height())
        col = QColor(self.PH)
        if hovered:
            p.fillRect(r, col)
        text(p, r, f"[{port_label(ch)}]", F("VT323", 15), "#020b06" if hovered else col, C, fit=False)

    def paint_both(self, p, it, side, rect, hovered):
        col = QColor(self.PH)
        p.setPen(QPen(alpha(col, 255 if hovered else 90), 1, Qt.DotLine))
        p.setBrush(alpha(col, 40) if hovered else Qt.NoBrush)
        p.drawRect(rect)
        p.setBrush(Qt.NoBrush)
        text(p, self.both_tab(side, rect), "BOTH", F("VT323", 13), alpha(col, 255 if hovered else 150), C)

    def paint_link(self, p, path, color, L):
        p.setRenderHint(QPainter.Antialiasing)
        if L.dashed or L.blocked or not L.live:
            stroke(p, path, color, 1.4, Qt.CustomDashLine, dash=[3, 5])
            if L.selected:
                stroke(p, path, alpha(self.col("fg"), 120), 5)
            return
        shown = path
        if L.signal:
            pts = self.cached(L.key, path, "wave", lambda: sample(path, 56))
            n = len(pts) - 1
            poly = QPolygonF()
            ph = self.t * 9 + (L.seed % 17)
            for i, (pt, nm) in enumerate(pts):
                u = i / n
                off = 6.0 * math.sin(math.pi * u) * (math.sin(u * 38 + ph) + 0.45 * math.sin(u * 91 - ph * 1.7))
                poly.append(QPointF(pt.x() + nm.x() * off, pt.y() + nm.y() * off))
            shown = QPainterPath()
            shown.addPolygon(poly)
        head = None
        if L.age is not None and L.age < 0.9:
            frac = 1 - (1 - L.age / 0.9) ** 3
            shown = partial(shown, frac, 72)
            head = shown.pointAtPercent(1.0)
        if L.selected:
            stroke(p, shown, alpha(self.col("fg"), 110), 9)
        stroke(p, shown, alpha(color, 70), 5.5, cap=Qt.FlatCap, join=Qt.BevelJoin)
        stroke(p, shown, color, 1.8, cap=Qt.FlatCap, join=Qt.BevelJoin)
        if head is not None:
            p.setPen(Qt.NoPen)
            p.setBrush(alpha("#eafff0", 120))
            p.drawEllipse(head, 8, 8)
            p.setBrush(QColor("#eafff0"))
            p.drawEllipse(head, 3.5, 3.5)
            p.setBrush(Qt.NoBrush)
        if L.label:
            _tag(p, path, L.label, F("VT323", 15), "#020b06", color, 0, color)

    def eq_colors(self):
        return {"bg": QColor("#020b06"), "grid": QColor(29, 90, 51, 150), "text": QColor("#8dffb0"),
                "curve": QColor("#d6ffe3"), "fill": QColor(125, 255, 155, 30), "spectrum": QColor(125, 255, 155, 70),
                "handle": QColor("#d6ffe3"), "handle_sel": QColor("#e9ff70"), "band": QColor(125, 255, 155, 70)}


# =====================================================================================
# 3 · TRANSIT MAP
# =====================================================================================

class MetroSkin(Skin):
    key, name = "metro", "Transit Map"
    blurb = "A subway map: 45-degree lines, stations, trains carrying the audio."
    animated = True
    anchor_inset = 4.0
    ui_font = ("Overpass", 10)
    style_args = dict(window="#f4f1e8", base="#ebe7db", surface="#ffffff", accent="#e2ddd0", secondary="#c9c3b3",
                      border="#16161a", outline="#16161a", text="#16161a", text_outline="#ffffff", corner_radius=6)
    canvas = {"link_in": "#1f5fbf", "link_out": "#ef8a17", "link_bypass": "#2e9a52", "link_direct": "#8b3fbf",
              "link_pending": "#b9b4a8", "link_blocked": "#9c988e", "closed": "#a39f95", "fg": "#16161a",
              "link_width": 7.0}
    INK = "#16161a"

    def __init__(self):
        super().__init__()
        self.title_font = F("Overpass", 17, weight=900, spacing=0.5)

    def box_title(self, label, index):
        return f"{label} District"

    def paint_background(self, p, rect, view):
        p.fillRect(rect, QColor("#f4f1e8"))
        # one river across every map, for orientation
        river = QPainterPath(QPointF(rect.left() - 20, 250))
        x = math.floor(rect.left() / 400) * 400 - 400
        river = QPainterPath(QPointF(x, 240 + 30 * math.sin(x / 260)))
        while x < rect.right() + 400:
            x += 100
            river.lineTo(x, 240 + 30 * math.sin(x / 260) + 18 * math.sin(x / 97))
        stroke(p, river, QColor("#cfe4ee"), 46)

    def paint_body(self, p, it):
        b = self.body(it)
        closed = it.status == "closed"
        ink = QColor("#a39f95" if closed else self.INK)
        fill = QColor(self.INK) if it.hub else QColor("#ffffff")
        if it.color:
            fill = QColor(it.color)
        pen = QPen(ink, 3)
        if it.ghost:
            pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        p.setBrush(fill)
        p.drawRoundedRect(b, 7, 7)
        p.setBrush(Qt.NoBrush)
        if it.selected:
            p.setPen(QPen(QColor("#ffd400"), 3))
            p.drawRoundedRect(b.adjusted(-4, -4, 4, 4), 10, 10)
        light = fill.lightness() < 130
        tcol = QColor("#ffffff") if light else ink
        tf = F("Overpass", 13, weight=800)
        tf.setStrikeOut(closed)
        badge = self.badges(it, "♪", "^", "E")
        bw = QFontMetricsF(tf).horizontalAdvance(badge) + 8 if badge else 0
        text(p, QRectF(10, 1, CELL_W - 20 - bw, HEADER_H), it.title, tf, tcol)
        if badge:
            text(p, QRectF(CELL_W - bw - 8, 1, bw, HEADER_H), badge, F("Overpass", 11, weight=800), tcol, R_ | V)
        m = self.mid(it)
        sf = F("Overpass", 9, weight=700, spacing=1.0)
        sub = alpha(tcol, 170)
        if it.hub:
            text(p, m, "NOISE FILTER" if it.is_ns else "INTERCHANGE", sf, sub, C)
        elif closed:
            text(p, m, "STATION CLOSED", sf, sub, C)
        elif it.status != "blocked":
            text(p, m, "TRAINS RUNNING" if it.signal else "NO SERVICE", sf, sub, C)
        if it.status == "blocked":
            on = int(self.t * 2.5) % 2 == 0
            r = QRectF(CELL_W / 2 - 78, b.height() + 5, 156, 18)
            p.setPen(QPen(QColor(self.INK), 1.5))
            p.setBrush(QColor("#ffd400") if on else QColor("#f2e9b0"))
            p.drawRoundedRect(r, 3, 3)
            p.setBrush(Qt.NoBrush)
            text(p, r, "⚠ SERVICE SUSPENDED", F("Overpass", 10, weight=900), self.INK, C)

    def paint_port(self, p, it, side, ch, rect, hovered):
        cx = rect.left() + 4 if side == "in" else rect.right() - 4
        cy = rect.center().y()
        p.setPen(QPen(QColor(self.INK), 3))
        p.setBrush(QColor("#ffd400" if hovered else "#ffffff"))
        p.drawEllipse(QPointF(cx, cy), 6.2, 6.2)
        p.setBrush(Qt.NoBrush)
        lr = QRectF(cx + 9, cy - 7, 14, 14) if side == "in" else QRectF(cx - 23, cy - 7, 14, 14)
        dark = it.hub and not it.color
        text(p, lr, port_label(ch), F("Overpass", 10, weight=800), "#ffffff" if dark else self.INK, C)

    def paint_both(self, p, it, side, rect, hovered):
        # an interchange capsule around the L and R stations, with a tab to grab
        p.setPen(QPen(QColor(self.INK), 2))
        p.setBrush(QColor("#ffd400" if hovered else "#f4f1e8"))
        p.drawRoundedRect(rect, 9, 9)
        p.setBrush(Qt.NoBrush)
        text(p, self.both_tab(side, rect), "BOTH", F("Overpass", 8, weight=900), self.INK, C)

    def link_path(self, a, b, key=""):
        dx, ady = b.x() - a.x(), abs(b.y() - a.y())
        if dx < ady + 36:
            return ortho(a, b)
        mx = a.x() + dx / 2
        path = QPainterPath(a)
        path.lineTo(mx - ady / 2, a.y())
        path.lineTo(mx + ady / 2, b.y())
        path.lineTo(b)
        return path

    def paint_link(self, p, path, color, L):
        p.setRenderHint(QPainter.Antialiasing)
        w = L.width or 7.0
        if L.dashed:
            stroke(p, path, color, 3, Qt.CustomDashLine, dash=[2, 2.5])
            return
        shown = path
        if L.age is not None and L.age < 1.1:
            shown = partial(path, 1 - (1 - L.age / 1.1) ** 2, 80)
        if L.selected:
            stroke(p, shown, QColor("#ffd400"), w + 7)
        if L.blocked or not L.live:
            stroke(p, shown, color, w - 1, Qt.CustomDashLine, Qt.FlatCap, dash=[2.2, 1.6])
        else:
            stroke(p, shown, color, w)
        if L.signal and shown is path:
            for k in range(2):
                u = (self.t * 0.14 + (L.seed % 100) / 100.0 + k * 0.5) % 1.0
                pt = path.pointAtPercent(u)
                p.save()
                p.translate(pt)
                p.rotate(-path.angleAtPercent(u))
                p.setPen(QPen(QColor(color), 2.4))
                p.setBrush(QColor("#ffffff"))
                p.drawRoundedRect(QRectF(-9, -4.5, 18, 9), 3.5, 3.5)
                p.restore()
            p.setBrush(Qt.NoBrush)
        if L.age is not None and L.age < 2.4 and L.age > 0.2:
            mid = path.pointAtPercent(0.5)
            a = 255 if L.age < 1.9 else 255 * (2.4 - L.age) / 0.5
            r = QRectF(mid.x() - 52, mid.y() - 30, 104, 20)
            p.setPen(Qt.NoPen)
            p.setBrush(alpha(self.INK, a))
            p.drawRoundedRect(r, 3, 3)
            p.setBrush(Qt.NoBrush)
            text(p, r, "NEW SERVICE", F("Overpass", 10, weight=900), alpha("#ffd400", a), C)
        if L.label:
            _tag(p, path, L.label, F("Overpass", 10, weight=800), "#ffffff", self.INK, 8, self.INK)


# =====================================================================================
# 4 · SCHEMATIC
# =====================================================================================

class SchematicSkin(Skin):
    key, name = "schematic", "Schematic"
    blurb = "An engineering drawing: components, orthogonal nets, DNP stamps."
    animated = True
    ui_font = ("IBM Plex Mono", 9)
    style_args = dict(window="#0f3a68", base="#0c3059", surface="#134678", accent="#1a5597", secondary="#2f6db3",
                      border="#7fa7d8", outline="#eaf2ff", text="#eaf2ff", corner_radius=0)
    canvas = {"link_in": "#eaf2ff", "link_out": "#ffd166", "link_bypass": "#9ef0c4", "link_direct": "#ffb3d1",
              "link_pending": "#5f8cc0", "link_blocked": "#ff8a8a", "closed": "#8fb0d8", "fg": "#ffffff",
              "link_width": 1.8}
    INK = "#eaf2ff"
    BG = "#0f3a68"
    MONO = "IBM Plex Mono"

    def __init__(self):
        super().__init__()
        self.title_font = F(self.MONO, 14, weight=600, spacing=1.0, caps=True)
        self._tile = None

    def box_title(self, label, index):
        return f"SHEET {index + 1} · {label}"

    def paint_background(self, p, rect, view):
        if self._tile is None:              # 50 px drafting grid with 10 px subdivisions, as one tile
            self._tile = QPixmap(100, 100)
            self._tile.fill(QColor(self.BG))
            tp = QPainter(self._tile)
            for i in range(0, 100, 10):
                c = QColor(255, 255, 255, 30 if i % 50 == 0 else 12)
                tp.fillRect(i, 0, 1, 100, c)
                tp.fillRect(0, i, 100, 1, c)
            tp.end()
        if view.zoom < 0.45:
            p.fillRect(rect, QColor(self.BG))
        else:
            p.fillRect(rect, QBrush(self._tile))

    def _designator(self, it):
        if it.is_ns:
            return "FL1"
        if it.hub:
            return "MX1"
        n = "".join(ch for ch in it.cell_id if ch.isdigit()) or str(it.index + 1)
        return ("LS" if it.speaker else "U") + n

    def paint_body(self, p, it):
        b = self.body(it)
        closed = it.status == "closed"
        ink = QColor(it.color) if it.color else QColor(self.INK)
        p.fillRect(b, QColor(self.BG))
        pen = QPen(ink, 2.2 if it.hub else 1.8)
        if closed or it.ghost:
            pen.setStyle(Qt.CustomDashLine)
            pen.setDashPattern([4, 3])
        p.setPen(pen)
        p.drawRect(b)
        if it.selected:
            p.setPen(QPen(QColor("#ffd166"), 1.2, Qt.DashLine))
            p.drawRect(b.adjusted(-4, -4, 4, 4))
        p.setPen(QPen(alpha(ink, 120), 1))
        p.drawLine(QPointF(0, HEADER_H), QPointF(CELL_W, HEADER_H))
        text(p, QRectF(0, -15, 60, 13), self._designator(it), F(self.MONO, 10, weight=600), ink)
        badge = self.badges(it, "SPK", "AD", "EV")
        if badge:
            text(p, QRectF(CELL_W - 90, -15, 90, 13), badge, F(self.MONO, 9), alpha(ink, 180), R_ | V)
        text(p, QRectF(8, 0, CELL_W - 16, HEADER_H), it.title.upper(), F(self.MONO, 11, weight=600), ink)
        m = self.mid(it)
        if it.status == "blocked":
            self._switch(p, m, it)
        elif closed:
            age = it.fx("closed")
            k = 1.0 if age is None or age > 0.4 else 2.4 - 1.4 * ease_out_back(age / 0.4, 1.2)
            p.save()
            p.translate(m.center())
            p.rotate(-12)
            p.scale(k, k)
            p.setOpacity(1.0)
            p.setPen(QPen(QColor("#ff6b6b"), 2.2))
            p.drawRect(QRectF(-24, -10, 48, 20))
            text(p, QRectF(-24, -10, 48, 20), "DNP", F(self.MONO, 14, weight=600), "#ff6b6b", C)
            p.restore()
        elif it.hub and not it.is_ns:
            text(p, m, "Σ", F(self.MONO, 24), ink, C)
        elif it.speaker:
            c = m.center()
            sp = QPainterPath()
            sp.addRect(QRectF(c.x() - 12, c.y() - 6, 8, 12))
            sp.moveTo(c.x() - 4, c.y() - 6)
            sp.lineTo(c.x() + 8, c.y() - 14)
            sp.lineTo(c.x() + 8, c.y() + 14)
            sp.lineTo(c.x() - 4, c.y() + 6)
            stroke(p, sp, ink, 1.6, join=Qt.MiterJoin)
        else:
            text(p, m, "FILTER" if it.is_ns else ("SRC" if it.outs else "LOAD"), F(self.MONO, 9), alpha(ink, 170), C)

    def _switch(self, p, m, it):
        c = m.center()
        age = it.fx("block")
        ang = 32.0 if age is None or age > 0.35 else 32.0 * (age / 0.35)
        red = QColor("#ff8a8a")
        p.setPen(QPen(red, 1.6))
        p.setBrush(QColor(self.BG))
        p.drawLine(QPointF(c.x() - 30, c.y() + 4), QPointF(c.x() - 15, c.y() + 4))
        p.drawLine(QPointF(c.x() + 15, c.y() + 4), QPointF(c.x() + 30, c.y() + 4))
        p.drawEllipse(QPointF(c.x() - 15, c.y() + 4), 2.6, 2.6)
        p.drawEllipse(QPointF(c.x() + 15, c.y() + 4), 2.6, 2.6)
        a = math.radians(ang)
        p.setPen(QPen(red, 2))
        p.drawLine(QPointF(c.x() - 15, c.y() + 4), QPointF(c.x() - 15 + 29 * math.cos(a), c.y() + 4 - 29 * math.sin(a)))
        p.setBrush(Qt.NoBrush)
        text(p, QRectF(m.left(), m.bottom() - 11, m.width(), 11), "SW OPEN", F(self.MONO, 8), red, C)

    def paint_port(self, p, it, side, ch, rect, hovered):
        ink = QColor(self.INK)
        x = rect.left() if side == "in" else rect.right() - 8
        pad = QRectF(x, rect.center().y() - 4, 8, 8)
        p.setPen(QPen(ink, 1.4))
        p.setBrush(QColor("#ffd166") if hovered else QColor(self.BG))
        p.drawRect(pad)
        p.setBrush(Qt.NoBrush)
        lr = QRectF(pad.right() + 4, rect.top(), 16, rect.height()) if side == "in" else \
            QRectF(pad.left() - 20, rect.top(), 16, rect.height())
        text(p, lr, port_label(ch), F(self.MONO, 9), "#ffd166" if hovered else ink, C)

    def paint_both(self, p, it, side, rect, hovered):
        ink = QColor("#ffd166") if hovered else alpha(self.INK, 150)
        p.setPen(QPen(ink, 1, Qt.DashLine))
        p.setBrush(alpha("#ffd166", 40) if hovered else QColor(self.BG))
        p.drawRect(rect)
        p.setBrush(Qt.NoBrush)
        text(p, self.both_tab(side, rect), "L+R", F(self.MONO, 8), ink, C)

    def link_path(self, a, b, key=""):
        path = ortho(a, b, 0, 0.5)
        if b.x() - a.x() >= 24:
            mx = round((a.x() + (b.x() - a.x()) / 2) / 10) * 10 + ((hseed(key) % 4) - 1.5) * 8
            mx = max(a.x() + 10, min(b.x() - 10, mx))
            path = QPainterPath(a)
            path.lineTo(mx, a.y())
            path.lineTo(mx, b.y())
            path.lineTo(b)
        return path

    def paint_link(self, p, path, color, L):
        p.setRenderHint(QPainter.Antialiasing)
        if L.dashed:
            stroke(p, path, color, 1.2, Qt.CustomDashLine, Qt.FlatCap, Qt.MiterJoin, [4, 4])
            return
        shown = path
        if L.age is not None and L.age < 0.9:
            shown = partial(path, L.age / 0.9, 90)
        if L.selected:
            stroke(p, shown, alpha("#ffd166", 150), 6, cap=Qt.FlatCap, join=Qt.MiterJoin)
        if not L.live and not L.blocked:
            stroke(p, shown, color, 1.2, Qt.CustomDashLine, Qt.FlatCap, Qt.MiterJoin, [5, 4])
        else:
            stroke(p, shown, color, 1.8, cap=Qt.FlatCap, join=Qt.MiterJoin)
        if L.signal and shown is path:        # current flowing: marching dashes
            pen = QPen(QColor("#ffd166"), 2.6, Qt.CustomDashLine, Qt.FlatCap, Qt.MiterJoin)
            pen.setDashPattern([1.2, 5])
            pen.setDashOffset(-self.t * 9)
            p.strokePath(path, pen)
        if shown is path:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(color))
            for u in (0.0, 1.0):
                p.drawEllipse(path.pointAtPercent(u), 3, 3)
            p.setBrush(Qt.NoBrush)
        if L.blocked:
            mid = path.pointAtPercent(0.5)
            p.fillRect(QRectF(mid.x() - 11, mid.y() - 9, 22, 18), QColor(self.BG))
            p.setPen(QPen(QColor(color), 2))
            p.drawLine(QPointF(mid.x() - 7, mid.y() - 7), QPointF(mid.x() + 7, mid.y() + 7))
            p.drawLine(QPointF(mid.x() - 7, mid.y() + 7), QPointF(mid.x() + 7, mid.y() - 7))
        elif L.label:
            _tag(p, path, L.label, F(self.MONO, 9), self.BG, color, 0, color)


# =====================================================================================
# 5 · TERMINAL
# =====================================================================================

class TuiSkin(Skin):
    key, name = "tui", "Terminal"
    blurb = "A full-screen TUI: box drawing, monospace, F-keys along the bottom."
    animated = True
    ui_font = ("JetBrains Mono", 9)
    style_args = dict(window="#0b0c0d", base="#101214", surface="#16191c", accent="#22272c", secondary="#2b6cb0",
                      border="#4b5058", outline="#c7c9c7", text="#c7c9c7", corner_radius=0)
    canvas = {"link_in": "#4fd6e4", "link_out": "#e5c07b", "link_bypass": "#98c379", "link_direct": "#c678dd",
              "link_pending": "#4b5058", "link_blocked": "#e06c75", "closed": "#4b5058", "fg": "#ffffff",
              "link_width": 1.5}
    MONO = "JetBrains Mono"
    FG, DIM, BG = "#c7c9c7", "#5b616a", "#0b0c0d"

    def __init__(self):
        super().__init__()
        self.title_font = F(self.MONO, 14, bold=True)

    def box_title(self, label, index):
        return f"~/{label.lower().replace(' ', '-')}"

    def paint_background(self, p, rect, view):
        p.fillRect(rect, QColor(self.BG))

    def paint_foreground(self, p, w, h, view):
        bar = QRectF(0, h - 18, w, 18)
        p.fillRect(bar, QColor("#2b6cb0"))
        f = F(self.MONO, 11)
        x = 0.0
        for k, label in (("F2", "NewCell"), ("F5", "Refresh"), ("F9", "Block"), ("Del", "Unlink"), ("Esc", "Back")):
            kw = QFontMetricsF(f).horizontalAdvance(k) + 6
            if x + kw + 70 > w:
                break
            p.fillRect(QRectF(x, h - 18, kw, 18), QColor("#18a0b0"))
            text(p, QRectF(x, h - 18, kw, 18), k, f, "#001014", C, fit=False)
            text(p, QRectF(x + kw + 3, h - 18, 70, 18), label, f, "#e8f1ff", fit=False)
            x += kw + 70

    def paint_body(self, p, it):
        h = it.height()
        col = QColor(it.color) if it.color else QColor(self.FG)
        if it.status == "blocked":
            col = QColor("#e06c75")
        elif it.status == "closed":
            col = QColor(self.DIM)
        elif it.hub:
            col = QColor(it.color) if it.color else QColor("#c678dd")
        frame = QRectF(0, 8, CELL_W, h - 8)
        p.fillRect(QRectF(0, 0, CELL_W, h), QColor(self.BG))
        pen = QPen(col, 1.2)
        if it.ghost:
            pen.setStyle(Qt.DotLine)
        p.setPen(pen)
        p.drawRect(frame)
        if it.hub:
            p.drawRect(frame.adjusted(3, 3, -3, -3))
        if it.selected:
            p.setPen(QPen(QColor("#ffffff"), 1, Qt.DashLine))
            p.drawRect(frame.adjusted(-3, -3, 3, 3))
        tf = F(self.MONO, 11, bold=True)
        badge = self.badges(it, "spk", "^", "ev")
        title = elide(it.title, tf, CELL_W - 40 - (len(badge) * 7 + 10 if badge else 0))
        tw = QFontMetricsF(tf).horizontalAdvance(title) + 10
        p.fillRect(QRectF(9, 0, tw, 16), QColor(self.BG))
        text(p, QRectF(14, 0, tw, 16), title, tf, col, fit=False)
        if badge:
            bw = QFontMetricsF(tf).horizontalAdvance(badge) + 8
            p.fillRect(QRectF(CELL_W - bw - 8, 0, bw, 16), QColor(self.BG))
            text(p, QRectF(CELL_W - bw - 4, 0, bw, 16), badge, F(self.MONO, 10), self.DIM, fit=False)
        m = self.mid(it)
        f = F(self.MONO, 10)
        r1 = QRectF(m.left() + 2, m.top() + 1, m.width() - 4, min(16.0, m.height() / 2))
        r2 = QRectF(m.left() + 2, r1.bottom(), m.width() - 4, min(16.0, m.height() / 2))
        if it.status == "blocked":
            age = it.fx("block")
            flash = age is not None and age < 0.5 and int(age * 8) % 2 == 0
            if flash:
                p.fillRect(r1, QColor("#e06c75"))
            text(p, r1, "[BLOCKED]", f, "#000000" if flash else "#e06c75", C)
            text(p, r2, "F9 unblock", f, self.DIM, C)
        elif it.status == "closed":
            text(p, r1, "░░░░░░░░░░", f, self.DIM, C)
            text(p, r2, "closed", f, self.DIM, C)
        else:
            text(p, r1, "● live" if it.signal else "○ idle", f, "#98c379" if it.signal else self.DIM, C)
            if it.signal:
                glyphs = "▁▂▃▄▅▆▇"
                step = int(self.t * 9)
                s = "".join(glyphs[_hash2(step + i * 7, it.index + i) % len(glyphs)] for i in range(7))
                text(p, r2, s, f, "#98c379", C)
            else:
                text(p, r2, "mix bus" if it.hub and not it.is_ns else "denoise" if it.is_ns else "stereo" if len(it.ins or it.outs) > 1 else "mono",
                     f, self.DIM, C)

    def paint_port(self, p, it, side, ch, rect, hovered):
        r = QRectF(rect.left() + (4 if side == "in" else PORT_W - 28), rect.top(), 24, rect.height())
        if hovered:
            p.fillRect(r, QColor(self.FG))
        col = "#4fd6e4" if side == "in" else "#e5c07b"
        text(p, r, f"[{port_label(ch)}]", F(self.MONO, 10), self.BG if hovered else col, C, fit=False)

    def paint_both(self, p, it, side, rect, hovered):
        p.setPen(QPen(QColor(self.FG if hovered else self.DIM), 1, Qt.DotLine))
        p.setBrush(QColor("#22272c") if hovered else Qt.NoBrush)
        p.drawRect(rect.adjusted(2, 0, -2, 0))
        p.setBrush(Qt.NoBrush)
        text(p, self.both_tab(side, rect), "[*]", F(self.MONO, 10), self.FG if hovered else self.DIM, C)

    def link_path(self, a, b, key=""):
        path = ortho(a, b)
        if b.x() - a.x() >= 24:
            mx = round((a.x() + (b.x() - a.x()) / 2) / 9) * 9 + ((hseed(key) % 3) - 1) * 9
            mx = max(a.x() + 9, min(b.x() - 9, mx))
            path = QPainterPath(a)
            path.lineTo(mx, a.y())
            path.lineTo(mx, b.y())
            path.lineTo(b)
        return path

    def paint_link(self, p, path, color, L):
        p.setRenderHint(QPainter.Antialiasing, False)
        if L.dashed:
            stroke(p, path, color, 1, Qt.DotLine, Qt.FlatCap, Qt.MiterJoin)
            p.setRenderHint(QPainter.Antialiasing)
            return
        shown = path
        if L.age is not None:
            length = max(1.0, path.length())
            chars = int(L.age / 0.016)                 # typed out one character cell at a time
            if chars * 9 < length:
                shown = partial(path, chars * 9 / length, 120)
        if L.selected:
            stroke(p, shown, QColor("#ffffff"), 4, cap=Qt.FlatCap, join=Qt.MiterJoin)
        style = Qt.SolidLine if (L.live and not L.blocked) else Qt.DashLine
        stroke(p, shown, color, 1.5, style, Qt.FlatCap, Qt.MiterJoin)
        p.setRenderHint(QPainter.Antialiasing)
        if shown is path:
            arrow_head(p, path, color, 8)
            if L.signal:                               # a packet stepping along the wire
                length = max(1.0, path.length())
                n = max(1, int(length / 9))
                pt = path.pointAtPercent(((int(self.t * 14) + L.seed) % n) / n)
                p.fillRect(QRectF(pt.x() - 3.5, pt.y() - 3.5, 7, 7), QColor("#ffffff"))
        if L.blocked:
            mid = path.pointAtPercent(0.5)
            p.fillRect(QRectF(mid.x() - 7, mid.y() - 8, 14, 16), QColor(self.BG))
            text(p, QRectF(mid.x() - 7, mid.y() - 8, 14, 16), "✗", F(self.MONO, 12, bold=True), color, C, fit=False)
        elif L.label:
            _tag(p, path, L.label, F(self.MONO, 10), self.BG, color, 0)


# =====================================================================================
# 6 · LIQUID GLASS
# =====================================================================================

class GlassSkin(Skin):
    key, name = "glass", "Liquid Glass"
    blurb = "Frosted cards over a drifting aurora; springy and soft."
    animated = True
    ui_font = ("Manrope", 10)
    style_args = dict(window="#0c1316", base="#101a1e", surface="#1a272c", accent="#26373d", secondary="#14b8a6",
                      border="#3a4d54", outline="#d7e6ea", text="#f2f6f7", corner_radius=16)
    canvas = {"link_in": "#5eead4", "link_out": "#fb7185", "link_bypass": "#a3e635", "link_direct": "#c4b5fd",
              "link_pending": "#5b6b70", "link_blocked": "#6b4a52", "closed": "#7d8b90", "fg": "#ffffff",
              "link_width": 4.0}

    def __init__(self):
        super().__init__()
        self.title_font = F("Manrope", 17, weight=800)

    def tint(self, label):
        return QColor.fromHsv(hseed(label) % 360, 140, 245)

    def paint_background(self, p, rect, view):
        p.fillRect(rect, QColor("#0c1316"))
        p.save()
        p.resetTransform()
        w, h = view.viewport().width(), view.viewport().height()
        t = self.t
        for (bx, by, rad, col, sp, ph) in ((0.18, 0.2, 0.55, QColor(20, 184, 166, 105), 0.11, 0.0),
                                           (0.85, 0.85, 0.5, QColor(244, 63, 94, 95), 0.08, 2.0),
                                           (0.55, 0.45, 0.4, QColor(132, 204, 22, 50), 0.06, 4.0)):
            cx = (bx + 0.07 * math.sin(t * sp + ph)) * w
            cy = (by + 0.07 * math.cos(t * sp * 1.3 + ph)) * h
            r = rad * max(w, h)
            g = QRadialGradient(cx, cy, r)
            g.setColorAt(0, col)
            g.setColorAt(1, QColor(col.red(), col.green(), col.blue(), 0))
            p.fillRect(QRectF(cx - r, cy - r, 2 * r, 2 * r).intersected(QRectF(0, 0, w, h)), g)
        p.restore()

    def cell_opacity(self, it):
        if it.status == "closed":
            return 0.42
        return super().cell_opacity(it) if it.status != "inactive" else 0.8

    def paint_body(self, p, it):
        b = self.body(it)
        blocked = it.status == "blocked"
        if blocked:
            age = it.fx("block")
            k = 0.95 if age is None or age > 0.6 else 1.0 - 0.05 * ease_out_back(age / 0.6, 4.0)
            c = b.center()
            p.translate(c)
            p.scale(k, k)
            p.translate(-c)
        tint = QColor(it.color) if it.color else self.tint(it.title)
        if blocked or it.status == "closed":
            g = int(tint.lightness() * 0.7)
            tint = QColor(g, g, g)
        if it.signal:
            pulse = 0.5 + 0.5 * math.sin(self.t * 2.6 + it.index)
            p.setPen(QPen(alpha("#a7f3d0", 30 + 60 * pulse), 3 + 4 * pulse))
            p.drawRoundedRect(b.adjusted(-2, -2, 2, 2), 20, 20)
        pen = QPen(QColor(255, 255, 255, 70), 1.2)
        if it.status == "closed" or it.ghost:
            pen.setStyle(Qt.DashLine)
        g = QLinearGradient(0, 0, 0, b.height())
        g.setColorAt(0, QColor(255, 255, 255, 44 if it.hub else 34))
        g.setColorAt(1, QColor(255, 255, 255, 16))
        p.setPen(pen)
        p.setBrush(g)
        p.drawRoundedRect(b, 18, 18)
        p.setBrush(Qt.NoBrush)
        if it.selected:
            p.setPen(QPen(QColor(255, 255, 255, 220), 2))
            p.drawRoundedRect(b.adjusted(-3, -3, 3, 3), 21, 21)
        tile = QRectF(9, 4, 19, 19)
        tg = QLinearGradient(tile.topLeft(), tile.bottomRight())
        tg.setColorAt(0, tint.lighter(120))
        tg.setColorAt(1, tint.darker(170))
        p.setPen(Qt.NoPen)
        p.setBrush(tg)
        p.drawRoundedRect(tile, 6, 6)
        p.setBrush(Qt.NoBrush)
        text(p, tile, (it.title[:1] or "?").upper(), F("Manrope", 11, weight=800), "#ffffff", C, fit=False)
        badge = self.badges(it, "♪", "^", "⚡")
        bf = F("Manrope", 10, weight=700)
        bw = QFontMetricsF(bf).horizontalAdvance(badge) + 8 if badge else 0
        text(p, QRectF(34, 1, CELL_W - 44 - bw, HEADER_H), it.title, F("Manrope", 12, weight=700), "#f2f6f7")
        if badge:
            text(p, QRectF(CELL_W - bw - 10, 1, bw, HEADER_H), badge, bf, alpha("#ffffff", 170), R_ | V)
        m = self.mid(it)
        sub = self.status_word(it, "Playing", "Quiet", "Blocked", "Closed")
        if it.hub and it.status == "active":
            sub = "Denoising" if it.is_ns else ("Mixing" if it.signal else "Mix")
        sf = F("Manrope", 10, weight=600)
        if it.signal:
            sw = QFontMetricsF(sf).horizontalAdvance(sub)
            x0 = m.center().x() - (sw + 20) / 2
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#a7f3d0"))
            for i in range(4):
                hh = 3 + 8 * abs(math.sin(self.t * (4.5 + i * 0.9) + i * 1.3 + it.index))
                p.drawRoundedRect(QRectF(x0 + i * 4, m.center().y() + 5 - hh, 2.6, hh), 1.2, 1.2)
            p.setBrush(Qt.NoBrush)
            text(p, QRectF(x0 + 20, m.top(), sw + 4, m.height()), sub, sf, alpha("#ffffff", 190), fit=False)
        else:
            text(p, m, sub, sf, alpha("#ffffff", 170), C)
        if blocked:
            age = it.fx("block")
            k = 1.0 if age is None or age > 0.5 else ease_out_back(age / 0.5, 2.6)
            c = QPointF(CELL_W - 6, 6)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#f43f5e"))
            p.drawEllipse(c, 10 * k, 10 * k)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor("#ffffff"), 1.6 * k))
            p.drawRoundedRect(QRectF(c.x() - 4 * k, c.y() - 1 * k, 8 * k, 6 * k), 1.5, 1.5)
            p.drawArc(QRectF(c.x() - 2.6 * k, c.y() - 5 * k, 5.2 * k, 7 * k), 0, 180 * 16)

    def paint_port(self, p, it, side, ch, rect, hovered):
        p.setPen(QPen(QColor(255, 255, 255, 90), 1))
        p.setBrush(QColor(255, 255, 255, 110 if hovered else 44))
        p.drawRoundedRect(rect, 8, 8)
        p.setBrush(Qt.NoBrush)
        text(p, rect, port_label(ch), F("Manrope", 10, weight=700), "#ffffff", C)

    def paint_both(self, p, it, side, rect, hovered):
        p.setPen(QPen(QColor(255, 255, 255, 60), 1))
        p.setBrush(QColor(255, 255, 255, 70 if hovered else 20))
        p.drawRoundedRect(rect, 10, 10)
        p.setBrush(Qt.NoBrush)
        text(p, self.both_tab(side, rect), "Both", F("Manrope", 9, weight=600), alpha("#ffffff", 200), C)

    def paint_link(self, p, path, color, L):
        p.setRenderHint(QPainter.Antialiasing)
        if L.dashed:
            stroke(p, path, alpha(color, 150), 2, Qt.DashLine)
            return
        w = L.width or 4.0
        a, b = path.pointAtPercent(0), path.pointAtPercent(1)
        brush = QColor(color)
        if L.live and not L.blocked and L.src_tint is not None and L.dst_tint is not None:
            g = QLinearGradient(a, b)
            g.setColorAt(0, L.src_tint)
            g.setColorAt(1, L.dst_tint)
            brush = QBrush(g)
        shown = path
        head = None
        if L.age is not None and L.age < 0.95:
            k = L.age / 0.95
            k = 2 * k * k if k < 0.5 else 1 - (-2 * k + 2) ** 2 / 2
            shown = partial(path, k, 64)
            head = shown.pointAtPercent(1.0)
        if L.selected:
            stroke(p, shown, QColor(255, 255, 255, 150), w + 6)
        if L.live and not L.blocked:
            p.strokePath(shown, QPen(QBrush(alpha(color, 46)), w * 3.4, Qt.SolidLine, Qt.RoundCap))
        pen = QPen(brush, w, Qt.SolidLine, Qt.RoundCap)
        if not L.live or L.blocked:
            pen = QPen(alpha(color, 170), w - 1.5, Qt.SolidLine, Qt.RoundCap)
        p.strokePath(shown, pen)
        if L.signal and head is None:
            u = (self.t * 0.35 + (L.seed % 100) / 100.0) % 1.0
            pt = path.pointAtPercent(u)
            fade = math.sin(math.pi * u)
            g = QRadialGradient(pt, 9)
            g.setColorAt(0, QColor(255, 255, 255, int(210 * fade)))
            g.setColorAt(1, QColor(255, 255, 255, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(g)
            p.drawEllipse(pt, 9, 9)
            p.setBrush(Qt.NoBrush)
        if head is not None:                            # the droplet
            g = QRadialGradient(head, 14)
            g.setColorAt(0, QColor(255, 255, 255, 255))
            g.setColorAt(0.35, QColor(167, 243, 208, 200))
            g.setColorAt(1, QColor(167, 243, 208, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(g)
            p.drawEllipse(head, 14, 14)
            p.setBrush(Qt.NoBrush)
        if L.label:
            _tag(p, path, L.label, F("Manrope", 10, weight=700), QColor(20, 30, 34, 230), "#ffffff", 9,
                 QColor(255, 255, 255, 70))


# =====================================================================================
# 7 · SWISS POSTER
# =====================================================================================

class SwissSkin(Skin):
    key, name = "swiss", "Swiss Poster"
    blurb = "International style: black, white, one red. Nothing eases, everything snaps."
    animated = True
    ui_font = ("Archivo", 10)
    style_args = dict(window="#fafaf7", base="#f0f0ea", surface="#ffffff", accent="#e6e6df", secondary="#ff2a12",
                      border="#0a0a0a", outline="#0a0a0a", text="#0a0a0a", text_outline="#ffffff", corner_radius=0)
    canvas = {"link_in": "#0a0a0a", "link_out": "#ff2a12", "link_bypass": "#1f3fff", "link_direct": "#6f6f66",
              "link_pending": "#b9b9b3", "link_blocked": "#ff2a12", "closed": "#8a8a84", "fg": "#0a0a0a",
              "link_width": 3.0}
    INK, RED, PAPER = "#0a0a0a", "#ff2a12", "#fafaf7"

    def __init__(self):
        super().__init__()
        self.title_font = F("Archivo Black", 18, caps=True)

    def box_title(self, label, index):
        return f"{index + 1:02d} / {label}"

    def paint_background(self, p, rect, view):
        p.fillRect(rect, QColor(self.PAPER))
        label = view.editor.plain_box_label(view.box).upper()
        f = F("Archivo Black", 150, spacing=-5)
        r = QRectF(-520, 70, 2400, 190)
        if rect.intersects(r):
            text(p, r, label, f, "#efefe9", fit=False)

    def paint_body(self, p, it):
        b = self.body(it)
        closed = it.status == "closed"
        hub = it.hub
        fill = QColor(it.color) if it.color else QColor(self.INK if hub else self.PAPER)
        ink = QColor(self.PAPER) if fill.lightness() < 130 else QColor(self.INK)
        p.fillRect(b, fill)
        p.fillRect(QRectF(0, 0, CELL_W, 5), QColor(self.RED if hub else self.INK))
        if it.ghost:
            p.setPen(QPen(QColor(self.INK), 1, Qt.DashLine))
            p.drawRect(b)
        if it.selected:
            p.setPen(QPen(QColor(self.RED), 2))
            p.drawRect(b.adjusted(-3, -3, 3, 3))
        nf = F("Archivo Black", 15)
        num = ("NR" if it.is_ns else "Σ") if hub else f"{it.index + 1:02d}"
        text(p, QRectF(6 if hub else 0, 6, 26, 20), num, nf, ink, fit=False)
        tf = F("Archivo", 11, weight=800, caps=True)
        badge = self.badges(it, "SPK", "AD", "EV")
        bf = F("Archivo", 8, weight=700, spacing=0.8)
        bw = QFontMetricsF(bf).horizontalAdvance(badge) + 6 if badge else 0
        tr = QRectF(32 if hub else 26, 6, CELL_W - (38 if hub else 30) - bw, 20)
        name = elide(it.title.upper(), tf, tr.width())
        if closed:                                       # outline type
            path = QPainterPath()
            f2 = QFont(tf)
            f2.setCapitalization(QFont.MixedCase)
            path.addText(tr.left(), tr.center().y() + 4, f2, name)
            p.setPen(QPen(ink, 0.9))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
        else:
            text(p, tr, name, tf, ink, fit=False)
        if badge:
            text(p, QRectF(CELL_W - bw - 4, 6, bw, 20), badge, bf, ink, R_ | V)
        if it.status == "blocked":
            age = it.fx("block")
            k = 1.0 if age is None else min(1.0, (int(age / 0.07) + 1) / 3.0)      # slams in, three hard steps
            tw = QFontMetricsF(tf).horizontalAdvance(name) + 8
            p.fillRect(QRectF(tr.left() - 4, tr.center().y() - 3, tw * k, 7), QColor(self.RED))
        m = self.mid(it)
        sf = F("Archivo", 9, weight=700, spacing=1.2)
        word = self.status_word(it, "LIVE", "IDLE", "OFF", "CLOSED")
        sw = QFontMetricsF(sf).horizontalAdvance(word) + 14
        x0 = m.center().x() - sw / 2
        sq = QRectF(x0, m.center().y() - 4, 8, 8)
        if it.signal:
            if int(self.t * 2.5) % 2 == 0:
                p.fillRect(sq, QColor(self.RED))
        elif it.status == "blocked":
            p.fillRect(sq, QColor(self.RED))
        else:
            p.setPen(QPen(ink, 1.4))
            p.drawRect(sq.adjusted(0.7, 0.7, -0.7, -0.7))
        text(p, QRectF(x0 + 14, m.top(), sw, m.height()), word, sf, ink, fit=False)

    def paint_port(self, p, it, side, ch, rect, hovered):
        r = QRectF(rect.left() if side == "in" else rect.right() - 20, rect.top(), 20, rect.height())
        p.fillRect(r, QColor(self.RED if hovered else self.INK))
        if it.hub and not it.color:
            p.setPen(QPen(QColor(self.PAPER), 1))
            p.drawRect(r)
        text(p, r, port_label(ch), F("Archivo", 10, weight=800), self.PAPER, C)

    def paint_both(self, p, it, side, rect, hovered):
        dark = it.hub and not it.color
        ink = QColor(self.RED) if hovered else QColor(self.PAPER if dark else self.INK)
        p.setPen(QPen(ink, 1.4))
        p.setBrush(Qt.NoBrush)
        p.drawRect(rect)
        text(p, self.both_tab(side, rect), "L+R", F("Archivo", 8, weight=800), ink, C)

    def link_path(self, a, b, key=""):
        path = ortho(a, b)
        if b.x() - a.x() >= 24:
            mx = a.x() + (b.x() - a.x()) / 2 + ((hseed(key) % 4) - 1.5) * 9
            mx = max(a.x() + 10, min(b.x() - 16, mx))
            path = QPainterPath(a)
            path.lineTo(mx, a.y())
            path.lineTo(mx, b.y())
            path.lineTo(b)
        return path

    def paint_link(self, p, path, color, L):
        p.setRenderHint(QPainter.Antialiasing)
        if L.dashed:
            stroke(p, path, color, 1.2, Qt.CustomDashLine, Qt.FlatCap, Qt.MiterJoin, [2, 4])
            return
        shown = path
        if L.age is not None and L.age < 0.32:
            shown = partial(path, (int(L.age / 0.08) + 1) / 4.0, 80)         # four hard steps
        w = L.width or 3.0
        if L.selected:
            stroke(p, shown, alpha(self.RED, 90), w + 7, cap=Qt.FlatCap, join=Qt.MiterJoin)
        if L.blocked or not L.live:
            stroke(p, shown, color, 1.3, Qt.CustomDashLine, Qt.FlatCap, Qt.MiterJoin, [2, 5])
        else:
            stroke(p, shown, color, w, cap=Qt.FlatCap, join=Qt.MiterJoin)
            if shown is path:
                arrow_head(p, path, color, 11)
        if L.blocked:
            mid = path.pointAtPercent(0.5)
            p.fillRect(QRectF(mid.x() - 9, mid.y() - 3.5, 18, 7), QColor(self.RED))
        elif L.label:
            _tag(p, path, L.label, F("Archivo", 9, weight=800), self.INK, self.PAPER, 0)

    def paint_foreground(self, p, w, h, view):
        age = view.editor.fx_age(("flash", view.box))
        if age is not None and age < 0.09:
            p.fillRect(QRectF(0, 0, w, h), QColor(self.RED))


# =====================================================================================
# 8 · MIXING DESK  (the desk itself is conduit_desk.py; this is its patch view)
# =====================================================================================

class DeskSkin(Skin):
    key, name = "desk", "Mixing Desk"
    blurb = "A console: every device is a channel strip, links are lit routing buttons."
    animated = True
    has_desk = True
    ui_font = ("Barlow Condensed", 11)
    style_args = dict(window="#1c1d20", base="#17181b", surface="#2a2c31", accent="#3a3d44", secondary="#ffb43a",
                      border="#4a4d55", outline="#d9d9d4", text="#d9d9d4", corner_radius=4)
    canvas = {"link_in": "#ffb43a", "link_out": "#7ad3ff", "link_bypass": "#7bf0a0", "link_direct": "#e6a0ff",
              "link_pending": "#5a5d64", "link_blocked": "#7a1f1a", "closed": "#5a6460", "fg": "#ffffff",
              "link_width": 2.6}
    MONO = "Share Tech Mono"
    LCD, LCD_OFF, LCD_INK = "#8fd6c6", "#26302d", "#0b2a25"

    def __init__(self):
        super().__init__()
        self.title_font = F("Barlow Condensed", 19, weight=700, spacing=2.0, caps=True)

    def paint_background(self, p, rect, view):
        p.fillRect(rect, QColor("#1c1d20"))
        self.dot_grid(p, rect, QColor(255, 255, 255, 10), 32)

    def level(self, seed, t) -> float:
        """A plausible bouncing level, 0..1 (activity, not a measurement)."""
        return max(0.0, min(1.0, 0.55 + 0.22 * math.sin(t * 6.1 + seed) + 0.14 * math.sin(t * 13.7 + seed * 2.1)
                            + 0.09 * math.sin(t * 29.3 + seed * 0.7)))

    def paint_body(self, p, it):
        b = self.body(it)
        g = QLinearGradient(0, 0, CELL_W, 0)
        base = ("#33302a", "#3b372f") if it.hub else ("#2a2c31", "#303238")
        g.setColorAt(0, QColor(base[0]))
        g.setColorAt(0.5, QColor(base[1]))
        g.setColorAt(1, QColor(base[0]))
        pen = QPen(QColor("#0d0d0f"), 1.5)
        if it.ghost:
            pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        p.setBrush(g)
        p.drawRoundedRect(b, 4, 4)
        p.setBrush(Qt.NoBrush)
        if it.selected:
            p.setPen(QPen(QColor("#ffb43a"), 2))
            p.drawRoundedRect(b.adjusted(-2, -2, 2, 2), 5, 5)
        lcd = QRectF(5, 4, CELL_W - 10, HEADER_H - 7)
        closed, blocked = it.status == "closed", it.status == "blocked"
        age = it.fx("closed")
        off = closed and (age is None or age > 0.8 or int(age * 5) % 2 == 1)
        fill = QColor(it.color) if it.color and not off else QColor(self.LCD_OFF if off else "#d68f8f" if blocked else self.LCD)
        p.setPen(QPen(QColor("#0b0b0c"), 1))
        p.setBrush(fill)
        p.drawRoundedRect(lcd, 2, 2)
        p.setBrush(Qt.NoBrush)
        ink = "#4a5a55" if off else self.LCD_INK
        badge = self.badges(it, "SPK", "^", "E")
        f = F(self.MONO, 12)
        bw = QFontMetricsF(f).horizontalAdvance(badge) + 6 if badge else 0
        text(p, lcd.adjusted(5, 0, -5 - bw, 0), "--------" if off else it.title, f, ink)
        if badge and not off:
            text(p, QRectF(lcd.right() - bw - 3, lcd.top(), bw, lcd.height()), badge, F(self.MONO, 10), ink, R_ | V)
        m = self.mid(it)
        segs = 10
        sw = (m.width() - 8) / segs
        lit = int(self.level(it.index * 1.7, self.t) * segs + 0.5) if it.signal else 0
        y = m.top() + 3
        for i in range(segs):
            on = i < lit
            col = (QColor("#ff4a3a") if i >= 8 else QColor("#ffd23a") if i >= 6 else QColor("#45e35c")) if on else \
                (QColor("#3a1414") if i >= 8 else QColor("#3a3414") if i >= 6 else QColor("#16301b"))
            p.fillRect(QRectF(m.left() + 4 + i * sw, y, sw - 1.5, 6), col)
        mr = QRectF(m.center().x() - 22, y + 11, 44, 13)
        p.setPen(QPen(QColor("#0b0b0c"), 1))
        p.setBrush(QColor("#ff3b30") if blocked else QColor("#1a1b1e"))
        p.drawRoundedRect(mr, 2, 2)
        p.setBrush(Qt.NoBrush)
        text(p, mr, "MUTE", F("Barlow Condensed", 10, weight=700, spacing=1.0), "#2a0000" if blocked else "#7a7a75", C)

    def paint_port(self, p, it, side, ch, rect, hovered):
        r = QRectF(rect.left() + (2 if side == "in" else PORT_W - 24), rect.top(), 22, rect.height())
        amber = QColor("#ffb43a" if side == "in" else "#7ad3ff")
        p.setPen(QPen(QColor("#0b0b0c"), 1))
        p.setBrush(amber if hovered else QColor("#1a1b1e"))
        p.drawRoundedRect(r, 2.5, 2.5)
        p.setBrush(Qt.NoBrush)
        text(p, r, port_label(ch), F("Barlow Condensed", 11, weight=700), "#1a1000" if hovered else amber, C)

    def paint_both(self, p, it, side, rect, hovered):
        p.setPen(QPen(QColor("#ffb43a") if hovered else QColor("#0b0b0c"), 1))
        p.setBrush(QColor("#111214"))
        p.drawRoundedRect(rect, 3, 3)
        p.setBrush(Qt.NoBrush)
        text(p, self.both_tab(side, rect), "BOTH", F("Barlow Condensed", 9, weight=700, spacing=0.8),
             "#ffb43a" if hovered else "#7a7a75", C)

    def paint_link(self, p, path, color, L):
        p.setRenderHint(QPainter.Antialiasing)
        if L.dashed:
            stroke(p, path, color, 1.6, Qt.DashLine)
            return
        w = L.width or 2.6
        shown = path if L.age is None or L.age >= 0.4 else partial(path, L.age / 0.4)
        if L.selected:
            stroke(p, shown, QColor(255, 255, 255, 200), w + 4)
        if L.live and not L.blocked:
            stroke(p, shown, alpha(color, 50), w * 3)
        stroke(p, shown, color, w, Qt.SolidLine if L.live or L.blocked else Qt.DashLine)
        if L.label:
            _tag(p, path, L.label, F(self.MONO, 10), self.LCD, self.LCD_INK, 2, "#0b0b0c")

    def eq_colors(self):
        return {"bg": QColor("#0f1513"), "grid": QColor(143, 214, 198, 40), "text": QColor("#8fd6c6"),
                "curve": QColor("#ffb43a"), "fill": QColor(255, 180, 58, 40), "spectrum": QColor(69, 227, 92, 80),
                "handle": QColor("#d9d9d4"), "handle_sel": QColor("#ff3b30"), "band": QColor(143, 214, 198, 70)}


# =====================================================================================
# 9 · NOTEBOOK
# =====================================================================================

class SketchSkin(Skin):
    key, name = "sketch", "Notebook"
    blurb = "Hand-drawn on lined paper: sticky notes and pen arrows."
    animated = True
    ui_font = ("Caveat", 14)
    style_args = dict(window="#fbf8ef", base="#f3eee0", surface="#fffdf6", accent="#ece5d0", secondary="#ffe98a",
                      border="#2b2b33", outline="#2b2b33", text="#2b2b33", text_outline="#ffffff", corner_radius=5)
    canvas = {"link_in": "#2b59c3", "link_out": "#d1442f", "link_bypass": "#2a7a3a", "link_direct": "#7a3fa8",
              "link_pending": "#a9a59a", "link_blocked": "#d1442f", "closed": "#9a968a", "fg": "#2b2b33",
              "link_width": 2.3}
    INK = "#2b2b33"
    NOTES = ["#ffe98a", "#cfe3ff", "#c9f2d3", "#ffc2d1", "#ffd8a8", "#e3d3ff"]

    def __init__(self):
        super().__init__()
        self.title_font = F("Caveat", 24, bold=True)

    def box_title(self, label, index):
        return f"{label.lower()} stuff"

    def paint_background(self, p, rect, view):
        p.fillRect(rect, QColor("#fbf8ef"))
        p.setPen(QPen(QColor("#cfe0f0"), 1.2))
        y = math.floor(rect.top() / 28) * 28
        lines = []
        while y < rect.bottom():
            lines.append(QLineF(rect.left(), y, rect.right(), y))
            y += 28
        if len(lines) < 400:
            p.drawLines(lines)

    def _angle(self, it):
        a = ((hseed(it.cell_id) % 300) / 100.0 - 1.5)
        return a * (3.0 if it.status == "closed" else 1.0)

    def paint_cell(self, p, it):
        p.save()
        c = QPointF(CELL_W / 2, it.height() / 2)
        p.translate(c)
        p.rotate(self._angle(it))
        p.translate(-c)
        super().paint_cell(p, it)
        p.restore()

    def _wobbly_rect(self, r: QRectF, seed, j=1.3) -> QPainterPath:
        rng = Rng(seed)
        pts = [r.topLeft(), r.topRight(), r.bottomRight(), r.bottomLeft()]
        path = QPainterPath()
        for i in range(4):
            a, b = pts[i], pts[(i + 1) % 4]
            a = QPointF(a.x() + (rng() - .5) * j * 2, a.y() + (rng() - .5) * j * 2)
            if i == 0:
                path.moveTo(a)
            mid = QPointF((a.x() + b.x()) / 2 + (rng() - .5) * j * 3, (a.y() + b.y()) / 2 + (rng() - .5) * j * 3)
            path.quadTo(mid, QPointF(b.x() + (rng() - .5) * j * 2, b.y() + (rng() - .5) * j * 2))
        return path

    def paint_body(self, p, it):
        b = self.body(it)
        seed = hseed(it.cell_id)
        ink = QColor(self.INK)
        if it.hub:
            p.fillRect(b, QColor("#fffdf6"))
            stroke(p, self._wobbly_rect(b, seed), ink, 2.4)
            stroke(p, self._wobbly_rect(b.adjusted(-3, -3, 3, 3), seed + 9, 2.0), alpha(ink, 150), 1.1)
        else:
            p.fillRect(b.translated(3, 5), QColor(0, 0, 0, 20))
            p.fillRect(b, QColor(it.color) if it.color else QColor(self.NOTES[seed % len(self.NOTES)]))
            p.save()
            p.translate(CELL_W / 2, 0)
            p.rotate(-self._angle(it) * 2)
            p.fillRect(QRectF(-26, -8, 52, 15), QColor(255, 255, 255, 150))
            p.restore()
            if it.ghost:
                stroke(p, self._wobbly_rect(b, seed), alpha(ink, 160), 1.2, Qt.DashLine)
        if it.selected:
            stroke(p, self._wobbly_rect(b.adjusted(-6, -6, 6, 6), seed + 3, 2.2), QColor("#d1442f"), 2)
        badge = self.badges(it, "♪", "^", "!")
        bf = F("Caveat", 15, bold=True)
        bw = QFontMetricsF(bf).horizontalAdvance(badge) + 6 if badge else 0
        text(p, QRectF(10, 2, CELL_W - 20 - bw, HEADER_H), it.title, F("Caveat", 20, bold=True), ink)
        if badge:
            text(p, QRectF(CELL_W - bw - 8, 2, bw, HEADER_H), badge, bf, "#d1442f", R_ | V)
        m = self.mid(it)
        word = self.status_word(it, "playing ♪", "quiet", "muted!!", "(closed for now)")
        if it.hub and it.status == "active":
            word = "cleans up noise" if it.is_ns else "the mix!"
        text(p, m, word, F("Caveat", 15), "#d1442f" if (it.hub and not it.is_ns) or it.status == "blocked" else "#55555e", C)
        if it.status == "blocked":
            rng = Rng(seed + 11)
            pts = [QPointF(8, 20)]
            for i in range(9):
                pts.append(QPointF(16 + i * (CELL_W - 30) / 8 + rng() * 6, (b.height() - 8) if i % 2 == 0 else 14 + rng() * 6))
            path = QPainterPath(pts[0])
            for pt in pts[1:]:
                path.lineTo(pt)
            age = it.fx("block")
            if age is not None and age < 0.65:
                path = partial(path, age / 0.65, 60)
            stroke(p, path, QColor("#d1442f"), 3)
        if it.signal and not it.hub:
            wig = 5 * math.sin(self.t * 9 + it.index)
            p.save()
            p.translate(CELL_W + 6, b.height() / 2)
            p.rotate(wig)
            arcs = QPainterPath()
            arcs.moveTo(4, -8)
            arcs.quadTo(11, 0, 4, 8)
            arcs.moveTo(11, -14)
            arcs.quadTo(22, 0, 11, 14)
            stroke(p, arcs, QColor("#2a7a3a"), 2.1)
            p.restore()
            u = (self.t * 0.5 + (seed % 10) / 10.0) % 1.0
            text(p, QRectF(CELL_W - 26, -12 - 26 * u, 20, 20), "♪", F("Caveat", 20, bold=True), alpha("#2a7a3a", 255 * (1 - u)), C)

    def paint_port(self, p, it, side, ch, rect, hovered):
        cx = rect.left() + 11 if side == "in" else rect.right() - 11
        cy = rect.center().y()
        seed = hseed(it.cell_id + side + ch)
        ink = QColor("#d1442f" if hovered else self.INK)
        p.setPen(QPen(ink, 1.6))
        p.setBrush(QColor(255, 255, 255, 150))
        p.drawEllipse(QPointF(cx + (seed % 3) * 0.4, cy), 8.2, 7.0)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(alpha(ink, 120), 0.9))
        p.drawEllipse(QPointF(cx - 0.8, cy + 0.6), 7.4, 7.8)
        text(p, QRectF(cx - 8, cy - 8, 16, 16), port_label(ch), F("Caveat", 14, bold=True), ink, C, fit=False)

    def paint_both(self, p, it, side, rect, hovered):
        ink = QColor("#d1442f" if hovered else self.INK)
        stroke(p, self._wobbly_rect(rect.adjusted(1, 0, -1, 0), hseed(it.cell_id + side), 1.0), alpha(ink, 200), 1.3)
        text(p, self.both_tab(side, rect), "both", F("Caveat", 13), ink, C)

    def link_path(self, a, b, key=""):
        return bezier(a, b)

    def _rough(self, path, seed, wob, n=34) -> QPainterPath:
        rng = Rng(seed)
        out = QPainterPath()
        for i, (pt, nm) in enumerate(sample(path, n)):
            j = (rng() - 0.5) * wob * 2 * math.sin(math.pi * i / n + 0.15)
            q = QPointF(pt.x() + nm.x() * j, pt.y() + nm.y() * j)
            out.moveTo(q) if i == 0 else out.lineTo(q)
        return out

    def paint_link(self, p, path, color, L):
        p.setRenderHint(QPainter.Antialiasing)
        if L.dashed:
            stroke(p, path, alpha(color, 170), 1.4, Qt.DashLine)
            return
        main, second = self.cached(L.key, path, "rough", lambda: (self._rough(path, L.seed, 1.7),
                                                                 self._rough(path, L.seed + 1, 2.6)))
        drawing = L.age is not None and L.age < 1.2
        if drawing:
            k = L.age / 1.2
            k = 2 * k * k if k < 0.5 else 1 - (-2 * k + 2) ** 2 / 2
            main = partial(main, k, 40)
        if L.selected:
            stroke(p, main, QColor(255, 233, 138, 220), 11)
        if L.kind == "bypass" and L.live and not L.blocked:
            stroke(p, main, QColor(123, 224, 123, 110), 11)        # highlighter
        style = Qt.SolidLine if L.live and not L.blocked else Qt.DashLine
        stroke(p, main, color, 2.3, style)
        if not drawing:
            if style == Qt.SolidLine:
                stroke(p, second, alpha(color, 150), 1.0)
            p.setPen(QPen(QColor(color), 2.3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            arrow_head(p, path, color, 11, filled=False)
        else:                                                       # the pencil rides the tip
            tip = main.pointAtPercent(1.0)
            p.save()
            p.translate(tip)
            p.rotate(28)
            p.setPen(QPen(QColor(self.INK), 1.2))
            p.setBrush(QColor("#f2c14e"))
            p.drawPolygon(QPolygonF([QPointF(0, 0), QPointF(-5, -14), QPointF(-2, -44), QPointF(6, -44), QPointF(9, -14)]))
            p.setBrush(QColor("#e8d3a8"))
            p.drawPolygon(QPolygonF([QPointF(0, 0), QPointF(-5, -14), QPointF(9, -14)]))
            p.setBrush(QColor("#f08a8a"))
            p.drawRect(QRectF(-2, -51, 8, 7))
            p.restore()
            p.setBrush(Qt.NoBrush)
        if L.blocked:
            mid = path.pointAtPercent(0.5)
            x = QPainterPath()
            x.moveTo(mid.x() - 8, mid.y() - 7)
            x.lineTo(mid.x() + 7, mid.y() + 8)
            x.moveTo(mid.x() - 7, mid.y() + 8)
            x.lineTo(mid.x() + 8, mid.y() - 8)
            stroke(p, x, QColor("#d1442f"), 3)
        elif L.label:
            _tag(p, path, L.label, F("Caveat", 15, bold=True), "#fffdf6", color, 6, color)


# =====================================================================================
# 10 · ARCADE
# =====================================================================================

class ArcadeSkin(Skin):
    key, name = "arcade", "Arcade"
    blurb = "8-bit cabinet: pixel sprites, a score HUD, chase-light links."
    animated = True
    ui_font = ("Share Tech Mono", 10)
    style_args = dict(window="#070712", base="#0b0b1c", surface="#11112a", accent="#1c1c40", secondary="#ff3df0",
                      border="#3df5ff", outline="#ffe03d", text="#f4f4ff", corner_radius=0)
    canvas = {"link_in": "#3df5ff", "link_out": "#ff3df0", "link_bypass": "#3dff6e", "link_direct": "#ffe03d",
              "link_pending": "#4a4a6a", "link_blocked": "#ff3d3d", "closed": "#6a6a80", "fg": "#ffe03d",
              "link_width": 6.0}
    PX = "Press Start 2P"
    NEON = ["#3df5ff", "#b98bff", "#3dff6e", "#ff8a3d", "#ff3df0", "#7aa2ff"]
    SPRITES = {
        "note": ["..XXXX.", "..X..X.", "..X..X.", "..X..X.", "XXX.XXX", "XXX.XXX", ".X...X."],
        "spk": ["...X...", "..XX.X.", "XXXX..X", "XXXX..X", "XXXX..X", "..XX.X.", "...X..."],
        "core": ["..XXX..", ".X.X.X.", "XXXXXXX", "X.XXX.X", "XXXXXXX", ".X.X.X.", "..XXX.."],
        "mic": ["..XXX..", "..XXX..", "..XXX..", "X.XXX.X", "X.....X", ".XXXXX.", "...X..."],
    }

    def __init__(self):
        super().__init__()
        self.title_font = F(self.PX, 11)

    def box_title(self, label, index):
        return f"STAGE {index + 1}: {label.upper()}"

    def tint(self, label):
        return QColor(self.NEON[hseed(label) % len(self.NEON)])

    def paint_background(self, p, rect, view):
        p.fillRect(rect, QColor("#070712"))
        cell = 64
        if (rect.width() / cell) * (rect.height() / cell) > 6000:
            return
        p.setPen(Qt.NoPen)
        tw = int(self.t * 2)
        gx0, gy0 = math.floor(rect.left() / cell), math.floor(rect.top() / cell)
        for gx in range(gx0, int(rect.right() / cell) + 1):
            for gy in range(gy0, int(rect.bottom() / cell) + 1):
                h = _hash2(gx, gy)
                if h % 3 == 0:
                    a = 90 if (h + tw) % 4 else 200
                    p.fillRect(QRectF(gx * cell + (h >> 4) % 15 * 4, gy * cell + (h >> 8) % 15 * 4, 4, 4), QColor(255, 255, 255, a))

    def paint_foreground(self, p, w, h, view):
        ed = view.editor
        n = ed.box_link_count(view.box)
        f = F(self.PX, 10)
        text(p, QRectF(12, 8, 40, 16), "1UP", f, "#ff3d3d", fit=False)
        text(p, QRectF(56, 8, 90, 16), f"{420 + n * 100:06d}", f, "#ffffff", fit=False)
        if w > 330:
            text(p, QRectF(170, 8, 150, 16), f"LINKS {n:02d}", f, "#ffe03d", fit=False)
        if int(self.t * 1.6) % 2 == 0 and w > 300:
            text(p, QRectF(0, h - 22, w, 14), "PRESS F2 TO ADD A CELL", F(self.PX, 8), "#5a5a7a", C, fit=False)

    @staticmethod
    def _stepped(x, y, w, h, s=6.0) -> QPainterPath:
        path = QPainterPath()
        pts = [(x + s, y), (x + w - s, y), (x + w - s, y + s / 2), (x + w - s / 2, y + s / 2), (x + w - s / 2, y + s),
               (x + w, y + s), (x + w, y + h - s), (x + w - s / 2, y + h - s), (x + w - s / 2, y + h - s / 2),
               (x + w - s, y + h - s / 2), (x + w - s, y + h), (x + s, y + h), (x + s, y + h - s / 2),
               (x + s / 2, y + h - s / 2), (x + s / 2, y + h - s), (x, y + h - s), (x, y + s), (x + s / 2, y + s),
               (x + s / 2, y + s / 2), (x + s, y + s / 2)]
        path.moveTo(*pts[0])
        for pt in pts[1:]:
            path.lineTo(*pt)
        path.closeSubpath()
        return path

    def _sprite(self, p, name, x, y, color, s=3.0):
        p.setPen(Qt.NoPen)
        for j, row in enumerate(self.SPRITES[name]):
            for i, ch in enumerate(row):
                if ch == "X":
                    p.fillRect(QRectF(x + i * s, y + j * s, s, s), QColor(color))

    def paint_cell(self, p, it):
        age = it.fx("block")
        p.save()
        p.setRenderHint(QPainter.Antialiasing, False)
        if age is not None and age < 0.42:                         # hit: shake, on a stepped clock
            dx, dy = ((-6, 0), (6, -3), (-5, 3), (4, 0), (0, 0))[min(4, int(age / 0.085))]
            p.translate(dx, dy)
        super().paint_cell(p, it)
        p.restore()

    def paint_body(self, p, it):
        p.setRenderHint(QPainter.Antialiasing, False)
        b = self.body(it)
        closed = it.status == "closed"
        col = QColor(it.color) if it.color else (QColor("#ffe03d") if it.hub else self.tint(it.title))
        if closed:
            col = QColor("#6a6a80")
        if it.status == "blocked":
            col = QColor("#ff3d3d")
        path = self._stepped(0, 0, CELL_W, b.height())
        p.fillPath(path, QColor("#11112a"))
        pen = QPen(col, 3, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin)
        if it.ghost:
            pen.setStyle(Qt.DotLine)
        p.strokePath(path, pen)
        if it.selected:
            p.strokePath(self._stepped(-5, -5, CELL_W + 10, b.height() + 10), QPen(QColor("#ffffff"), 2))
        f = F(self.PX, 7)
        badge = self.badges(it, "♪", "^", "E")
        bw = len(badge) * 7 + 6 if badge else 0
        text(p, QRectF(10, 3, CELL_W - 20 - bw, HEADER_H - 4), it.title.upper(), f, "#ffffff")
        if badge:
            text(p, QRectF(CELL_W - bw - 8, 3, bw, HEADER_H - 4), badge, f, col, R_ | V, fit=False)
        m = self.mid(it)
        name = "core" if it.hub else "spk" if it.speaker else "mic" if (it.box == "mic" and it.outs) else "note"
        bob = -3 if (it.signal and int(self.t * 4) % 2 == 0) else 0
        sx, sy = m.left() + 2, m.center().y() - 10 + bob
        self._sprite(p, name, sx, sy, col)
        word = self.status_word(it, "LV3 ON", "IDLE", "STUN", "ZZZ")
        text(p, QRectF(sx + 26, m.top(), m.width() - 28, m.height()), word, F(self.PX, 6), col, fit=False)
        if it.status == "blocked":
            p.setPen(Qt.NoPen)
            for i in range(7):
                p.fillRect(QRectF(sx + i * 3, sy + i * 3, 4, 4), QColor("#ffffff"))
                p.fillRect(QRectF(sx + 18 - i * 3, sy + i * 3, 4, 4), QColor("#ffffff"))
            if int(self.t * 4) % 2 == 0 and not it.volume_text:
                text(p, QRectF(0, -14, CELL_W, 12), "BLOCKED!", F(self.PX, 7), "#ff3d3d", C, fit=False)
        if it.signal and not it.hub:
            step = int(((self.t * 0.7 + it.index * 0.3) % 1.0) * 7)
            if step < 6:
                self._sprite(p, "note", CELL_W - 26, -8 - step * 5, col, 2.0)

    def paint_port(self, p, it, side, ch, rect, hovered):
        r = QRectF(rect.left() + (2 if side == "in" else PORT_W - 22), rect.top() + 1, 20, rect.height() - 2)
        col = QColor("#ffe03d") if hovered else QColor("#3df5ff" if side == "in" else "#ff3df0")
        p.fillRect(r, QColor("#070712"))
        p.setPen(QPen(col, 2, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin))
        p.drawRect(r)
        text(p, r, port_label(ch), F(self.PX, 6), col, C, fit=False)

    def paint_both(self, p, it, side, rect, hovered):
        col = QColor("#ffe03d") if hovered else QColor("#4a4a6a")
        p.setPen(QPen(col, 2, Qt.DotLine))
        p.drawRect(rect.adjusted(1, 0, -1, 0))
        text(p, self.both_tab(side, rect), "2P", F(self.PX, 6), col, C, fit=False)

    def paint_link(self, p, path, color, L):
        p.setRenderHint(QPainter.Antialiasing, False)
        length = max(1.0, path.length())
        if L.dashed:
            n = max(2, int(length / 22))
            for i in range(1, n):
                pt = path.pointAtPercent(i / n)
                p.fillRect(QRectF(round(pt.x() / 2) * 2 - 2, round(pt.y() / 2) * 2 - 2, 4, 4), alpha(color, 150))
            p.setRenderHint(QPainter.Antialiasing)
            return
        n = max(2, int(length / 14))
        shown = n if L.age is None else min(n, int(L.age / 0.045))
        chase = int(self.t * 9)
        dots = self.cached(L.key, path, "dots", lambda: [path.pointAtPercent(i / n) for i in range(n)])
        for i in range(1, min(shown, len(dots))):
            pt = dots[i]
            x, y = round(pt.x() / 2) * 2 - 3, round(pt.y() / 2) * 2 - 3
            if L.blocked:
                if i % 2:
                    continue
                c = QColor(color)
            elif not L.live:
                c = alpha(color, 255)
            elif L.signal:
                c = QColor("#ffffff") if (i - chase) % 6 == 0 else QColor(color)
            else:
                c = alpha(color, 150)
            if L.selected:
                p.fillRect(QRectF(x - 2, y - 2, 10, 10), QColor("#ffffff"))
            p.fillRect(QRectF(x, y, 6, 6), c)
        if L.age is not None and L.age < 1.4:
            mid = path.pointAtPercent(0.5)
            rise = int(L.age / 0.1) * 4
            text(p, QRectF(mid.x() - 60, mid.y() - 26 - rise, 120, 14), "+1 LINK!", F(self.PX, 9), "#ffe03d", C, fit=False)
        if L.label:
            _tag(p, path, L.label, F(self.PX, 7), "#070712", color, 0, color)
        p.setRenderHint(QPainter.Antialiasing)

    def eq_colors(self):
        return {"bg": QColor("#070712"), "grid": QColor(61, 245, 255, 40), "text": QColor("#3df5ff"),
                "curve": QColor("#ffe03d"), "fill": QColor(255, 224, 61, 36), "spectrum": QColor(255, 61, 240, 90),
                "handle": QColor("#ffffff"), "handle_sel": QColor("#3dff6e"), "band": QColor(61, 245, 255, 70)}


# =====================================================================================
# registry
# =====================================================================================

SKINS = {cls.key: cls for cls in (Skin, RackSkin, ScopeSkin, MetroSkin, SchematicSkin, TuiSkin, GlassSkin, SwissSkin,
                                  DeskSkin, SketchSkin, ArcadeSkin)}
ORDER = ["studio", "rack", "scope", "metro", "schematic", "tui", "glass", "swiss", "desk", "sketch", "arcade"]

_current: "Skin | None" = None


def current() -> Skin:
    global _current
    if _current is None:
        _current = Skin()
    return _current


def activate(key: str, app=None) -> Skin:
    """Make a skin current: kit style + palette + UI font.  Build (or rebuild) windows afterwards."""
    global _current
    import qt_ui_kit as kit
    from PySide6.QtGui import QFont as _QFont
    from PySide6.QtWidgets import QApplication
    load_fonts()
    _current = SKINS.get(key, Skin)()
    kit.set_style(_current.style())
    app = app or QApplication.instance()
    if app is not None:
        kit.apply_palette(app)
        default = getattr(activate, "_default_font", None)
        if default is None:
            default = _QFont(app.font())
            activate._default_font = default
        if _current.ui_font:
            f = _QFont(default)
            f.setFamilies([_current.ui_font[0]] + default.families())
            f.setPointSize(_current.ui_font[1])
            app.setFont(f)
        else:
            app.setFont(default)
    return _current
