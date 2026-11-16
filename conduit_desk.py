"""
conduit_desk.py -- the Mixing Desk: Conduit's routing as a console instead of a graph.

One section per box (Speaker, Microphone, each Custom Conduit).  Every cell in
the box is a channel strip; the box's virtual device is the master strip.

  routing buttons   IN    this device plays INTO the box's mix          (cell -> hub)
                    OUT   the box's mix is sent OUT to this device      (hub -> cell)
                    D:x   Speaker box only: straight to real speaker x  (bypass)
                    lit = linked; click to link / unlink (red rim = the link exists but is blocked)
  fader             the cell's volume (0 .. 2x; double-click = 1x).  On a master strip: master volume.
  MUTE              block / unblock the cell.  On a master strip: mute the device.
  EQ / NS           master strips: the equaliser window, noise suppression
  meter             ACTIVITY, not a measurement: it moves while audio is flowing through the strip

It edits exactly the same links the graph does (the Both-to-Both ones); per-port
links, cell-to-cell links, events and colours stay untouched -- use Patch view for those.
"""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetricsF, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

import qt_ui_kit as kit

import conduit_core as core
import conduit_meter
import conduit_dialogs as dlg
import conduit_skins as skins
from conduit_skin_base import F, elide, hseed, text

STRIP_W, MASTER_W, GAP, SECTION_GAP = 96, 124, 6, 22
TOP = 34
FADER_MAX = 2.0
C = Qt.AlignCenter


def _rows(chans):
    return (["*"] + list(chans)) if len(chans) >= 2 else list(chans)


