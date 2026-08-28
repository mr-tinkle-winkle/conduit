#!/usr/bin/env python3
"""
conduit_gui.py — Qt (PySide6) front-end for the Conduit routing config.

Edits ~/.config/conduit/state.json -- the exact same file
conduit_daemon.py reads. Never talks to the running daemon directly;
every change writes state.json to disk, then (after a short debounce)
runs `systemctl --user restart conduit-daemon.service` so the new
config takes effect immediately. Same "config is the single source of
truth, restart is fast" philosophy as Puppetry's macro_gui.py.

Layout: two panels side by side.
  Left  = Speaker: Input list, Output list, Bypass list, Speakers picker.
  Right = Mic:     Input list, Output list.

Each "Input"/"Output"/"Bypass" section is a dropdown of currently
eligible PipeWire nodes (queried live via conduit_daemon.pw_dump) plus
a list of what's already been added, each removable with an "x". The
"Speakers" picker under Bypass is a plain single-value dropdown, not
an accumulating list.

Requires conduit_daemon.py to be importable (same directory by default).
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import conduit_daemon as cd

from PySide6.QtCore import Qt, QTimer, QPoint
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QGroupBox, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QListWidget, QListWidgetItem, QPushButton, QFrame,
    QCheckBox, QLineEdit, QDoubleSpinBox, QSystemTrayIcon, QMenu, QScrollArea,
)

PLACEHOLDER = "Add a device or app…"
DESTINATION_PLACEHOLDER = "Select destination…"
RESTART_DEBOUNCE_MS = 600


class NoScrollComboBox(QComboBox):
    """A QComboBox that ignores mouse-wheel events instead of changing its
    selection -- the default Qt behavior changes the selected item on
    scroll even when the dropdown isn't open, which is surprising and
    easy to trigger by accident while scrolling past one in a list."""
    def wheelEvent(self, event):
        event.ignore()


# ---------------------------------------------------------------------------
# Live device list, queried straight from pw-dump each time it's needed
# (apps come and go, so this can't be cached for long).
# ---------------------------------------------------------------------------

def eligible_labels(nodes, predicate, exclude_labels=()):
    labels = []
    for node in nodes.values():
        if predicate(node):
            label = node.display_label
            if label and label not in labels and label not in exclude_labels:
                labels.append(label)
    return sorted(labels)


def current_nodes():
    dump = cd.pw_dump()
    if not dump:
        return {}
    return cd.build_graph(dump)


# ---------------------------------------------------------------------------
# Auto-Detect: a small popup (opened via the "^" button on each device row)
# letting you combine any of four strategies for sweeping in (or keeping
# out) devices you didn't explicitly add. Scoped to that one device, not
# the whole list -- see conduit_daemon.expand_item_auto_detect for exactly
# how each strategy matches.
# ---------------------------------------------------------------------------

DEFAULT_AUTO_DETECT = {
    "prefix": False, "keyword": False, "keyword_text": "", "same_app": False,
    "anti": False, "anti_keyword_text": "",
}


class AutoDetectPopup(QWidget):
    def __init__(self, label, config, on_apply, anchor_widget):
        super().__init__(anchor_widget, Qt.Popup)
        self._on_apply = on_apply
        self.setContentsMargins(0, 0, 0, 0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.addWidget(QLabel(f"<b>Auto-Detect for {label}</b>"))

        self.prefix_cb = QCheckBox("Prefix match")
        self.prefix_cb.setToolTip(f'A device like "{label}-2" or "{label} (2)" counts as the same as this one')
        self.prefix_cb.setChecked(config.get("prefix", False))
        layout.addWidget(self.prefix_cb)

        self.keyword_cb = QCheckBox("Keyword match")
        layout.addWidget(self.keyword_cb)

        self.keyword_edit = QLineEdit(config.get("keyword_text", ""))
        self.keyword_edit.setPlaceholderText("e.g. vencord")
        layout.addWidget(self.keyword_edit)
        self.keyword_cb.setChecked(config.get("keyword", False))
        self.keyword_edit.setEnabled(self.keyword_cb.isChecked())

        self.same_app_cb = QCheckBox("Same source app")
        self.same_app_cb.setToolTip("Groups every stream created by the same running app/process as this device, "
                                     "however differently each one is named")
        self.same_app_cb.setChecked(config.get("same_app", False))
        layout.addWidget(self.same_app_cb)

        layout.addWidget(_hline())

        self.anti_cb = QCheckBox("Anti-Auto-Detect")
        self.anti_cb.setToolTip("Keeps anything matching these keywords out of this device's auto-detect "
                                 "sweep above, even if it would otherwise match. Never removes this device itself.")
        layout.addWidget(self.anti_cb)

        self.anti_edit = QLineEdit(config.get("anti_keyword_text", ""))
        self.anti_edit.setPlaceholderText("e.g. screenshare, mic (comma-separated)")
        layout.addWidget(self.anti_edit)
        self.anti_cb.setChecked(config.get("anti", False))
        self.anti_edit.setEnabled(self.anti_cb.isChecked())

        # Wire signals up only after every widget has its initial state --
        # otherwise setChecked() above would itself fire _apply() and
        # trigger a pointless save/daemon-restart just from opening the
        # popup, before the person has touched anything.
        self.prefix_cb.toggled.connect(self._apply)
        self.keyword_cb.toggled.connect(self._on_keyword_toggled)
        self.keyword_edit.editingFinished.connect(self._apply)
        self.same_app_cb.toggled.connect(self._apply)
        self.anti_cb.toggled.connect(self._on_anti_toggled)
        self.anti_edit.editingFinished.connect(self._apply)

        self.adjustSize()

    def _on_keyword_toggled(self, checked):
        self.keyword_edit.setEnabled(checked)
        self._apply()

    def _on_anti_toggled(self, checked):
        self.anti_edit.setEnabled(checked)
        self._apply()

    def _apply(self):
        self._on_apply({
            "prefix": self.prefix_cb.isChecked(),
            "keyword": self.keyword_cb.isChecked(),
            "keyword_text": self.keyword_edit.text(),
            "same_app": self.same_app_cb.isChecked(),
            "anti": self.anti_cb.isChecked(),
            "anti_keyword_text": self.anti_edit.text(),
        })


def _auto_detect_is_active(config):
    return bool(config.get("prefix") or config.get("keyword") or config.get("same_app"))


# ---------------------------------------------------------------------------
# Noise Suppression: a small popup for the Virtual Mic and any
# as_microphone Custom Conduit. Unlike Auto-Detect's independently
# combinable strategies, this is a single mutually-exclusive choice, so
# a plain dropdown rather than checkboxes. See conduit_daemon's NOISE
# SUPPRESSION docstring section for what actually happens at the
# PipeWire level, and note the pool is only present at all once
# services.conduit.noiseSuppression.enable is set and rebuilt.
# ---------------------------------------------------------------------------

NOISE_SUPPRESSION_LABELS = [
    ("none", "None"),
    ("rnnoise", "RNNoise (better quality)"),
    ("webrtc", "WebRTC (built-in, lower quality)"),
]


class NoiseSuppressionPopup(QWidget):
    def __init__(self, current_method, on_apply, anchor_widget):
        super().__init__(anchor_widget, Qt.Popup)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.addWidget(QLabel("<b>Noise Suppression</b>"))

        note = QLabel("Only available if services.conduit.noiseSuppression.enable "
                       "is set in your NixOS config (and rebuilt).")
        note.setWordWrap(True)
        note.setMaximumWidth(240)
        layout.addWidget(note)

        self.combo = NoScrollComboBox()
        for key, label in NOISE_SUPPRESSION_LABELS:
            self.combo.addItem(label, key)
        idx = self.combo.findData(current_method)
        self.combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.combo.currentIndexChanged.connect(lambda _: on_apply(self.combo.currentData()))
        layout.addWidget(self.combo)

        self.adjustSize()


def _noise_suppression_button_text(method):
    return "Noise Suppression" + ("" if method == "none" else " \u2713")


# ---------------------------------------------------------------------------
# A labeled "dropdown adds to a removable list" widget. Each row carries
# its own auto-detect config (via the "^" button), since a list often
# mixes real hardware with an app-created virtual device and a single
# list-wide setting would wrongly apply to both.
# ---------------------------------------------------------------------------

class AddListSection(QWidget):
    def __init__(self, title, on_change, parent=None):
        super().__init__(parent)
        self._on_change = on_change
        self._eligible = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QHBoxLayout()
        header.addWidget(QLabel(f"<b>{title}</b>"))
        self.combo = NoScrollComboBox()
        self.combo.addItem(PLACEHOLDER)
        self.combo.activated.connect(self._on_combo_activated)
        header.addWidget(self.combo, 1)
        layout.addLayout(header)

        self.list_widget = QListWidget()
        self.list_widget.setMaximumHeight(110)
        layout.addWidget(self.list_widget)

    def set_eligible(self, labels):
        self._eligible = labels
        current_labels = self.item_labels()
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem(PLACEHOLDER)
        for label in labels:
            if label not in current_labels:  # already-added items don't need re-offering
                self.combo.addItem(label)
        self.combo.blockSignals(False)

    def _on_combo_activated(self, index):
        if index == 0:
            return
        label = self.combo.currentText()
        self.combo.setCurrentIndex(0)
        if label in self.item_labels():
            return
        self._add_row(label)
        self._on_change()

    def _add_row(self, label, enabled=True, volume=1.0, mono=False, auto_detect=None):
        auto_detect = {**DEFAULT_AUTO_DETECT, **(auto_detect or {})}
        item = QListWidgetItem(self.list_widget)
        item.setData(Qt.UserRole, auto_detect)

        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(4, 2, 4, 2)

        enabled_cb = QCheckBox()
        enabled_cb.setToolTip("Enabled -- untick to keep this configured without routing it")
        enabled_cb.setChecked(enabled)
        enabled_cb.toggled.connect(self._on_change)
        row_layout.addWidget(enabled_cb)

        row_layout.addWidget(QLabel(label))
        row_layout.addStretch()

        mono_cb = QCheckBox("Mono")
        mono_cb.setToolTip("Treat this device as mono -- fans a single channel out to both stereo "
                            "channels on the other side (or sums a stereo signal down to it), instead "
                            "of the normal one-to-one channel matching that can otherwise leave one "
                            "side silent")
        mono_cb.setChecked(mono)
        mono_cb.toggled.connect(self._on_change)
        row_layout.addWidget(mono_cb)

        volume_spin = QDoubleSpinBox()
        volume_spin.setRange(0.0, 10.0)
        volume_spin.setSingleStep(0.1)
        volume_spin.setDecimals(2)
        volume_spin.setSuffix("x")
        volume_spin.setFixedWidth(70)
        volume_spin.setToolTip("Volume multiplier -- 1.00x leaves it alone, 2.00x doubles it, continuously "
                                "enforced, and independent per row even if the same device appears elsewhere")
        volume_spin.setValue(volume)
        volume_spin.valueChanged.connect(self._on_change)
        row_layout.addWidget(volume_spin)

        auto_detect_btn = QPushButton()
        auto_detect_btn.setFixedWidth(28)
        auto_detect_btn.setFlat(True)
        auto_detect_btn.setToolTip("Auto-Detect")
        self._refresh_auto_detect_button(auto_detect_btn, auto_detect)
        auto_detect_btn.clicked.connect(lambda: self._open_auto_detect_popup(item, auto_detect_btn, label))
        row_layout.addWidget(auto_detect_btn)

        remove_btn = QPushButton("\u2715")
        remove_btn.setFixedWidth(24)
        remove_btn.setFlat(True)
        remove_btn.clicked.connect(lambda: self._remove_row(item))
        row_layout.addWidget(remove_btn)

        # Stashed for items() to read back -- see items() below for why
        # these live as attributes on the row widget rather than parsed
        # back out of child layout positions.
        row._conduit_enabled_cb = enabled_cb
        row._conduit_mono_cb = mono_cb
        row._conduit_volume_spin = volume_spin
        row._conduit_label = label

        item.setSizeHint(row.sizeHint())
        self.list_widget.addItem(item)
        self.list_widget.setItemWidget(item, row)

    def _refresh_auto_detect_button(self, btn, auto_detect):
        btn.setText("^\u2713" if _auto_detect_is_active(auto_detect) else "^")

    def _open_auto_detect_popup(self, item, btn, label):
        current = item.data(Qt.UserRole) or dict(DEFAULT_AUTO_DETECT)

        def apply(config):
            item.setData(Qt.UserRole, config)
            self._refresh_auto_detect_button(btn, config)
            self._on_change()

        popup = AutoDetectPopup(label, current, apply, btn)
        popup.move(btn.mapToGlobal(QPoint(0, -popup.sizeHint().height())))
        popup.show()

    def _remove_row(self, item):
        row_index = self.list_widget.row(item)
        self.list_widget.takeItem(row_index)
        self._on_change()

    def item_labels(self):
        """Just the labels -- used for dropdown de-duplication."""
        return [entry["label"] for entry in self.items()]

    def items(self):
        """Return [{"label", "enabled", "volume", "mono", "auto_detect"}, ...] in display order."""
        result = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            widget = self.list_widget.itemWidget(item)
            auto_detect = item.data(Qt.UserRole) or dict(DEFAULT_AUTO_DETECT)
            result.append({
                "label": widget._conduit_label,
                "enabled": widget._conduit_enabled_cb.isChecked(),
                "volume": widget._conduit_volume_spin.value(),
                "mono": widget._conduit_mono_cb.isChecked(),
                "auto_detect": auto_detect,
            })
        return result

    def set_items(self, entries):
        self.list_widget.clear()
        for entry in entries:
            if isinstance(entry, str):
                self._add_row(entry)
            else:
                self._add_row(
                    entry["label"],
                    entry.get("enabled", True),
                    entry.get("volume", 1.0),
                    entry.get("mono", False),
                    entry.get("auto_detect"),
                )


# ---------------------------------------------------------------------------
# Plain single-select dropdown (used for the "Speakers" bypass target).
# ---------------------------------------------------------------------------

class SingleSelectSection(QWidget):
    def __init__(self, title, placeholder, on_change, parent=None):
        super().__init__(parent)
        self._on_change = on_change
        self._placeholder = placeholder
        self._value = None  # authoritative selection; the combo is just UI for it

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        header.addWidget(QLabel(f"<b>{title}</b>"))

        self.combo = NoScrollComboBox()
        self.combo.addItem(placeholder)
        self.combo.activated.connect(self._on_combo_activated)
        header.addWidget(self.combo, 1)
        layout.addLayout(header)

    def _on_combo_activated(self, index):
        text = self.combo.currentText()
        self._value = None if text == self._placeholder else text
        self._on_change()

    def set_eligible(self, labels):
        # Rebuild the option list, but always keep the currently-saved
        # value present even if it isn't in this fresh live list -- a
        # device that's momentarily unavailable (asleep, unplugged,
        # PipeWire mid-reconfiguration) should stay configured rather
        # than getting silently cleared just because one refresh caught
        # it absent.
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem(self._placeholder)
        all_labels = list(labels)
        if self._value and self._value not in all_labels:
            all_labels = [self._value] + all_labels
        for label in all_labels:
            self.combo.addItem(label)
        self.combo.setCurrentText(self._value if self._value else self._placeholder)
        self.combo.blockSignals(False)

    def value(self):
        return self._value

    def set_value(self, label):
        self._value = label
        if label:
            if self.combo.findText(label) == -1:
                self.combo.addItem(label)
            self.combo.setCurrentText(label)
        else:
            self.combo.setCurrentIndex(0)


# ---------------------------------------------------------------------------
# One user-created "Custom Conduit" -- name (click to edit), As
# Speaker / As Microphone checkboxes, and its own Input/Output lists.
# See conduit_daemon's CUSTOM CONDUITS docstring section for what the
# checkboxes actually do at the PipeWire level.
# ---------------------------------------------------------------------------

class CustomConduitBox(QFrame):
    def __init__(self, conduit_id, on_change, on_remove, parent=None):
        super().__init__(parent)
        self.conduit_id = conduit_id
        self._on_change = on_change
        self.setFrameShape(QFrame.StyledPanel)
        self.setFixedWidth(380)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)

        header = QHBoxLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setFrame(False)
        self.name_edit.setStyleSheet("background: transparent; font-weight: bold; font-size: 13pt;")
        self.name_edit.setToolTip("Click to rename")
        self.name_edit.editingFinished.connect(self._on_change)
        header.addWidget(self.name_edit, 1)

        self.as_speaker_cb = QCheckBox("As Speaker")
        self.as_speaker_cb.setToolTip("Selectable as a playback device in apps. This is largely automatic for "
                                       "any patch point like this one -- there's no reliable way to hide it "
                                       "from speaker pickers even when unticked.")
        self.as_speaker_cb.toggled.connect(self._on_change)
        header.addWidget(self.as_speaker_cb)

        self.as_mic_cb = QCheckBox("As Microphone")
        self.as_mic_cb.setToolTip("Creates a second linked device so this conduit's mix is also selectable "
                                   "as a recording device, like a \"Stereo Mix\"")
        self.as_mic_cb.toggled.connect(self._on_as_mic_toggled)
        header.addWidget(self.as_mic_cb)

        self._mic_noise_suppression = "none"
        self.ns_btn = QPushButton(_noise_suppression_button_text("none"))
        self.ns_btn.setFlat(True)
        self.ns_btn.setEnabled(False)
        self.ns_btn.setToolTip("Only applies with \"As Microphone\" ticked")
        self.ns_btn.clicked.connect(self._open_noise_suppression_popup)
        header.addWidget(self.ns_btn)

        remove_btn = QPushButton("\u2715 Remove")
        remove_btn.setFlat(True)
        remove_btn.clicked.connect(lambda: on_remove(self.conduit_id))
        header.addWidget(remove_btn)

        outer.addLayout(header)

        lists = QHBoxLayout()
        self.inputs = AddListSection("Input", self._on_change)
        self.outputs = AddListSection("Output", self._on_change)
        lists.addWidget(self.inputs)
        lists.addWidget(self.outputs)
        outer.addLayout(lists)

    def _on_as_mic_toggled(self, checked):
        self.ns_btn.setEnabled(checked)
        self._on_change()

    def _open_noise_suppression_popup(self):
        def apply(method):
            self._mic_noise_suppression = method
            self.ns_btn.setText(_noise_suppression_button_text(method))
            self._on_change()
        popup = NoiseSuppressionPopup(self._mic_noise_suppression, apply, self.ns_btn)
        popup.move(self.ns_btn.mapToGlobal(QPoint(0, -popup.sizeHint().height())))
        popup.show()

    def set_config(self, conduit):
        self.name_edit.setText(conduit.get("name") or f"Custom Conduit {self.conduit_id}")
        self.as_speaker_cb.setChecked(conduit.get("as_speaker", True))
        self.as_mic_cb.setChecked(conduit.get("as_microphone", False))
        self.ns_btn.setEnabled(self.as_mic_cb.isChecked())
        self._mic_noise_suppression = conduit.get("mic_noise_suppression", "none")
        self.ns_btn.setText(_noise_suppression_button_text(self._mic_noise_suppression))
        self.inputs.set_items(conduit.get("inputs", []))
        self.outputs.set_items(conduit.get("outputs", []))

    def to_config(self):
        name = self.name_edit.text().strip() or f"Custom Conduit {self.conduit_id}"
        return {
            "id": self.conduit_id,
            "name": name,
            "as_speaker": self.as_speaker_cb.isChecked(),
            "as_microphone": self.as_mic_cb.isChecked(),
            "mic_noise_suppression": self._mic_noise_suppression,
            "inputs": self.inputs.items(),
            "outputs": self.outputs.items(),
        }

    def own_node_labels(self):
        """This conduit's own underlying device name(s) -- excluded from
        its own Input/Output dropdowns so it can't be routed into itself."""
        name = self.name_edit.text().strip() or f"Custom Conduit {self.conduit_id}"
        labels = {name}
        if self.as_mic_cb.isChecked():
            labels.add(f"{name} (Mic)")
        return labels

    def refresh_devices(self, nodes):
        exclude = self.own_node_labels()
        self.inputs.set_eligible(eligible_labels(nodes, cd.is_producer, exclude))
        self.outputs.set_eligible(eligible_labels(nodes, cd.is_consumer, exclude))


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class ConduitWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Conduit")
        self.setWindowIcon(QIcon.fromTheme("conduit"))
        self.resize(980, 760)

        self._restart_timer = QTimer(self)
        self._restart_timer.setSingleShot(True)
        self._restart_timer.timeout.connect(self._restart_daemon)

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("<i>Changes save automatically.</i>"))
        toolbar.addStretch()
        refresh_btn = QPushButton("Refresh devices")
        refresh_btn.clicked.connect(self.refresh_devices)
        toolbar.addWidget(refresh_btn)
        self.auto_refresh_cb = QCheckBox("Auto Refresh Devices")
        self.auto_refresh_cb.setToolTip("Periodically re-check for new/gone devices on its own, "
                                         "every few seconds, instead of only on manual Refresh")
        self.auto_refresh_cb.toggled.connect(self._on_auto_refresh_toggled)
        toolbar.addWidget(self.auto_refresh_cb)
        tray_btn = QPushButton("Close to Tray")
        tray_btn.clicked.connect(self.close_to_tray)
        toolbar.addWidget(tray_btn)
        outer.addLayout(toolbar)

        self._auto_refresh_timer = QTimer(self)
        self._auto_refresh_timer.setInterval(3000)
        self._auto_refresh_timer.timeout.connect(self.refresh_devices)

        self._tray_icon = None  # created lazily on first use of close_to_tray

        panels = QHBoxLayout()
        outer.addLayout(panels)

        # --- Speaker panel (left) ---
        speaker_box = QGroupBox("Speaker")
        speaker_layout = QVBoxLayout(speaker_box)
        self.speaker_inputs = AddListSection("Input", self._save)
        self.speaker_outputs = AddListSection("Output", self._save)
        self.speaker_bypass = AddListSection("Bypass", self._save)
        self.speakers_target = SingleSelectSection("Destination", DESTINATION_PLACEHOLDER, self._save)
        speaker_layout.addWidget(self.speaker_inputs)
        speaker_layout.addWidget(_hline())
        speaker_layout.addWidget(self.speaker_outputs)
        speaker_layout.addWidget(_hline())
        bypass_row = QHBoxLayout()
        bypass_row.addWidget(self.speaker_bypass)
        bypass_row.addWidget(self.speakers_target)
        speaker_layout.addLayout(bypass_row)
        speaker_layout.addStretch()
        panels.addWidget(speaker_box)

        # --- Mic panel (right) ---
        mic_box = QGroupBox("Microphone")
        mic_layout = QVBoxLayout(mic_box)
        mic_header = QHBoxLayout()
        mic_header.addStretch()
        self.mic_ns_btn = QPushButton(_noise_suppression_button_text("none"))
        self.mic_ns_btn.setFlat(True)
        self.mic_ns_btn.clicked.connect(self._open_mic_noise_suppression_popup)
        mic_header.addWidget(self.mic_ns_btn)
        mic_layout.addLayout(mic_header)
        self._mic_noise_suppression = "none"
        self.mic_inputs = AddListSection("Input", self._save)
        self.mic_outputs = AddListSection("Output", self._save)
        mic_layout.addWidget(self.mic_inputs)
        mic_layout.addWidget(_hline())
        mic_layout.addWidget(self.mic_outputs)
        mic_layout.addStretch()
        panels.addWidget(mic_box)

        # --- Custom Connection (below Speaker/Mic) ---
        custom_box = QGroupBox("Custom Connection")
        custom_outer = QVBoxLayout(custom_box)
        custom_header = QHBoxLayout()
        custom_header.addWidget(QLabel("Custom conduits are your own virtual patch points -- "
                                        "route anything into them, then out to anything else."))
        custom_header.addStretch()
        add_custom_btn = QPushButton("+")
        add_custom_btn.setFixedWidth(28)
        add_custom_btn.setToolTip("Add a Custom Conduit")
        add_custom_btn.clicked.connect(self._add_custom_conduit)
        custom_header.addWidget(add_custom_btn)
        custom_outer.addLayout(custom_header)

        self._custom_container = QWidget()
        self._custom_container_layout = QHBoxLayout(self._custom_container)
        self._custom_container_layout.setContentsMargins(0, 0, 0, 0)
        self._custom_container_layout.addStretch()

        custom_scroll = QScrollArea()
        custom_scroll.setWidgetResizable(True)
        custom_scroll.setWidget(self._custom_container)
        custom_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        custom_scroll.setFixedHeight(300)
        custom_outer.addWidget(custom_scroll)

        outer.addWidget(custom_box)

        self.custom_conduit_boxes = {}  # id -> CustomConduitBox
        self._next_custom_id = 1

        self._loading = True
        # refresh_devices() must run BEFORE load_state(): the Speakers
        # picker's set_value() works by calling QComboBox.setCurrentText(),
        # which silently no-ops if the combo doesn't already contain that
        # entry. Loading state first (when the combo holds nothing but the
        # placeholder) meant the saved bypass_target was discarded on
        # every single launch -- populate the real device list first, then
        # restore the selection against it.
        self.refresh_devices()
        self.load_state()
        self._loading = False

    # -- system tray ------------------------------------------------------

    def close_to_tray(self):
        if self._tray_icon is None:
            self._tray_icon = QSystemTrayIcon(QIcon.fromTheme("conduit"), self)
            self._tray_icon.setToolTip("Conduit")
            menu = QMenu()
            open_action = menu.addAction("Open Conduit")
            open_action.triggered.connect(self._restore_from_tray)
            quit_action = menu.addAction("Quit")
            quit_action.triggered.connect(QApplication.instance().quit)
            self._tray_icon.setContextMenu(menu)
            self._tray_icon.activated.connect(self._on_tray_activated)
        self._tray_icon.show()
        self.hide()

    def _on_tray_activated(self, reason):
        # Trigger (left-click) toggles the window; Context (right-click)
        # is handled separately by the menu itself.
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._restore_from_tray()

    def _restore_from_tray(self):
        self.show()
        self.raise_()
        self.activateWindow()

    # -- noise suppression --------------------------------------------------

    def _open_mic_noise_suppression_popup(self):
        def apply(method):
            self._mic_noise_suppression = method
            self.mic_ns_btn.setText(_noise_suppression_button_text(method))
            self._save()
        popup = NoiseSuppressionPopup(self._mic_noise_suppression, apply, self.mic_ns_btn)
        popup.move(self.mic_ns_btn.mapToGlobal(QPoint(0, -popup.sizeHint().height())))
        popup.show()

    # -- custom conduits ---------------------------------------------------

    def _add_custom_conduit(self):
        conduit_id = self._next_custom_id
        self._next_custom_id += 1
        box = CustomConduitBox(conduit_id, self._save, self._remove_custom_conduit)
        box.set_config({"id": conduit_id, "name": f"Custom Conduit {conduit_id}",
                         "as_speaker": True, "as_microphone": False, "inputs": [], "outputs": []})
        self.custom_conduit_boxes[conduit_id] = box
        # Insert before the trailing stretch so new boxes stack top-down.
        self._custom_container_layout.insertWidget(self._custom_container_layout.count() - 1, box)
        box.refresh_devices(current_nodes())
        self._save()

    def _remove_custom_conduit(self, conduit_id):
        box = self.custom_conduit_boxes.pop(conduit_id, None)
        if box is not None:
            self._custom_container_layout.removeWidget(box)
            box.deleteLater()
            self._save()

    # -- device list refresh --------------------------------------------

    def _on_auto_refresh_toggled(self, checked):
        if checked:
            self._auto_refresh_timer.start()
        else:
            self._auto_refresh_timer.stop()

    def refresh_devices(self):
        nodes = current_nodes()
        self.mic_inputs.set_eligible(eligible_labels(nodes, cd.is_producer))
        self.mic_outputs.set_eligible(eligible_labels(nodes, cd.is_consumer))
        self.speaker_inputs.set_eligible(eligible_labels(nodes, cd.is_producer))
        self.speaker_outputs.set_eligible(eligible_labels(nodes, cd.is_consumer))
        self.speaker_bypass.set_eligible(eligible_labels(nodes, cd.is_producer))
        self.speakers_target.set_eligible(eligible_labels(nodes, cd.is_hardware_sink))
        for box in self.custom_conduit_boxes.values():
            box.refresh_devices(nodes)

    # -- persistence ------------------------------------------------------

    def load_state(self):
        cd.ensure_config_exists()
        state = cd.load_state()
        self.mic_inputs.set_items(state["mic"]["inputs"])
        self.mic_outputs.set_items(state["mic"]["outputs"])
        self._mic_noise_suppression = state["mic"].get("noise_suppression", "none")
        self.mic_ns_btn.setText(_noise_suppression_button_text(self._mic_noise_suppression))
        self.speaker_inputs.set_items(state["speaker"]["inputs"])
        self.speaker_outputs.set_items(state["speaker"]["outputs"])
        self.speaker_bypass.set_items(state["speaker"]["bypass"])
        self.speakers_target.set_value(state["speaker"].get("bypass_target"))

        custom = state.get("custom", {"next_id": 1, "conduits": []})
        self._next_custom_id = custom.get("next_id", 1)
        for conduit_id, box in list(self.custom_conduit_boxes.items()):
            self._custom_container_layout.removeWidget(box)
            box.deleteLater()
        self.custom_conduit_boxes = {}
        nodes = current_nodes()
        for conduit in custom.get("conduits", []):
            box = CustomConduitBox(conduit["id"], self._save, self._remove_custom_conduit)
            box.set_config(conduit)
            box.refresh_devices(nodes)
            self.custom_conduit_boxes[conduit["id"]] = box
            self._custom_container_layout.insertWidget(self._custom_container_layout.count() - 1, box)

    def _current_state(self):
        return {
            "mic": {
                "inputs": self.mic_inputs.items(),
                "outputs": self.mic_outputs.items(),
                "noise_suppression": self._mic_noise_suppression,
            },
            "speaker": {
                "inputs": self.speaker_inputs.items(),
                "outputs": self.speaker_outputs.items(),
                "bypass": self.speaker_bypass.items(),
                "bypass_target": self.speakers_target.value(),
            },
            "custom": {
                "next_id": self._next_custom_id,
                "conduits": [box.to_config() for box in self.custom_conduit_boxes.values()],
            },
        }

    def _save(self):
        if self._loading:
            return
        cd.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        cd.STATE_FILE.write_text(json.dumps(self._current_state(), indent=2))
        # Debounce: rapid-fire edits (several clicks in a row) only trigger
        # one restart, RESTART_DEBOUNCE_MS after the last change settles.
        self._restart_timer.start(RESTART_DEBOUNCE_MS)

    def _restart_daemon(self):
        subprocess.run(
            ["systemctl", "--user", "restart", "conduit-daemon.service"],
            capture_output=True,
        )


def _hline():
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setFrameShadow(QFrame.Sunken)
    return line


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Conduit")
    window = ConduitWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
