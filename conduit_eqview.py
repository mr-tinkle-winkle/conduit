"""
conduit_eqview.py -- the equaliser window of one Conduit device (hub).

A real response graph, not a row of sliders:
  - log frequency axis (20 Hz .. 20 kHz), gain in dB; the thick curve is what the
    whole EQ does, the faint ones are the individual bands
  - every band is a handle: drag = frequency / gain, mouse wheel = Q (width),
    double-click empty space = new band, double-click a handle = bypass it,
    right-click = type / remove, Delete = remove
  - behind the curve, the live spectrum of what is coming out of the device
    right now (after the EQ), from a small capture stream that only exists
    while this window is open.  Two kinds of smoothing, both adjustable:
    across frequency ("Smoothing") and over time ("Time smoothing": no big
    jumps from one frame to the next).  Where either is reshaping the
    spectrum it is tinted in that smoothing's colour, by how much.
  - new devices start with flat points on the areas worth tuning (voice
    points on microphones, audio points elsewhere); "Lock X" (or Shift)
    keeps a dragged handle on its frequency
  - Listen plays the device through your speakers while you tune it

Edits go through the controller like any other edit (so Auto-apply / Save both
work); the daemon pushes frequency / gain / Q changes into the running filter
live, so dragging a handle is audible as you drag.
"""
from __future__ import annotations

import math
import struct

from PySide6.QtCore import QPointF, QProcess, QRectF, Qt, QTimer, Signal, QObject
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

import qt_ui_kit as kit

import conduit_core as core
import conduit_eq as eqm
import conduit_listen as listen
import conduit_skins as skins

try:
    import numpy as _np
except Exception:  # noqa: BLE001 -- numpy is optional: without it a small pure-Python FFT is used
    _np = None

RATE = 48000
N_BINS = 72
FFT_N = 2048 if _np is not None else 512
DB_FLOOR = -84.0


def _fft_mag(samples):
    """Magnitudes of a Hann-windowed real FFT (len(samples) must be a power of two)."""
    n = len(samples)
    if _np is not None:
        x = _np.asarray(samples, dtype=_np.float32) * _np.hanning(n)
        return (_np.abs(_np.fft.rfft(x)) / (n / 4)).tolist()
    x = [samples[i] * (0.5 - 0.5 * math.cos(2 * math.pi * i / (n - 1))) for i in range(n)]
    j = 0
    a = [complex(v) for v in x]
    for i in range(1, n):                       # bit-reversal permutation
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j ^= bit
        if i < j:
            a[i], a[j] = a[j], a[i]
    length = 2
    while length <= n:
        w = complex(math.cos(-2 * math.pi / length), math.sin(-2 * math.pi / length))
        for i in range(0, n, length):
            wn = 1 + 0j
            half = length >> 1
            for k in range(i, i + half):
                u, v = a[k], a[k + half] * wn
                a[k], a[k + half] = u + v, u - v
                wn *= w
        length <<= 1
    return [abs(v) / (n / 4) for v in a[:n // 2 + 1]]


class SpectrumTap(QObject):
    """Captures a node's audio with pw-record (or parec) and turns it into log-spaced dB bins."""
    updated = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.bins = [DB_FLOOR] * N_BINS
        self.status = "off"                # off | starting | live | unavailable
        self._buf = bytearray()
        self._proc = None
        self._cmds = []
        self._target = None
        self._got_data = False
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._analyse)
        edges = eqm.log_freqs(N_BINS + 1)
        self._ranges = []
        for i in range(N_BINS):
            lo = edges[i] * FFT_N / RATE
            hi = edges[i + 1] * FFT_N / RATE
            self._ranges.append((lo, hi))

    def start(self, node_name: str, is_sink: bool):
        if self._target == (node_name, is_sink) and self._proc is not None:
            return
        self.stop()
        self._target = (node_name, is_sink)
        props = ('{ node.name = "conduit_spectrum" node.description = "Conduit Analyser" '
                 'application.name = "Conduit Analyser" node.dont-reconnect = true '
                 f'stream.capture.sink = {"true" if is_sink else "false"} }}')
        self._cmds = [
            ["pw-record", "--target", node_name, "--rate", str(RATE), "--channels", "1", "--format", "f32",
             "--latency", "20ms", "--raw", "-P", props, "-"],
            ["parec", f"--device={node_name}{'.monitor' if is_sink else ''}", "--format=float32le",
             f"--rate={RATE}", "--channels=1", "--raw", "--latency-msec=30",
             "--client-name=Conduit Analyser", "--property=node.name=conduit_spectrum"],
        ]
        self.status = "starting"
        self._launch()
        self._timer.start()

    def _launch(self):
        if not self._cmds:
            self.status = "unavailable"
            self._proc = None
            self.updated.emit()
            return
        cmd = self._cmds.pop(0)
        self._got_data = False
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.SeparateChannels)
        proc.readyReadStandardOutput.connect(self._read)
        proc.finished.connect(lambda *_a, p=proc: self._ended(p))
        proc.errorOccurred.connect(lambda *_a, p=proc: self._ended(p))
        self._proc = proc
        proc.start(cmd[0], cmd[1:])

    def _ended(self, proc):
        if proc is not self._proc:
            return
        self._proc = None
        proc.deleteLater()
        if self._target is not None and not self._got_data:
            self._launch()                 # this tool didn't work here: try the next one
        elif self._target is not None:
            self.status = "off"
            self.updated.emit()

    def _read(self):
        if self._proc is None:
            return
        data = bytes(self._proc.readAllStandardOutput())
        if data:
            self._got_data = True
            self.status = "live"
            self._buf += data
            keep = FFT_N * 4 * 2
            if len(self._buf) > keep:
                del self._buf[:len(self._buf) - keep - (len(self._buf) - keep) % 4]

    def stop(self):
        self._timer.stop()
        self._target = None
        proc, self._proc = self._proc, None
        if proc is not None:
            proc.kill()
            proc.waitForFinished(300)
            proc.deleteLater()
        self._buf.clear()
        self.status = "off"
        self.bins = [DB_FLOOR] * N_BINS

    def feed(self, samples):
        """Tests / demos: push float samples directly."""
        self._buf += struct.pack(f"<{len(samples)}f", *samples)
        self.status = "live"

    def _analyse(self):
        n = FFT_N
        usable = len(self._buf) - len(self._buf) % 4
        if usable < n * 4:
            decayed = [max(DB_FLOOR, b - 3.0) for b in self.bins]
            if decayed != self.bins:
                self.bins = decayed
                self.updated.emit()
            return
        chunk = bytes(self._buf[usable - n * 4:usable])
        if _np is not None:
            samples = _np.frombuffer(chunk, dtype="<f4")
        else:
            samples = struct.unpack(f"<{n}f", chunk)
        mag = _fft_mag(samples)
        top = len(mag) - 1
        out = []
        for i, (lo, hi) in enumerate(self._ranges):
            a, b = int(lo), min(top, int(math.ceil(hi)))
            if b <= a:
                frac = lo - a                      # below the FFT's resolution: interpolate
                v = mag[min(a, top)] * (1 - frac) + mag[min(a + 1, top)] * frac
            else:
                v = max(mag[a:b + 1])
            db = 20 * math.log10(v) if v > 1e-7 else DB_FLOOR
            out.append(max(DB_FLOOR, db))          # raw: SpectrumSmoother does attack / release
        self.bins = out
        self.updated.emit()


