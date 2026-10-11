"""
conduit_dialogs.py -- the popups and dialogs both editors share, all on qt_ui_kit:
auto-detect, noise suppression, volume, colour, New Cell / New Speaker, events.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QPoint, QRect, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidgetItem, QVBoxLayout, QWidget,
                               QSizePolicy)

import qt_ui_kit as kit
from qt_ui_kit.themed_list import ROLE_SECONDARY
from qt_ui_kit.scale_reveal import animate_popup_from_point

import conduit_core as core

NOISE_SUPPRESSION_LABELS = [("none", "None"), ("rnnoise", "RNNoise (better quality)"),
                            ("webrtc", "WebRTC (built-in, lower quality)")]


def dim_label(text: str, wrap: bool = True) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(wrap)
    c = QColor(kit.Theme().text())
    c.setAlphaF(0.6)
    lab.setStyleSheet(f"QLabel {{ color: {c.name(QColor.HexArgb)}; }}")
    return lab


def bold_label(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setStyleSheet("QLabel { font-weight: bold; }")
    return lab


def show_popover_at(pop: kit.TabbedPopover, global_pos: QPoint) -> None:
    pop.adjustSize()
    pop.resize(pop._width, pop.height())
    screen = pop.screen().availableGeometry() if pop.screen() else None
    pos = QPoint(global_pos)
    if screen is not None:
        pos.setX(max(screen.left(), min(pos.x(), screen.right() - pop.width())))
        pos.setY(max(screen.top(), min(pos.y(), screen.bottom() - pop.height())))
    animate_popup_from_point(pop, global_pos, QRect(pos, pop.size()))


# ---------------------------------------------------------------------------
# Auto-detect
# ---------------------------------------------------------------------------

class AutoDetectPopup(kit.TabbedPopover):
    """The four combinable strategies of the old per-row "^" popup, unchanged in meaning
    (see conduit_core.expand_auto_detect)."""
    applied = Signal(dict)

    def __init__(self, label: str, config: dict, parent=None):
        super().__init__(parent, tab_labels=("Auto-Detect",), width=330)
        page = QWidget()
        page.setAttribute(Qt.WA_TranslucentBackground, True)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.addWidget(bold_label(label))
        self.prefix = kit.CustomCheckBox("Prefix match")
        self.prefix.setToolTip(f'"{label}-2" or "{label} (2)" counts as this device')
        self.keyword = kit.CustomCheckBox("Keyword match")
        self.keyword_edit = kit.CustomLineEdit(config.get("keyword_text", ""))
        self.keyword_edit.setPlaceholderText("e.g. vencord")
        self.same_app = kit.CustomCheckBox("Same source app")
        self.same_app.setToolTip("Every stream the same running app creates, however it's named "
                                 "(only while this device itself is live)")
        self.anti = kit.CustomCheckBox("Anti-Auto-Detect")
        self.anti.setToolTip("Keeps matching devices out of the sweep above. Never removes this device itself.")
        self.anti_edit = kit.CustomLineEdit(config.get("anti_keyword_text", ""))
        self.anti_edit.setPlaceholderText("e.g. screenshare, mic (comma-separated)")
        for w in (self.prefix, self.keyword, self.keyword_edit, self.same_app, self.anti, self.anti_edit):
            lay.addWidget(w)
        self.prefix.setChecked(config.get("prefix", False))
        self.keyword.setChecked(config.get("keyword", False))
        self.same_app.setChecked(config.get("same_app", False))
        self.anti.setChecked(config.get("anti", False))
        self.keyword_edit.setEnabled(self.keyword.isChecked())
        self.anti_edit.setEnabled(self.anti.isChecked())
        # connected only now, so opening the popup never saves anything by itself
        self.prefix.toggled.connect(self._apply)
        self.same_app.toggled.connect(self._apply)
        self.keyword.toggled.connect(lambda on: (self.keyword_edit.setEnabled(on), self._apply()))
        self.anti.toggled.connect(lambda on: (self.anti_edit.setEnabled(on), self._apply()))
        self.keyword_edit.editingFinished.connect(self._apply)
        self.anti_edit.editingFinished.connect(self._apply)
        self.set_page_widget(0, page)

    def _apply(self):
        self.applied.emit({
            "prefix": self.prefix.isChecked(), "keyword": self.keyword.isChecked(),
            "keyword_text": self.keyword_edit.text(), "same_app": self.same_app.isChecked(),
            "anti": self.anti.isChecked(), "anti_keyword_text": self.anti_edit.text(),
        })


def auto_detect_active(config: dict) -> bool:
    return bool(config.get("prefix") or config.get("keyword") or config.get("same_app"))


# ---------------------------------------------------------------------------
# Noise suppression (a themed menu of three exclusive choices)
# ---------------------------------------------------------------------------

def noise_suppression_menu(parent, current: str, on_pick):
    menu = kit.themed_menu("Noise Suppression", parent)
    title = menu.addAction("Needs services.conduit.noiseSuppression.enable")
    title.setEnabled(False)
    menu.addSeparator()
    for key, label in NOISE_SUPPRESSION_LABELS:
        act = menu.addAction(label)
        act.setCheckable(True)
        act.setChecked(key == current)
        act.triggered.connect(lambda _=False, k=key: on_pick(k))
    return menu


def noise_suppression_button_text(method: str) -> str:
    return {"rnnoise": "NS: RNNoise", "webrtc": "NS: WebRTC"}.get(method, "Noise Suppression")


# ---------------------------------------------------------------------------
# Volume / colour
# ---------------------------------------------------------------------------

def format_volume(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".") + "x" if v == v else "1x"


def ask_volume(parent, title: str, current: float, unlimited: bool = False) -> "float | None":
    hint = ("Multiplier -- 1.0 leaves it alone." + (" No upper limit." if unlimited else " 0 to 10."))
    while True:
        text = kit.ask_text(parent, title, hint, f"{current:g}", "Set")
        if text is None:
            return None
        try:
            v = float(text.replace("x", "").strip())
        except ValueError:
            continue
        if v < 0 or (not unlimited and v > 10):
            continue
        return v


def ask_color(parent, current: "str | None", title: str = "Choose Colour") -> "str | None":
    c = kit.get_color(QColor(current) if current else QColor("#ffffff"), parent, title)
    if c is None or not c.isValid():
        return None
    return c.name()


# ---------------------------------------------------------------------------
# New Cell / New Speaker
# ---------------------------------------------------------------------------

class DevicePickerDialog(kit.ThemedDialog):
    """Every device / stream PipeWire currently has, plus remembered closed ones."""

    def __init__(self, title: str, entries: list, parent=None, hint: str = ""):
        """entries: [(label, secondary text, enabled)]"""
        super().__init__(title, parent)
        if hint:
            self.lay.addWidget(self.label(hint, dim=True))
        self.filter = kit.CustomLineEdit("")
        self.filter.setPlaceholderText("Filter…")
        self.filter.setMinimumHeight(30)
        self.lay.addWidget(self.filter)
        self.list = kit.ThemedListWidget(row_height=30)
        self.list.setMinimumSize(440, 360)
        self.lay.addWidget(self.list, 1)
        for label, secondary, enabled in entries:
            it = QListWidgetItem(label)
            it.setData(ROLE_SECONDARY, secondary)
            it.setData(Qt.UserRole, label)
            if not enabled:
                it.setFlags(it.flags() & ~Qt.ItemIsEnabled & ~Qt.ItemIsSelectable)
            self.list.addItem(it)
        if not entries:
            it = QListWidgetItem("(nothing seen yet -- start the app, then Refresh devices)")
            it.setFlags(Qt.NoItemFlags)
            self.list.addItem(it)
        row, ok = self.button_row("Add")
        self.lay.addLayout(row)
        self.filter.textChanged.connect(self._filter)
        self.list.itemDoubleClicked.connect(lambda *_: self.accept())
        self.filter.returnPressed.connect(self._accept_first)
        self.filter.setFocus()

    def _filter(self, text):
        t = text.strip().lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(t) and t not in it.text().lower())

    def _accept_first(self):
        if self.list.currentItem() is None or self.list.currentItem().isHidden():
            for i in range(self.list.count()):
                it = self.list.item(i)
                if not it.isHidden() and it.flags() & Qt.ItemIsEnabled:
                    self.list.setCurrentItem(it)
                    break
        self.accept()

    def selected(self) -> "str | None":
        it = self.list.currentItem()
        if it is None or not (it.flags() & Qt.ItemIsEnabled):
            return None
        return it.data(Qt.UserRole)


def device_entries(snap: core.Snapshot, layout, existing_labels: set, speakers_only: bool = False) -> list:
    live = {}
    for n in snap.nodes.values():
        if n.is_ours or not n.display_label:
            continue
        if speakers_only and not core.is_hardware_sink(n):
            continue
        if not speakers_only and not (n.input_ports or n.output_ports):
            continue
        tags = live.setdefault(n.display_label, set())
        if n.output_ports:
            tags.add("produces")
        if n.input_ports:
            tags.add("consumes")
        if n.running:
            tags.add("active")
    entries = []
    for label in sorted(live, key=str.lower):
        t = live[label]
        sec = " · ".join(x for x in ("active" if "active" in t else "", "out" if "produces" in t else "",
                                    "in" if "consumes" in t else "") if x)
        if label in existing_labels:
            sec = "already in this box"
        entries.append((label, sec, label not in existing_labels))
    for label, rec in sorted(layout.data["known_devices"].items(), key=lambda kv: kv[0].lower()):
        if label in live:
            continue
        if speakers_only and not rec.get("hw_sink"):
            continue
        entries.append((label, "already in this box" if label in existing_labels else "closed",
                        label not in existing_labels))
    return entries


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

class EventDialog(kit.ThemedDialog):
    """The events of one cell.  "Select Outcome" closes the dialog and puts the box into
    outcome-selection mode (middle-click cells: block, again: unblock, again: nothing)."""
    select_outcome = Signal(int)      # event index

    def __init__(self, state: dict, cell: dict, parent=None):
        super().__init__(f"Events — {cell['label']}", parent)
        self.state, self.cell = state, cell
        self.events = [dict(kind=e["kind"], outcomes=dict(e.get("outcomes", {}))) for e in cell.get("events", [])]
        self.lay.addWidget(self.label("Run by the daemon, even with this window closed. Outcomes block or "
                                      "unblock cells in this box. Nothing fires for a device that already "
                                      "existed when the daemon started.", dim=True))
        self.rows = QVBoxLayout()
        self.rows.setSpacing(8)
        self.lay.addLayout(self.rows)
        add = kit.CustomButton("+ Add Event")
        add.clicked.connect(self._add)
        bottom = QHBoxLayout()
        bottom.addWidget(add)
        bottom.addStretch(1)
        self.lay.addLayout(bottom)
        row, ok = self.button_row("Save")
        self.lay.addLayout(row)
        self.setMinimumWidth(560)
        self._outcome_request = None
        self._rebuild()

    def _rebuild(self):
        while self.rows.count():
            item = self.rows.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        if not self.events:
            self.rows.addWidget(dim_label("No events yet."))
        for idx, ev in enumerate(self.events):
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(QLabel(f"{self.cell['label']}:"))
            combo = kit.CustomComboBox()
            for k in core.EVENT_KINDS:
                combo.addItem(core.EVENT_KIND_LABELS[k].replace("Device ", ""), k)
            combo.setCurrentIndex(core.EVENT_KINDS.index(ev["kind"]))
            combo.currentIndexChanged.connect(lambda _i, e=ev, c=combo: e.__setitem__("kind", c.currentData()))
            combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            h.addWidget(combo, 1)
            h.addWidget(dim_label(self._outcome_text(ev), wrap=False))
            pick = kit.CustomButton("Select Outcome")
            pick.clicked.connect(lambda _=False, i=idx: self._pick(i))
            h.addWidget(pick)
            rm = kit.CustomButton("✕")
            rm.setToolTip("Remove this event")
            rm.clicked.connect(lambda _=False, i=idx: self._remove(i))
            h.addWidget(rm)
            self.rows.addWidget(w)
        self.adjustSize()

    def _outcome_text(self, ev):
        b = sum(1 for v in ev["outcomes"].values() if v == "block")
        u = sum(1 for v in ev["outcomes"].values() if v == "unblock")
        if not b and not u:
            return "no outcome"
        return ", ".join(x for x in (f"block {b}" if b else "", f"unblock {u}" if u else "") if x)

    def _add(self):
        self.events.append({"kind": "active", "outcomes": {}})
        self._rebuild()

    def _remove(self, i):
        del self.events[i]
        self._rebuild()

    def _pick(self, i):
        self._outcome_request = i
        self.accept()

    def outcome_request(self):
        return self._outcome_request
