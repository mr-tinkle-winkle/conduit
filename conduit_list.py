"""
conduit_list.py -- the List editor: the old Speaker / Microphone / Custom Conduit
panels, over the v2 model.

It shows only what is connected to a box's hub -- Inputs (things linked into
the hub) and Outputs (things the hub is linked out to) -- and edits just those
links.  Everything it can't represent (per-port choices, cell-to-cell links,
bypass anti-links, events, colours, positions, blocks on cells) is left exactly
as it is: a row stands for "every link between this thing and the hub", so
toggling its checkbox blocks / unblocks those links, its volume sets their
volume, and ✕ removes only those links (and the cell itself only when nothing
else -- links, events -- still uses it).
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

import qt_ui_kit as kit

import conduit_core as core
import conduit_dialogs as dlg
from conduit_header import BoxHeader

PLACEHOLDER = "Add a device or app…"


def _rows(chans):
    return (["*"] + list(chans)) if len(chans) >= 2 else list(chans)


def box_hubs(state, box, side):
    """Hub ids of a box that count for its Inputs ("in") or Outputs ("out") section."""
    if box == "speaker":
        return [core.HUB_SPEAKER]
    if box == "mic":
        return [core.HUB_MIC] if side == "in" else [core.HUB_MIC, core.NS_MIC]
    cid = box.split(".")[1]
    if side == "in":
        return [core.hub_custom(cid)]
    return [core.hub_custom(cid), core.hub_custom_mic(cid), core.ns_cell_id(box)]


def section_rows(state, box, side):
    """[(endpoint id, [link ids])] in a stable order."""
    hubs = set(box_hubs(state, box, side))
    rows = {}
    for l in state["links"]:
        if l.get("anti"):
            continue
        if side == "in" and l["dst"] in hubs and l["src"] not in hubs:
            rows.setdefault(l["src"], []).append(l["id"])
        elif side == "out" and l["src"] in hubs and l["dst"] not in hubs:
            rows.setdefault(l["dst"], []).append(l["id"])
    order = {c["id"]: i for i, c in enumerate(state["cells"])}
    return sorted(rows.items(), key=lambda kv: (order.get(kv[0], -1), kv[0]))


class Row(QFrame):
    def __init__(self, section, endpoint, link_ids):
        super().__init__()
        self.section, self.endpoint, self.link_ids = section, endpoint, list(link_ids)
        c = section.c
        h = QHBoxLayout(self)
        h.setContentsMargins(6, 2, 4, 2)
        h.setSpacing(6)
        self.enabled = kit.CustomCheckBox("")
        self.enabled.setToolTip("Untick to block these links without removing them")
        self.enabled.toggled.connect(self._toggle)
        h.addWidget(self.enabled)
        self.label = QLabel()
        self.label.setMinimumWidth(60)
        self.label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        h.addWidget(self.label, 1)
        self.vol = kit.CustomDoubleSpinBox()
        self.vol.setRange(0.0, 10.0)
        self.vol.setSingleStep(0.1)
        self.vol.setDecimals(2)
        self.vol.setSuffix("x")
        self.vol.setFixedWidth(84)
        self.vol.setToolTip("Volume of these links -- an inline gain stage; doesn't touch the device's own volume")
        self.vol.valueChanged.connect(self._volume)
        h.addWidget(self.vol)
        self.ad = kit.CustomButton("^")
        self.ad.setToolTip("Auto-Detect")
        self.ad.clicked.connect(self._auto_detect)
        keep = self.ad.sizePolicy()
        keep.setRetainSizeWhenHidden(True)        # rows line up whether or not they have "^"
        self.ad.setSizePolicy(keep)
        self.ad.setVisible(not core.is_hub(endpoint))
        h.addWidget(self.ad)
        rm = kit.CustomButton("✕")
        rm.setToolTip("Remove (only these links)")
        rm.clicked.connect(self._remove)
        h.addWidget(rm)
        self.refresh(link_ids)

    def refresh(self, link_ids):
        st = self.section.c.state
        self.link_ids = list(link_ids)
        links = [core.link_by_id(st, k) for k in self.link_ids]
        links = [l for l in links if l]
        text = core.cell_label(st, self.endpoint)
        cell = core.cell_by_id(st, self.endpoint)
        extra = []
        if cell is not None:
            if cell.get("blocked"):
                extra.append("blocked")
            if cell["box"] != self.section.box:
                extra.append(f"in {core.box_label(st, cell['box'])}")
            if cell.get("speaker"):
                extra.append("speaker")
            status = self.section.c.cell_status(self.endpoint)
            if status == "closed":
                extra.append("closed")
        self.label.setText(text + (f"  ·  {', '.join(extra)}" if extra else ""))
        self.label.setToolTip(text)
        for w in (self.enabled, self.vol):
            w.blockSignals(True)
        self.enabled.setChecked(any(not l.get("blocked") for l in links))
        if not self.vol.hasFocus():
            self.vol.setValue(min(10.0, links[0].get("volume", 1.0)) if links else 1.0)
        for w in (self.enabled, self.vol):
            w.blockSignals(False)
        if cell is not None:
            self.ad.setText("^ ✓" if dlg.auto_detect_active(cell["auto_detect"]) else "^")

    def _links(self):
        st = self.section.c.state
        return [l for l in (core.link_by_id(st, k) for k in self.link_ids) if l]

    def _toggle(self, on):
        for l in self._links():
            l["blocked"] = not on
        self.section.c.commit()

    def _volume(self, v):
        for l in self._links():
            l["volume"] = round(v, 4)
        self.section.c.commit()

    def _auto_detect(self):
        cell = core.cell_by_id(self.section.c.state, self.endpoint)
        if cell is None:
            return
        pop = dlg.AutoDetectPopup(cell["label"], cell["auto_detect"], self)
        cid = self.endpoint

        def apply(cfg):
            c = core.cell_by_id(self.section.c.state, cid)
            if c is not None:
                c["auto_detect"] = cfg
                self.section.c.commit()
        pop.applied.connect(apply)
        pop.show_below(self.ad)

    def _remove(self):
        c = self.section.c
        core.remove_links(c.state, self.link_ids)
        cell = core.cell_by_id(c.state, self.endpoint)
        if cell is not None and not cell.get("events") and not any(
                self.endpoint in (l["src"], l["dst"]) for l in c.state["links"]):
            core.remove_cell(c.state, self.endpoint)
            c.layout.forget(self.endpoint)
        QTimer.singleShot(0, c.commit)


class Section(QWidget):
    def __init__(self, controller, box, side, title):
        super().__init__()
        self.c, self.box, self.side = controller, box, side
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        head = QHBoxLayout()
        t = QLabel(title)
        t.setStyleSheet("QLabel { font-weight: bold; }")
        head.addWidget(t)
        self.combo = kit.CustomComboBox()
        self.combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.combo.activated.connect(self._add)
        head.addWidget(self.combo, 1)
        v.addLayout(head)
        self.list = QFrame()
        self.list.setAutoFillBackground(False)
        self.list_lay = QVBoxLayout(self.list)
        self.list_lay.setContentsMargins(4, 4, 4, 4)
        self.list_lay.setSpacing(2)
        self.list_lay.addStretch(1)
        self.list.setMinimumHeight(96)
        v.addWidget(self.list)
        self.rows: dict[str, Row] = {}
        self._sig = None
        self.refresh_devices()

    def paintEvent(self, e):
        from PySide6.QtGui import QPainter
        from PySide6.QtCore import QRectF
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.list.geometry()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.fillPath(kit.rounded_rect_path(r, kit.Theme().corner_radius), kit.Theme().surface())

    def sync(self):
        st = self.c.state
        rows = section_rows(st, self.box, self.side)
        sig = tuple(k for k, _ in rows)
        if sig != self._sig:
            self._sig = sig
            for r in self.rows.values():
                r.hide()
                r.deleteLater()
            self.rows = {}
            for endpoint, ids in rows:
                row = Row(self, endpoint, ids)
                self.rows[endpoint] = row
                self.list_lay.insertWidget(self.list_lay.count() - 1, row)
        else:
            for endpoint, ids in rows:
                self.rows[endpoint].refresh(ids)
        self.refresh_devices()

    def refresh_devices(self):
        if self.combo.popup_widget() is not None:
            return
        st = self.c.state
        have = {core.cell_label(st, e) for e in self.rows}
        dev_side = "out" if self.side == "in" else "in"     # Inputs offer producers, Outputs consumers
        live = [l for l in self.c.eligible_labels(dev_side) if l not in have]
        closed = []
        for label, rec in sorted(self.c.layout.data["known_devices"].items(), key=lambda kv: kv[0].lower()):
            if label in have or label in live:
                continue
            if rec.get(dev_side):
                closed.append(label)
        items = [(PLACEHOLDER, None)] + [(l, l) for l in live] + [(f"{l}  (closed)", l) for l in closed]
        cur = [(self.combo.itemText(i), self.combo.itemData(i)) for i in range(self.combo.count())]
        if cur == items:
            return
        self.combo.blockSignals(True)
        self.combo.clear()
        for text, data in items:
            self.combo.addItem(text, data)
        self.combo.setCurrentIndex(0)
        self.combo.blockSignals(False)

    def _add(self, index):
        label = self.combo.itemData(index)
        self.combo.setCurrentIndex(0)
        if not label:
            return
        c, st = self.c, self.c.state
        cell = c.ensure_cell(label, self.box)
        hub = box_hubs(st, self.box, self.side)[0]
        if self.side == "out" and self.box == "mic" and core.ns_method(st, "mic") in core.NOISE_SUPPRESSION_METHODS:
            hub = core.NS_MIC
        cp, hp = c.cell_ports(cell["id"]), c.cell_ports(hub)
        if self.side == "in":
            c.link_cells(cell["id"], _rows(cp["out"] or core.STEREO), hub, _rows(hp["in"]), "*", "*")
        else:
            c.link_cells(hub, _rows(hp["out"]), cell["id"], _rows(cp["in"] or core.STEREO), "*", "*")
        QTimer.singleShot(0, c.commit)


class BoxPanel(QFrame):
    def __init__(self, controller, box):
        super().__init__()
        self.c, self.box = controller, box
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 10)
        v.setSpacing(8)
        self.header = BoxHeader(controller, box)
        v.addWidget(self.header)
        self.inputs = Section(controller, box, "in", "Input")
        self.outputs = Section(controller, box, "out", "Output")
        v.addWidget(self.inputs)
        v.addWidget(self.outputs)
        v.addStretch(1)

    def paintEvent(self, e):
        from PySide6.QtGui import QPainter, QPen
        from PySide6.QtCore import QRectF
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        th = kit.Theme()
        path = kit.rounded_rect_path(QRectF(self.rect()).adjusted(1, 1, -1, -1), th.corner_radius)
        p.fillPath(path, th.base())
        p.setPen(QPen(th.border(), 1.2))
        p.drawPath(path)

    def sync(self):
        self.header.sync()
        self.inputs.sync()
        self.outputs.sync()
        self.setEnabled(True)


class ListEditor(QWidget):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.c = controller
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(10)
        self.panels: dict[str, BoxPanel] = {}
        for box in ("speaker", "mic"):
            p = BoxPanel(controller, box)
            self.panels[box] = p
            top.addWidget(p, 1)
        v.addLayout(top, 3)

        head = QHBoxLayout()
        head.setContentsMargins(6, 0, 6, 0)
        lab = QLabel("Custom Conduits")
        lab.setStyleSheet("QLabel { font-weight: bold; }")
        head.addWidget(lab)
        head.addWidget(dlg.dim_label("your own virtual patch points -- route anything in, then out to anything",
                                     wrap=False))
        head.addStretch(1)
        add = kit.CustomButton("+ Custom Conduit")
        add.clicked.connect(lambda: (core.add_conduit(self.c.state), self.c.commit()))
        head.addWidget(add)
        v.addLayout(head)

        self.custom_host = QWidget()
        self.custom_lay = QHBoxLayout(self.custom_host)
        self.custom_lay.setContentsMargins(0, 0, 0, 0)
        self.custom_lay.setSpacing(10)
        self.custom_lay.addStretch(1)
        self.scroll = kit.SmoothScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.custom_host)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setMinimumHeight(260)
        v.addWidget(self.scroll, 2)
        self.c.state_changed.connect(self.sync)
        self.c.live_changed.connect(self.sync)
        self.sync()

    def sync(self):
        boxes = [b for b in core.all_boxes(self.c.state) if b.startswith("custom.")]
        for box in list(self.panels):
            if box.startswith("custom.") and box not in boxes:
                p = self.panels.pop(box)
                p.hide()
                p.deleteLater()
        for box in boxes:
            if box not in self.panels:
                p = BoxPanel(self.c, box)
                p.setFixedWidth(540)
                self.panels[box] = p
                self.custom_lay.insertWidget(self.custom_lay.count() - 1, p)
        for p in self.panels.values():
            p.sync()