class SpectrumSmoother:
    """Turns raw spectrum frames into what is drawn.

    time:   each bin eases towards the new frame and may only move so far per frame
            (amount 0 = raw, 1 = slow and calm); off = raw frames
    visual: a gaussian across neighbouring bins (amount = width); off = the bins as measured
    process() returns (shown bins, time intensity per bin, visual intensity per bin), the
    intensities 0..1 being how much each smoothing is changing that bin right now."""

    TIME_FULL_DB = 18.0       # holding a bin this far from the raw frame = full time tint
    VISUAL_FULL_DB = 10.0

    def __init__(self):
        self.time_on, self.time = True, 0.5
        self.visual_on, self.visual = True, 0.5
        self._state = None
        self._kernels = {}

    def reset(self):
        self._state = None

    def _kernel(self, amount):
        key = round(amount, 3)
        k = self._kernels.get(key)
        if k is None:
            sigma = 0.35 + 3.4 * amount
            rad = max(1, int(math.ceil(2.5 * sigma)))
            k = [math.exp(-(d * d) / (2 * sigma * sigma)) for d in range(-rad, rad + 1)]
            self._kernels[key] = k
        return k

    def process(self, raw):
        n = len(raw)
        if self._state is None or len(self._state) != n:
            self._state = list(raw)
        t_int = [0.0] * n
        if self.time_on and self.time > 0:
            keep = 0.88 * self.time                  # how much of the last frame each frame keeps
            up = 60.0 - 54.0 * self.time             # max rise per frame (dB)
            down = 30.0 - 27.5 * self.time           # max fall per frame (dB)
            out = []
            for i, (r, prev) in enumerate(zip(raw, self._state)):
                step = (r - prev) * (1.0 - keep)
                v = prev + max(-down, min(up, step))
                out.append(v)
                t_int[i] = min(1.0, abs(r - v) / self.TIME_FULL_DB)
        else:
            out = list(raw)
        self._state = out
        v_int = [0.0] * n
        shown = out
        if self.visual_on and self.visual > 0:
            k = self._kernel(self.visual)
            rad = len(k) // 2
            shown = []
            for i in range(n):
                acc = wsum = 0.0
                for j, w in enumerate(k):
                    x = i + j - rad
                    if 0 <= x < n:
                        acc += out[x] * w
                        wsum += w
                v = acc / wsum
                shown.append(v)
                v_int[i] = min(1.0, abs(v - out[i]) / self.VISUAL_FULL_DB)
        return shown, t_int, v_int