class DeskCanvas(QWidget):
    def __init__(self, editor):
        super().__init__()
        self.ed = editor
        self.c = editor.c
        self.meter_ids = []           # cells / hubs the last frame drew a live meter for
        self.hits = []            # [(QRectF, kind, payload)]
        self.faders = []          # [(QRectF track, kind, id)]
        self._drag = None
        self.setMouseTracking(True)
        self.setMinimumHeight(430)
        self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Expanding)

    # ------------------------------------------------------------------ model helpers
    def _links(self, a, b):
        return [l for l in self.c.state["links"] if l["src"] == a and l["dst"] == b and not l.get("anti")]

    def _toggle_route(self, a, b):
        ls = self._links(a, b)
        if ls:
            core.remove_links(self.c.state, [l["id"] for l in ls])
        else:
            pa, pb = self.c.cell_ports(a), self.c.cell_ports(b)
            self.c.link_cells(a, _rows(pa["out"] or core.STEREO), b, _rows(pb["in"] or core.STEREO), "*", "*")
        QTimer.singleShot(0, self.c.commit)

    def sections(self):
        st = self.c.state
        out = []
        for box in core.all_boxes(st):
            cells = [c for c in st["cells"] if c["box"] == box]
            out.append((box, cells))
        return out

    def wanted_width(self):
        w = 14
        for _box, cells in self.sections():
            w += len(cells) * (STRIP_W + GAP) + MASTER_W + SECTION_GAP
        return w

    # ------------------------------------------------------------------ paint
    def paintEvent(self, e):
        sk = skins.current()
        st = self.c.state
        t = self.ed.t
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        bg = QLinearGradient(0, 0, 0, self.height())
        bg.setColorAt(0, QColor("#232428"))
        bg.setColorAt(1, QColor("#17181b"))
        p.fillRect(self.rect(), bg)
        self.hits, self.faders = [], []
        self.meter_ids = []
        self.setMinimumWidth(self.wanted_width())
        x = 14.0
        h = min(self.height() - TOP - 12, 600)
        ch = 0
        for bi, (box, cells) in enumerate(self.sections()):
            sec_w = len(cells) * (STRIP_W + GAP) + MASTER_W
            title = sk.box_title(core.box_label(st, box), bi)
            text(p, QRectF(x, 6, sec_w, 22), title, F("Barlow Condensed", 17, weight=700, spacing=2.0, caps=True), "#8f8f8a")
            p.fillRect(QRectF(x, 29, sec_w, 2), QColor("#0d0d0f"))
            speakers = [c for c in cells if c.get("speaker")]
            hub = core.hub_cells_for_box(st, box)[0]
            for cell in cells:
                ch += 1
                self._strip(p, QRectF(x, TOP, STRIP_W, h), cell, box, hub, speakers, ch, t)
                x += STRIP_W + GAP
            self._master(p, QRectF(x, TOP, MASTER_W, h), box, hub, t)
            x += MASTER_W + SECTION_GAP
        if not any(cells for _b, cells in self.sections()):
            text(p, QRectF(0, self.height() - 26, self.width(), 18),
                 "No devices yet -- switch to Patch (or List) to add some, they show up here as strips.",
                 F("Barlow Condensed", 14), "#8f8f8a", C)

    def _button(self, p, r, label, on, color="#ffb43a", rim=None, kind=None, payload=None, tip=""):
        p.setPen(QPen(QColor(rim or "#0b0b0c"), 1.6 if rim else 1))
        p.setBrush(QColor(color) if on else QColor("#1a1b1e"))
        p.drawRoundedRect(r, 3, 3)
        p.setBrush(Qt.NoBrush)
        if on:
            p.setPen(QPen(QColor(255, 255, 255, 90), 1))
            p.drawLine(QPointF(r.left() + 3, r.top() + 1.5), QPointF(r.right() - 3, r.top() + 1.5))
        text(p, r.adjusted(2, 0, -2, 0), label, F("Barlow Condensed", 12, weight=700, spacing=0.6),
             "#1a1000" if on else "#7a7a75", C)
        if kind:
            self.hits.append((QRectF(r), kind, payload, tip))

    def _level(self, cid, signal, seed, t, rms=False):
        """0..1 for a meter: the measured level (peak, or RMS for the master's second bar); the
        old bounce only when metering is unavailable on this system."""
        if not signal:
            return 0.0
        self.meter_ids.append(cid)
        mm = conduit_meter.manager(self.c)
        lv = mm.levels(cid)
        if lv is not None:
            return conduit_meter.norm(lv[1] if rms else lv[0])
        sk = skins.current()
        return sk.level(seed, t) if not mm.available and hasattr(sk, "level") else 0.0

    def _meter(self, p, r, level):
        p.fillRect(r, QColor("#0b0b0c"))
        segs = max(8, int((r.height() - 4) / 9))
        sh = (r.height() - 4) / segs
        lit = int(level * segs + 0.5)
        for i in range(segs):
            top = i >= segs - 2
            mid = i >= segs - 5
            on = i < lit
            col = (QColor("#ff4a3a") if top else QColor("#ffd23a") if mid else QColor("#45e35c")) if on else \
                (QColor("#3a1414") if top else QColor("#3a3414") if mid else QColor("#16301b"))
            p.fillRect(QRectF(r.left() + 2, r.bottom() - 2 - (i + 1) * sh + 1, r.width() - 4, sh - 1.5), col)

    def _fader(self, p, r, value, kind, ident):
        track = QRectF(r.center().x() - 2, r.top() + 14, 4, r.height() - 28)
        p.fillRect(track, QColor("#0b0b0c"))
        f = F("Share Tech Mono", 9)
        for v, lab in ((2.0, "2"), (1.0, "1"), (0.0, "0")):
            y = track.bottom() - track.height() * v / FADER_MAX
            p.setPen(QPen(QColor("#6d6d68"), 1))
            p.drawLine(QPointF(track.left() - 7, y), QPointF(track.left() - 3, y))
            text(p, QRectF(track.left() - 20, y - 6, 11, 12), lab, f, "#6d6d68", Qt.AlignRight | Qt.AlignVCenter, fit=False)
        y = track.bottom() - track.height() * max(0.0, min(FADER_MAX, value)) / FADER_MAX
        cap = QRectF(r.center().x() - 13, y - 15, 26, 30)
        g = QLinearGradient(0, cap.top(), 0, cap.bottom())
        for pos, col in ((0, "#d9dad6"), (0.45, "#8d8f8a"), (0.5, "#f2f2ee"), (0.55, "#8d8f8a"), (1, "#b9bab6")):
            g.setColorAt(pos, QColor(col))
        p.setPen(QPen(QColor("#0b0b0c"), 1))
        p.setBrush(g)
        p.drawRoundedRect(cap, 3, 3)
        p.setBrush(Qt.NoBrush)
        self.faders.append((QRectF(r.left(), track.top(), r.width(), track.height()), kind, ident))

    def _lcd(self, p, r, label, off=False, red=False):
        p.setPen(QPen(QColor("#0b0b0c"), 1))
        p.setBrush(QColor("#26302d" if off else "#d68f8f" if red else "#8fd6c6"))
        p.drawRoundedRect(r, 3, 3)
        p.setBrush(Qt.NoBrush)
        text(p, r.adjusted(4, 0, -4, 0), "--------" if off else label, F("Share Tech Mono", 13),
             "#4a5a55" if off else "#0b2a25", C)

    def _plate(self, p, r, master=False, dim=False):
        g = QLinearGradient(r.left(), 0, r.right(), 0)
        a, b = ("#33302a", "#3b372f") if master else ("#2a2c31", "#303238")
        if dim:
            a, b = "#1f2023", "#232428"
        g.setColorAt(0, QColor(a))
        g.setColorAt(0.5, QColor(b))
        g.setColorAt(1, QColor(a))
        p.setPen(QPen(QColor("#0d0d0f"), 1))
        p.setBrush(g)
        p.drawRoundedRect(r, 5, 5)
        p.setBrush(Qt.NoBrush)

    def _strip(self, p, r, cell, box, hub, speakers, ch, t):
        sk = skins.current()
        cid = cell["id"]
        status = self.c.cell_status(cid)
        closed, blocked = status == "closed", status == "blocked"
        self._plate(p, r, dim=closed)
        text(p, QRectF(r.left(), r.top() + 4, r.width(), 14), f"CH {ch}", F("Barlow Condensed", 12, weight=700, spacing=1.6), "#8f8f8a", C)
        ports = self.c.cell_ports(cid)
        y = r.top() + 22
        bw = (r.width() - 14) / 2
        if ports["out"]:
            ls = self._links(cid, hub)
            self._button(p, QRectF(r.left() + 5, y, bw, 20), "IN", any(not l.get("blocked") for l in ls),
                         rim="#ff3b30" if ls and all(l.get("blocked") for l in ls) else None,
                         kind="route", payload=(cid, hub), tip="This device plays into the mix")
        if ports["in"]:
            src = hub
            if box == "mic" and core.ns_method(self.c.state, "mic") in core.NOISE_SUPPRESSION_METHODS:
                src = core.NS_MIC
            ls = self._links(src, cid) or self._links(hub, cid)
            self._button(p, QRectF(r.left() + 9 + bw, y, bw, 20), "OUT", any(not l.get("blocked") for l in ls), "#7ad3ff",
                         rim="#ff3b30" if ls and all(l.get("blocked") for l in ls) else None,
                         kind="route", payload=(ls[0]["src"] if ls else src, cid), tip="The mix is sent out to this device")
        y += 24
        if box == "speaker" and not cell.get("speaker") and ports["out"]:
            for sp in [s for s in speakers if s["id"] != cid][:2]:
                ls = self._links(cid, sp["id"])
                self._button(p, QRectF(r.left() + 5, y, r.width() - 10, 18), "D:" + elide(sp["label"], F("Barlow Condensed", 12, weight=700), r.width() - 34),
                             bool(ls), "#7bf0a0", kind="route", payload=(cid, sp["id"]),
                             tip=f"Straight to {sp['label']} (bypasses the mix)")
                y += 21
        lcd_h, mute_h = 26, 22
        bottom = r.bottom() - 6
        lcd = QRectF(r.left() + 5, bottom - lcd_h, r.width() - 10, lcd_h)
        mute = QRectF(r.center().x() - 32, lcd.top() - mute_h - 6, 64, mute_h)
        body = QRectF(r.left() + 6, y + 4, r.width() - 12, mute.top() - y - 10)
        signal = status == "active"
        level = self._level(cid, signal, hseed(cid) % 50, t)
        self._meter(p, QRectF(body.left(), body.top(), 12, body.height()), level)
        self._fader(p, QRectF(body.left() + 30, body.top(), body.width() - 30, body.height()), cell.get("volume", 1.0), "cell", cid)
        self._button(p, mute, "MUTE", blocked, "#ff3b30", kind="mute", payload=cid, tip="Block / unblock this device")
        self._lcd(p, lcd, cell["label"], off=closed, red=blocked)
        self.hits.append((QRectF(lcd), "menu", cid, cell["label"]))

    def _master(self, p, r, box, hub, t):
        sk = skins.current()
        st = self.c.state
        cfg = core.hub_config(st, box)
        enabled = cfg.get("enabled", True)
        self._plate(p, r, master=True, dim=not enabled)
        text(p, QRectF(r.left(), r.top() + 4, r.width(), 14), "MASTER", F("Barlow Condensed", 12, weight=700, spacing=1.6), "#b8a98a", C)
        y = r.top() + 22
        n = 3 if core.box_is_microphone(st, box) else 2
        bw = (r.width() - 10 - (n - 1) * 4) / n
        x = r.left() + 5
        self._button(p, QRectF(x, y, bw, 20), "ON", enabled, "#45e35c", kind="enable", payload=box, tip="Enable / disable this box")
        x += bw + 4
        self._button(p, QRectF(x, y, bw, 20), "EQ", core.eq_enabled(st, box), kind="eq", payload=box, tip="Equaliser")
        if n == 3:
            x += bw + 4
            self._button(p, QRectF(x, y, bw, 20), "NS", core.ns_method(st, box) in core.NOISE_SUPPRESSION_METHODS,
                         "#7ad3ff", kind="ns", payload=box, tip="Noise suppression")
        lcd_h, mute_h = 26, 22
        bottom = r.bottom() - 6
        lcd = QRectF(r.left() + 5, bottom - lcd_h, r.width() - 10, lcd_h)
        mute = QRectF(r.center().x() - 32, lcd.top() - mute_h - 6, 64, mute_h)
        body = QRectF(r.left() + 8, y + 28, r.width() - 16, mute.top() - y - 34)
        item = self.ed.graph._item_for(hub) if self.ed.graph is not None else None
        signal = bool(item is not None and item.signal) and enabled and not cfg.get("muted")
        for k in range(2):
            level = self._level(hub, signal, hseed(box) % 40 + k * 3.3, t, rms=k == 1)
            self._meter(p, QRectF(body.left() + k * 15, body.top(), 12, body.height()), level)
        self._fader(p, QRectF(body.left() + 46, body.top(), body.width() - 46, body.height()), cfg.get("volume", 1.0), "hub", box)
        self._button(p, mute, "MUTE", bool(cfg.get("muted")), "#ff3b30", kind="hubmute", payload=box, tip="Mute this device")
        self._lcd(p, lcd, core.box_label(st, box), off=not enabled)

    # ------------------------------------------------------------------ input
    def _hit(self, pos):
        for r, kind, payload, tip in reversed(self.hits):
            if r.contains(pos):
                return kind, payload, tip
        return None

    def _fader_at(self, pos):
        for r, kind, ident in self.faders:
            if r.adjusted(0, -16, 0, 16).contains(pos):
                return r, kind, ident
        return None

    def _set_fader(self, r, kind, ident, y, commit=False):
        v = max(0.0, min(FADER_MAX, (r.bottom() - y) / r.height() * FADER_MAX))
        v = round(v * 20) / 20
        if abs(v - 1.0) < 0.04:
            v = 1.0                          # detent at unity
        if kind == "cell":
            cell = core.cell_by_id(self.c.state, ident)
            if cell is not None:
                cell["volume"] = v
        else:
            core.hub_config(self.c.state, ident)["volume"] = v
        self.update()
        if commit:
            self.c.commit()

    def mousePressEvent(self, e):
        pos = e.position()
        if e.button() == Qt.RightButton:
            hit = self._hit(pos)
            if hit and hit[0] == "menu":
                self._cell_menu(hit[1], e.globalPosition().toPoint())
            return
        if e.button() != Qt.LeftButton:
            return
        hit = self._hit(pos)
        if hit:
            kind, payload, _tip = hit
            st = self.c.state
            if kind == "route":
                self._toggle_route(*payload)
            elif kind == "mute":
                cell = core.cell_by_id(st, payload)
                if cell is not None:
                    cell["blocked"] = not cell.get("blocked", False)
                    self.c.commit()
            elif kind == "hubmute":
                cfg = core.hub_config(st, payload)
                cfg["muted"] = not cfg.get("muted", False)
                self.c.commit()
            elif kind == "enable":
                cfg = core.hub_config(st, payload)
                cfg["enabled"] = not cfg.get("enabled", True)
                self.c.commit()
            elif kind == "eq":
                import conduit_eqview
                conduit_eqview.open_eq(self.window(), self.c, payload)
            elif kind == "ns":
                box = payload

                def pick(method):
                    QTimer.singleShot(0, lambda: (core.set_noise_suppression(self.c.state, box, method), self.c.commit()))
                dlg.noise_suppression_menu(self, core.ns_method(st, box), pick).exec(e.globalPosition().toPoint())
            elif kind == "menu":
                self._cell_menu(payload, e.globalPosition().toPoint())
            return
        f = self._fader_at(pos)
        if f:
            self._drag = f
            self._set_fader(*f, pos.y())

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self._set_fader(*self._drag, e.position().y())
            return
        hit = self._hit(e.position())
        self.setCursor(Qt.PointingHandCursor if hit else Qt.SizeVerCursor if self._fader_at(e.position()) else Qt.ArrowCursor)
        self.setToolTip(hit[2] if hit else "")

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            f, self._drag = self._drag, None
            self._set_fader(*f, e.position().y(), commit=True)

    def mouseDoubleClickEvent(self, e):
        f = self._fader_at(e.position())
        if f and not self._hit(e.position()):
            r, kind, ident = f
            self._set_fader(r, kind, ident, r.bottom() - r.height() / FADER_MAX, commit=True)

    def _cell_menu(self, cid, gpos):
        cell = core.cell_by_id(self.c.state, cid)
        if cell is None:
            return
        menu = kit.themed_menu("", self)
        t = menu.addAction(cell["label"])
        t.setEnabled(False)
        menu.addSeparator()
        menu.addAction(f"Volume…  ({dlg.format_volume(cell['volume'])})", lambda: QTimer.singleShot(0, lambda: self._ask_volume(cid)))
        sp = menu.addAction("Real Speaker (direct-out target)")
        sp.setCheckable(True)
        sp.setChecked(cell.get("speaker", False))
        sp.triggered.connect(lambda on: (cell.__setitem__("speaker", bool(on)), QTimer.singleShot(0, self.c.commit)))
        menu.addSeparator()
        menu.addAction("Remove from desk", lambda: (core.remove_cell(self.c.state, cid), self.c.layout.forget(cid),
                                                    QTimer.singleShot(0, self.c.commit)))
        menu.exec(gpos)

    def _ask_volume(self, cid):
        cell = core.cell_by_id(self.c.state, cid)
        if cell is None:
            return
        v = dlg.ask_volume(self.window(), f"{cell['label']} — Volume", cell.get("volume", 1.0))
        if v is not None:
            cell["volume"] = v
            self.c.commit()