def eq_palette() -> dict:
    """The skin's EQ colours plus the two smoothing tints (derived when a skin has none):
    time smoothing and visual smoothing get hues well away from the spectrum's and each other."""
    col = dict(skins.current().eq_colors())
    base = QColor(col["spectrum"])
    h = base.hslHueF() if base.hslHueF() >= 0 else 0.33
    for key, turn in (("smooth_time", 0.36), ("smooth_visual", 0.64)):
        if key not in col:
            c = QColor.fromHslF((h + turn) % 1.0, 0.85, 0.62)
            c.setAlpha(max(110, base.alpha()))
            col[key] = c
    return col


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(int(a.red() + (b.red() - a.red()) * t), int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t), int(a.alpha() + (b.alpha() - a.alpha()) * t))


class EqGraph(QWidget):
    edited = Signal()                 # the eq dict was changed in place
    selection_changed = Signal(int)

    ML, MR, MT, MB = 38, 12, 12, 22

    def __init__(self, get_eq, parent=None):
        super().__init__(parent)
        self.get_eq = get_eq
        self.selected = -1
        self.spectrum = None           # list of dB bins, or None
        self.tint_time = None          # per bin 0..1: how hard time smoothing is working there
        self.tint_visual = None        # per bin 0..1: same for visual smoothing
        self.smooth_curve = True       # draw the spectrum as a curve rather than steps
        self.legend = ()               # (label, colour) of the active smoothing tints
        self.lock_x = False            # dragging a handle keeps its frequency
        self._drag = None
        self._freqs = eqm.log_freqs(220)
        self.setMinimumSize(660, 300)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.range_db = 18.0

    # -- mapping
    def plot(self) -> QRectF:
        return QRectF(self.ML, self.MT, self.width() - self.ML - self.MR, self.height() - self.MT - self.MB)

    def fx(self, f):
        r = self.plot()
        return r.left() + r.width() * math.log(f / eqm.FREQ_MIN) / math.log(eqm.FREQ_MAX / eqm.FREQ_MIN)

    def xf(self, x):
        r = self.plot()
        u = max(0.0, min(1.0, (x - r.left()) / r.width()))
        return eqm.FREQ_MIN * (eqm.FREQ_MAX / eqm.FREQ_MIN) ** u

    def gy(self, db):
        r = self.plot()
        return r.center().y() - db / self.range_db * r.height() / 2

    def yg(self, y):
        r = self.plot()
        return max(-self.range_db, min(self.range_db, (r.center().y() - y) / (r.height() / 2) * self.range_db))

    def handle_pos(self, band) -> QPointF:
        g = band["gain"] if band["type"] in eqm.USES_GAIN else 0.0
        return QPointF(self.fx(band["freq"]), self.gy(max(-self.range_db, min(self.range_db, g))))

    def band_at(self, pos) -> int:
        best, bd = -1, 13.0 ** 2
        for i, b in enumerate(self.get_eq()["bands"]):
            h = self.handle_pos(b)
            d = (h.x() - pos.x()) ** 2 + (h.y() - pos.y()) ** 2
            if d < bd:
                best, bd = i, d
        return best

    def select(self, i):
        if i != self.selected:
            self.selected = i
            self.selection_changed.emit(i)
            self.update()

    # -- paint
    def paintEvent(self, e):
        eq = self.get_eq()
        col = eq_palette()
        th = kit.Theme()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        full = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.fillPath(kit.rounded_rect_path(full, min(th.corner_radius, 10)), col["bg"])
        r = self.plot()
        peak = max([abs(b["gain"]) for b in eq["bands"]] + [abs(eq.get("preamp", 0.0)), 0.0])
        self.range_db = 24.0 if peak > 17 else 18.0
        f = QFont(self.font())
        f.setPixelSize(10)
        p.setFont(f)
        for hz, label in ((20, "20"), (50, "50"), (100, "100"), (200, "200"), (500, "500"), (1000, "1k"),
                          (2000, "2k"), (5000, "5k"), (10000, "10k"), (20000, "20k")):
            x = self.fx(hz)
            p.setPen(QPen(col["grid"], 1))
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            p.setPen(col["text"])
            p.drawText(QRectF(x - 20, r.bottom() + 3, 40, 14), Qt.AlignCenter, label)
        step = 6
        db = -int(self.range_db)
        while db <= self.range_db:
            y = self.gy(db)
            p.setPen(QPen(col["grid"], 1.6 if db == 0 else 1))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(col["text"])
            p.drawText(QRectF(0, y - 7, self.ML - 6, 14), Qt.AlignRight | Qt.AlignVCenter, f"{db:+d}" if db else "0")
            db += step
        p.save()
        p.setClipRect(r)
        # live spectrum
        if self.spectrum:
            self._paint_spectrum(p, r, col)
        enabled = eq.get("enabled", False)
        # single bands
        for i, b in enumerate(eq["bands"]):
            if not b.get("enabled", True):
                continue
            path = QPainterPath()
            for k, fr in enumerate(self._freqs):
                pt = QPointF(self.fx(fr), self.gy(eqm.band_db(b, fr)))
                path.moveTo(pt) if k == 0 else path.lineTo(pt)
            c = QColor(col["handle_sel"]) if i == self.selected else QColor(col["band"])
            if i == self.selected:
                c.setAlpha(170)
            p.setPen(QPen(c, 1.2))
            p.drawPath(path)
        # total
        resp = eqm.response_db(eq, self._freqs)
        path = QPainterPath()
        for k, (fr, v) in enumerate(zip(self._freqs, resp)):
            pt = QPointF(self.fx(fr), self.gy(v))
            path.moveTo(pt) if k == 0 else path.lineTo(pt)
        fill = QPainterPath(path)
        fill.lineTo(r.right(), self.gy(0))
        fill.lineTo(r.left(), self.gy(0))
        fill.closeSubpath()
        if enabled:
            p.fillPath(fill, col["fill"])
        pen = QPen(col["curve"], 2.6, Qt.SolidLine if enabled else Qt.DashLine, Qt.RoundCap, Qt.RoundJoin)
        if not enabled:
            c = QColor(col["curve"])
            c.setAlpha(120)
            pen.setColor(c)
        p.setPen(pen)
        p.drawPath(path)
        p.restore()
        # handles
        hf = QFont(self.font())
        hf.setPixelSize(10)
        hf.setBold(True)
        p.setFont(hf)
        for i, b in enumerate(eq["bands"]):
            h = self.handle_pos(b)
            sel = i == self.selected
            c = QColor(col["handle_sel"] if sel else col["handle"])
            rad = 10 if sel else 8.5
            p.setPen(QPen(c, 2))
            p.setBrush(c if b.get("enabled", True) else col["bg"])
            p.drawEllipse(h, rad, rad)
            p.setPen(kit.contrast_text(c) if b.get("enabled", True) else c)
            p.drawText(QRectF(h.x() - rad, h.y() - rad, 2 * rad, 2 * rad), Qt.AlignCenter, str(i + 1))
            if b.get("label"):
                lc = QColor(col["handle_sel"] if sel else col["text"])
                p.setPen(lc)
                above = h.y() - rad - 16 > r.top()
                lr = QRectF(h.x() - 50, h.y() - rad - 15 if above else h.y() + rad + 1, 100, 14)
                p.drawText(lr, Qt.AlignCenter, b["label"])
        p.setBrush(Qt.NoBrush)
        if self.legend:
            p.setFont(f)
            x = r.right() - 8
            for label, c in reversed(self.legend):
                w = p.fontMetrics().horizontalAdvance(label)
                x -= w
                p.setPen(col["text"])
                p.drawText(QRectF(x, r.top() + 4, w, 14), Qt.AlignLeft | Qt.AlignVCenter, label)
                sw = QColor(c)
                sw.setAlpha(255)
                p.fillRect(QRectF(x - 13, r.top() + 7, 9, 9), sw)
                x -= 24
        if not eq["bands"]:
            p.setPen(col["text"])
            p.drawText(r, Qt.AlignCenter, "Double-click to add a band")

    def _paint_spectrum(self, p, r, col):
        bins = self.spectrum
        n = len(bins)
        pts = []
        for i, v in enumerate(bins):
            level = max(0.0, min(1.0, (v - DB_FLOOR) / (0 - DB_FLOOR)))
            pts.append(QPointF(r.left() + (i + 0.5) / n * r.width(), r.bottom() - level * r.height()))
        edge = QPainterPath(QPointF(r.left(), pts[0].y()))
        if self.smooth_curve:
            edge.lineTo(pts[0])
            for a, b in zip(pts, pts[1:]):                       # midpoint quadratic: smooth, no overshoot
                mid = QPointF((a.x() + b.x()) / 2, (a.y() + b.y()) / 2)
                edge.quadTo(a, mid)
            edge.lineTo(pts[-1])
        else:
            for pt in pts:
                edge.lineTo(pt)
        edge.lineTo(QPointF(r.right(), pts[-1].y()))
        area = QPainterPath(edge)
        area.lineTo(r.right(), r.bottom())
        area.lineTo(r.left(), r.bottom())
        area.closeSubpath()
        base = QColor(col["spectrum"])
        tt, tv = self.tint_time, self.tint_visual
        if tt or tv:
            ct, cv = QColor(col["smooth_time"]), QColor(col["smooth_visual"])
            grad = QLinearGradient(r.left(), 0, r.right(), 0)
            line = QLinearGradient(r.left(), 0, r.right(), 0)
            for i in range(n):
                a = tt[i] if tt else 0.0
                b = tv[i] if tv else 0.0
                c = base
                if a + b > 0:
                    c = _mix(base, _mix(ct, cv, b / (a + b)), min(1.0, a + b))
                u = (i + 0.5) / n
                grad.setColorAt(u, c)
                lc = QColor(c)
                lc.setAlpha(min(255, c.alpha() + 110))
                line.setColorAt(u, lc)
            p.fillPath(area, grad)
            p.setPen(QPen(line, 1.4))
        else:
            p.fillPath(area, base)
            lc = QColor(base)
            lc.setAlpha(min(255, base.alpha() + 110))
            p.setPen(QPen(lc, 1.4))
        p.drawPath(edge)

    # -- input
    def mousePressEvent(self, e):
        self.setFocus()
        i = self.band_at(e.position())
        if e.button() == Qt.LeftButton:
            self.select(i)
            if i >= 0:
                self._drag = i
            e.accept()
        elif e.button() == Qt.RightButton and i >= 0:
            self.select(i)
            self._menu(i, e.globalPosition().toPoint())
            e.accept()

    def mouseMoveEvent(self, e):
        if self._drag is None:
            over = self.band_at(e.position()) >= 0
            locked = self.lock_x or e.modifiers() & Qt.ShiftModifier
            self.setCursor((Qt.SizeVerCursor if locked else Qt.PointingHandCursor) if over else Qt.ArrowCursor)
            return
        bands = self.get_eq()["bands"]
        if self._drag >= len(bands):
            self._drag = None
            return
        b = bands[self._drag]
        if not (self.lock_x or e.modifiers() & Qt.ShiftModifier):
            b["freq"] = round(self.xf(e.position().x()), 1)
        if b["type"] in eqm.USES_GAIN:
            b["gain"] = round(self.yg(e.position().y()) * 2) / 2 if e.modifiers() & Qt.ControlModifier \
                else round(self.yg(e.position().y()), 1)
        self.edited.emit()
        self.update()

    def mouseReleaseEvent(self, e):
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        eq = self.get_eq()
        i = self.band_at(e.position())
        if i >= 0:
            eq["bands"][i]["enabled"] = not eq["bands"][i].get("enabled", True)
        elif self.plot().contains(e.position()) and len(eq["bands"]) < eqm.MAX_BANDS:
            eq["bands"].append({"type": "peaking", "freq": round(self.xf(e.position().x()), 1), "q": 1.0,
                                "gain": round(self.yg(e.position().y()), 1), "enabled": True})
            eq["enabled"] = True
            self.select(len(eq["bands"]) - 1)
        self.edited.emit()
        self.update()

    def wheelEvent(self, e):
        eq = self.get_eq()
        i = self.band_at(e.position())
        if i < 0:
            i = self.selected
        if 0 <= i < len(eq["bands"]):
            b = eq["bands"][i]
            b["q"] = round(max(eqm.Q_MIN, min(eqm.Q_MAX, b["q"] * (1.12 ** (e.angleDelta().y() / 120.0)))), 3)
            self.select(i)
            self.edited.emit()
            self.update()
        e.accept()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.selected >= 0:
            self.remove(self.selected)
        else:
            super().keyPressEvent(e)

    def remove(self, i):
        bands = self.get_eq()["bands"]
        if 0 <= i < len(bands):
            del bands[i]
            self.selected = -1
            self.selection_changed.emit(-1)
            self.edited.emit()
            self.update()

    def _menu(self, i, gpos):
        menu = kit.themed_menu("", self)
        for key, label in eqm.TYPE_LABELS.items():
            a = menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(self.get_eq()["bands"][i]["type"] == key)
            a.triggered.connect(lambda _=False, k=key: self._set_type(i, k))
        menu.addSeparator()
        menu.addAction("Remove band", lambda: self.remove(i))
        menu.exec(gpos)

    def _set_type(self, i, key):
        bands = self.get_eq()["bands"]
        if 0 <= i < len(bands):
            bands[i]["type"] = key
            if key not in eqm.USES_GAIN:
                bands[i]["gain"] = 0.0
            self.edited.emit()
            self.selection_changed.emit(i)
            self.update()