class DeskEditor(QWidget):
    """The desk inside a horizontal scroll area, with its own animation clock for the meters."""

    def __init__(self, controller, graph=None, parent=None):
        super().__init__(parent)
        self.c = controller
        self.graph = graph
        self.t = 0.0
        self._t0 = time.monotonic()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        self.canvas = DeskCanvas(self)
        self.scroll = kit.SmoothScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.canvas)
        self.scroll.setFrameShape(self.scroll.Shape.NoFrame)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        v.addWidget(self.scroll)
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self.c.state_changed.connect(self.canvas.update)
        self.c.live_changed.connect(self.canvas.update)

    def _tick(self):
        mm = conduit_meter.manager(self.c)
        if not self.isVisible() or not self.c.layout.ui("animations"):
            if mm._wants.get("desk"):
                mm.want([], "desk")
            return
        self.t = time.monotonic() - self._t0
        mm.want(dict.fromkeys(self.canvas.meter_ids), "desk")
        mm.poll()
        self.canvas.update()

    def new_cell(self):
        """+ Device: pick a box, then a device (same picker as the graph's New Cell)."""
        st = self.c.state
        menu = kit.themed_menu("", self)
        for box in core.all_boxes(st):
            menu.addAction(f"Add to {core.box_label(st, box)}…", lambda b=box: QTimer.singleShot(0, lambda: self._pick(b)))
        menu.exec(self.cursor().pos())

    def _pick(self, box):
        st = self.c.state
        existing = {c["label"] for c in st["cells"] if c["box"] == box}
        d = dlg.DevicePickerDialog(f"New strip — {core.box_label(st, box)}",
                                   dlg.device_entries(self.c.snap, self.c.layout, existing), self.window(),
                                   "Apps, hardware and streams PipeWire has now, plus closed ones Conduit remembers.")
        if d.exec() and d.selected():
            core.new_cell(st, d.selected(), box)
            self.c.commit()