class EqDialog(kit.ThemedDialog):
    def __init__(self, controller, box: str, parent=None, live: bool = True):
        super().__init__(f"Equaliser — {core.box_label(controller.state, box)}", parent)
        self.c, self.box = controller, box
        self.setModal(False)
        self._live = live
        self._commit = QTimer(self)
        self._commit.setSingleShot(True)
        self._commit.setInterval(60)
        self._commit.timeout.connect(self._do_commit)
        self._loading = False

        top = QHBoxLayout()
        top.setSpacing(10)
        self.enabled = kit.CustomCheckBox("Enabled")
        self.enabled.toggled.connect(self._toggle)
        top.addWidget(self.enabled)
        top.addSpacing(8)
        top.addWidget(QLabel("Preset"))
        self.preset = kit.CustomComboBox()
        self.preset.addItem("—", None)
        for name in eqm.PRESETS:
            self.preset.addItem(name, name)
        self.preset.setMinimumWidth(170)
        self.preset.activated.connect(self._apply_preset)
        top.addWidget(self.preset)
        top.addStretch(1)
        self.listen = kit.CustomCheckBox("Listen")
        self.listen.setToolTip("Play this device through your speakers while you tune it")
        self.listen.toggled.connect(self._listen_toggled)
        top.addWidget(self.listen)
        top.addSpacing(8)
        top.addWidget(QLabel("Preamp"))
        self.preamp = self._spin(eqm.GAIN_MIN, eqm.GAIN_MAX, 0.5, 1, " dB", 96)
        self.preamp.valueChanged.connect(lambda v: self._set_root("preamp", round(v, 1)))
        top.addWidget(self.preamp)
        self.lay.addLayout(top)

        self.graph = EqGraph(self.eq)
        self.graph.edited.connect(self._edited)
        self.graph.selection_changed.connect(lambda *_: self._load_band())
        self.lay.addWidget(self.graph, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.band_label = QLabel("Band")
        self.band_label.setMinimumWidth(130)
        row.addWidget(self.band_label)
        self.btype = kit.CustomComboBox()
        for key, label in eqm.TYPE_LABELS.items():
            self.btype.addItem(label, key)
        self.btype.setMinimumWidth(120)
        self.btype.activated.connect(lambda *_: self._set_band("type", self.btype.currentData()))
        row.addWidget(self.btype)
        row.addWidget(QLabel("Freq"))
        self.freq = self._spin(eqm.FREQ_MIN, eqm.FREQ_MAX, 10, 0, " Hz", 104)
        self.freq.valueChanged.connect(lambda v: self._set_band("freq", round(v, 1)))
        row.addWidget(self.freq)
        row.addWidget(QLabel("Gain"))
        self.gain = self._spin(eqm.GAIN_MIN, eqm.GAIN_MAX, 0.5, 1, " dB", 96)
        self.gain.valueChanged.connect(lambda v: self._set_band("gain", round(v, 1)))
        row.addWidget(self.gain)
        row.addWidget(QLabel("Q"))
        self.q = self._spin(eqm.Q_MIN, eqm.Q_MAX, 0.1, 2, "", 80)
        self.q.valueChanged.connect(lambda v: self._set_band("q", round(v, 3)))
        row.addWidget(self.q)
        self.band_on = kit.CustomCheckBox("On")
        self.band_on.toggled.connect(lambda on: self._set_band("enabled", bool(on)))
        row.addWidget(self.band_on)
        self.remove = kit.CustomButton("Remove")
        self.remove.clicked.connect(lambda: self.graph.remove(self.graph.selected))
        row.addWidget(self.remove)
        row.addStretch(1)
        self.lay.addLayout(row)

        lay = controller.layout
        view = QHBoxLayout()
        view.setSpacing(8)
        self.smooth_on = kit.CustomCheckBox("Smoothing")
        self.smooth_on.setToolTip("Smooth the live spectrum across neighbouring frequencies (tinted where it reshapes it)")
        self.smooth_amt = self._spin(0, 100, 5, 0, " %", 76)
        self.time_on = kit.CustomCheckBox("Time smoothing")
        self.time_on.setToolTip("Stop the live spectrum from jumping large amounts between frames "
                                "(tinted where it is holding it back)")
        self.time_amt = self._spin(0, 100, 5, 0, " %", 76)
        self.lock_x = kit.CustomCheckBox("Lock X")
        self.lock_x.setToolTip("Dragging a handle only changes its gain, never its frequency (or hold Shift)")
        self.smooth_on.setChecked(bool(lay.ui("eq_smooth")))
        self.smooth_amt.setValue(round(float(lay.ui("eq_smooth_amount")) * 100))
        self.time_on.setChecked(bool(lay.ui("eq_time")))
        self.time_amt.setValue(round(float(lay.ui("eq_time_amount")) * 100))
        self.lock_x.setChecked(bool(lay.ui("eq_lock_x")))
        for wdg in (self.smooth_on, self.time_on, self.lock_x):
            wdg.toggled.connect(self._view_options)
        for wdg in (self.smooth_amt, self.time_amt):
            wdg.valueChanged.connect(self._view_options)
        for wdg in (self.smooth_on, self.smooth_amt):
            view.addWidget(wdg)
        view.addSpacing(10)
        for wdg in (self.time_on, self.time_amt):
            view.addWidget(wdg)
        view.addSpacing(10)
        view.addWidget(self.lock_x)
        view.addStretch(1)
        self.lay.addLayout(view)

        bottom = QHBoxLayout()
        self.hint = self.label("", dim=True)
        bottom.addWidget(self.hint, 1)
        add = kit.CustomButton("+ Band")
        add.clicked.connect(self._add_band)
        bottom.addWidget(add)
        close = kit.CustomButton("Close")
        close.setMinimumWidth(90)
        close.clicked.connect(self.close)
        bottom.addWidget(close)
        self.lay.addLayout(bottom)

        self.tap = SpectrumTap(self)
        self.tap.updated.connect(self._frame)
        self.smoother = SpectrumSmoother()
        self.listener = listen.manager(controller)
        self.listener.changed.connect(self._listen_sync)
        self.c.state_changed.connect(self._external)
        self.c.live_changed.connect(self._retarget)
        self.resize(860, 560)
        self._view_options()
        self._seed_points()
        eq = self.eq()
        # an EQ that is off and flat (e.g. fresh default points) switches on with the first edit,
        # so grabbing "Presence" and lifting it is heard straight away
        self._auto_enable = not eq.get("enabled") and not eq.get("preamp") and all(
            b["gain"] == 0 or b["type"] not in eqm.USES_GAIN for b in eq["bands"])
        self._load()
        self._listen_sync()

    def _spin(self, lo, hi, step, decimals, suffix, width):
        s = kit.CustomDoubleSpinBox()
        s.setRange(lo, hi)
        s.setSingleStep(step)
        s.setDecimals(decimals)
        s.setSuffix(suffix)
        s.setFixedWidth(width)
        return s

    # -- view options (GUI preferences, saved in layout.json)
    def _view_options(self, *_):
        lay = self.c.layout
        sm, tm = self.smooth_amt.value() / 100.0, self.time_amt.value() / 100.0
        vals = {"eq_smooth": self.smooth_on.isChecked(), "eq_smooth_amount": sm,
                "eq_time": self.time_on.isChecked(), "eq_time_amount": tm, "eq_lock_x": self.lock_x.isChecked()}
        for k, v in vals.items():
            if lay.ui(k) != v:
                lay.set_ui(k, v)
        self.smooth_amt.setEnabled(vals["eq_smooth"])
        self.time_amt.setEnabled(vals["eq_time"])
        sm_ = self.smoother
        sm_.visual_on, sm_.visual, sm_.time_on, sm_.time = vals["eq_smooth"], sm, vals["eq_time"], tm
        self.graph.lock_x = vals["eq_lock_x"]
        self.graph.smooth_curve = vals["eq_smooth"] and sm > 0
        self._spectrum()

    # -- listen
    def listen_key(self) -> str:
        return core.hub_cells_for_box(self.c.state, self.box)[0]

    def _listen_toggled(self, on):
        if not self._loading:
            self.listener.toggle(self.listen_key(), bool(on))

    def _listen_sync(self):
        key = self.listen_key() if core.hub_cells_for_box(self.c.state, self.box) else None
        ok = key is not None and core.listen_supported(self.c.state, key)
        self._loading = True
        self.listen.setChecked(bool(ok and self.listener.is_on(key)))
        self._loading = False
        self.listen.setEnabled(ok)
        if not ok:
            self.listen.setToolTip("You already hear the Virtual Speaker: it plays on your speakers")
        else:
            st = self.listener.status(key)
            self.listen.setToolTip("Play this device through your speakers while you tune it"
                                   + ("" if st == "off" else f"  ({st})"))

    # -- model
    def _seed_points(self):
        """A device's first look at its EQ: flat points on the areas worth tuning."""
        eq = self.eq()
        if eq.get("seeded"):
            return
        eq["seeded"] = True
        if not eq["bands"]:
            eq["bands"] = eqm.default_points(core.box_is_microphone(self.c.state, self.box))
        self._commit.start()

    def eq(self) -> dict:
        hub = core.hub_config(self.c.state, self.box)
        if not isinstance(hub.get("eq"), dict):
            hub["eq"] = eqm.default_eq()
        return hub["eq"]

    def _first_edit(self):
        if self._auto_enable:
            self._auto_enable = False
            self.eq()["enabled"] = True
            QTimer.singleShot(400, self._retarget)

    def _edited(self):
        self._first_edit()
        self._load_band()
        self._sync_top()
        self._commit.start()

    def _do_commit(self):
        self._own = True
        try:
            self.c.commit()
        finally:
            self._own = False

    def _external(self):
        if getattr(self, "_own", False):
            return
        if core.hub_config(self.c.state, self.box) == {}:      # the conduit was removed
            self.close()
            return
        self._load()
        self._listen_sync()

    def _set_root(self, key, value):
        if self._loading:
            return
        self.eq()[key] = value
        if key == "preamp":
            self._first_edit()
            self._sync_top()
        else:
            self._auto_enable = False
        self.graph.update()
        self._commit.start()

    def _toggle(self, on):
        self._set_root("enabled", bool(on))
        QTimer.singleShot(400, self._retarget)

    def _set_band(self, key, value):
        if self._loading:
            return
        bands = self.eq()["bands"]
        i = self.graph.selected
        if not (0 <= i < len(bands)):
            return
        bands[i][key] = value
        self._first_edit()
        self._sync_top()
        if key == "type":
            if value not in eqm.USES_GAIN:
                bands[i]["gain"] = 0.0
            self._load_band()
        self.graph.update()
        self._commit.start()

    def _add_band(self):
        eq = self.eq()
        if len(eq["bands"]) >= eqm.MAX_BANDS:
            return
        used = sorted(b["freq"] for b in eq["bands"])
        freq = 1000.0
        for cand in (1000.0, 250.0, 4000.0, 80.0, 10000.0, 500.0, 2000.0, 150.0, 6000.0, 40.0, 14000.0, 700.0):
            if all(abs(math.log(cand / u)) > 0.3 for u in used):
                freq = cand
                break
        eq["bands"].append({"type": "peaking", "freq": freq, "q": 1.0, "gain": 0.0, "enabled": True})
        self.graph.select(len(eq["bands"]) - 1)
        self._edited()
        self.graph.update()

    def _apply_preset(self, index):
        name = self.preset.itemData(index)
        if not name:
            return
        eq = self.eq()
        preset = eqm.PRESETS[name]
        eq["preamp"] = preset["preamp"]
        eq["bands"] = [dict(b) for b in preset["bands"]]
        eq["enabled"] = True
        self.graph.select(-1)
        self._load()
        self._commit.start()
        QTimer.singleShot(400, self._retarget)

    # -- widgets <- model
    def _sync_top(self):
        eq = self.eq()
        self._loading = True
        self.enabled.setChecked(eq.get("enabled", False))
        if not self.preamp.hasFocus():
            self.preamp.setValue(eq.get("preamp", 0.0))
        self._loading = False

    def _load(self):
        self._sync_top()
        self._load_band()
        self.graph.update()
        self._retarget()

    def _load_band(self):
        bands = self.eq()["bands"]
        i = self.graph.selected
        ok = 0 <= i < len(bands)
        self._loading = True
        for w in (self.btype, self.freq, self.gain, self.q, self.band_on, self.remove):
            w.setEnabled(ok)
        self.band_label.setText((f"Band {i + 1}" + (f" · {bands[i]['label']}" if bands[i].get("label") else ""))
                                if ok else "Band —")
        if ok:
            b = bands[i]
            self.btype.setCurrentIndex(list(eqm.TYPE_LABELS).index(b["type"]))
            if not self.freq.hasFocus():
                self.freq.setValue(b["freq"])
            if not self.gain.hasFocus():
                self.gain.setValue(b["gain"])
            if not self.q.hasFocus():
                self.q.setValue(b["q"])
            self.band_on.setChecked(b.get("enabled", True))
            self.gain.setEnabled(b["type"] in eqm.USES_GAIN)
        self._loading = False

    # -- spectrum
    def _retarget(self):
        if not self._live or not self.isVisible():
            return
        node = core.hub_out_node(self.c.state, self.box, self.c.snap)
        if node is None:
            self.tap.stop()
            self._spectrum()
            return
        self.tap.start(node.name, node.media_class == "Audio/Sink")

    def _frame(self):
        """A new analyser frame: smooth it into what the graph shows."""
        g = self.graph
        if self.tap.status == "live":
            g.spectrum, t, v = self.smoother.process(self.tap.bins)
            g.tint_time = t if self.smoother.time_on and self.smoother.time > 0 else None
            g.tint_visual = v if self.smoother.visual_on and self.smoother.visual > 0 else None
        else:
            self.smoother.reset()
            g.spectrum = g.tint_time = g.tint_visual = None
        self._spectrum()

    def _spectrum(self):
        st = self.tap.status
        g = self.graph
        if st != "live":
            g.spectrum = g.tint_time = g.tint_visual = None
        else:
            if not (self.smoother.time_on and self.smoother.time > 0):
                g.tint_time = None
            if not (self.smoother.visual_on and self.smoother.visual > 0):
                g.tint_visual = None
        col = eq_palette()
        g.legend = tuple(x for x in (("time", col["smooth_time"]) if g.tint_time else None,
                                     ("smoothing", col["smooth_visual"]) if g.tint_visual else None) if x)
        base = ("Drag a handle: " + ("gain (X locked)" if g.lock_x else "frequency / gain, Shift: gain only")
                + " · wheel: width (Q) · double-click: add or bypass · right-click: type")
        if st == "unavailable":
            base += "   ·   live spectrum unavailable (needs pw-record or parec)"
        elif st != "live" and core.hub_out_node(self.c.state, self.box, self.c.snap) is None:
            base += "   ·   device not running, no live spectrum"
        if self.hint.text() != base:
            self.hint.setText(base)
        self.graph.update()

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(0, self._retarget)
        self._spectrum()

    def closeEvent(self, e):
        self.tap.stop()
        if self._commit.isActive():
            self._commit.stop()
            self._do_commit()
        try:
            self.c.state_changed.disconnect(self._external)
            self.c.live_changed.disconnect(self._retarget)
            self.listener.changed.disconnect(self._listen_sync)
        except (RuntimeError, TypeError):
            pass
        _open.pop((id(self.c), self.box), None)
        super().closeEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(e)


_open: dict = {}


def open_eq(parent, controller, box: str) -> EqDialog:
    """One equaliser window per device; asking again raises the existing one."""
    key = (id(controller), box)
    d = _open.get(key)
    if d is None:
        d = EqDialog(controller, box, parent)
        _open[key] = d
    d.show()
    d.raise_()
    d.activateWindow()
    return d


def close_all():
    for d in list(_open.values()):
        d.close()
