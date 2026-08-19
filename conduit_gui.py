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
    QCheckBox, QLineEdit,
)

PLACEHOLDER = "Add a device or app…"
SPEAKERS_PLACEHOLDER = "Select speakers…"
RESTART_DEBOUNCE_MS = 600


# ---------------------------------------------------------------------------
# Live device list, queried straight from pw-dump each time it's needed
# (apps come and go, so this can't be cached for long).
# ---------------------------------------------------------------------------

def eligible_labels(nodes, predicate):
    labels = []
    for node in nodes.values():
        if predicate(node):
            label = node.display_label
            if label and label not in labels:
                labels.append(label)
    return sorted(labels)


def current_nodes():
    dump = cd.pw_dump()
    if not dump:
        return {}
    return cd.build_graph(dump)


# ---------------------------------------------------------------------------
# Auto-Detect: a small popup (opened via the "^" button on each device row)
# letting you combine any of three strategies for sweeping in devices you
# didn't explicitly add. Scoped to that one device, not the whole list --
# see conduit_daemon.expand_item_auto_detect for exactly how each strategy
# matches.
# ---------------------------------------------------------------------------

DEFAULT_AUTO_DETECT = {"prefix": False, "keyword": False, "keyword_text": "", "same_app": False}


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

        # Wire signals up only after every widget has its initial state --
        # otherwise setChecked() above would itself fire _apply() and
        # trigger a pointless save/daemon-restart just from opening the
        # popup, before the person has touched anything.
        self.prefix_cb.toggled.connect(self._apply)
        self.keyword_cb.toggled.connect(self._on_keyword_toggled)
        self.keyword_edit.editingFinished.connect(self._apply)
        self.same_app_cb.toggled.connect(self._apply)

        self.adjustSize()

    def _on_keyword_toggled(self, checked):
        self.keyword_edit.setEnabled(checked)
        self._apply()

    def _apply(self):
        self._on_apply({
            "prefix": self.prefix_cb.isChecked(),
            "keyword": self.keyword_cb.isChecked(),
            "keyword_text": self.keyword_edit.text(),
            "same_app": self.same_app_cb.isChecked(),
        })


def _auto_detect_is_active(config):
    return bool(config.get("prefix") or config.get("keyword") or config.get("same_app"))


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

        layout.addWidget(QLabel(f"<b>{title}</b>"))

        self.combo = QComboBox()
        self.combo.addItem(PLACEHOLDER)
        self.combo.activated.connect(self._on_combo_activated)
        layout.addWidget(self.combo)

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

    def _add_row(self, label, auto_detect=None):
        auto_detect = {**DEFAULT_AUTO_DETECT, **(auto_detect or {})}
        item = QListWidgetItem(self.list_widget)
        item.setData(Qt.UserRole, auto_detect)

        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(4, 2, 4, 2)
        row_layout.addWidget(QLabel(label))
        row_layout.addStretch()

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
        """Return [{"label": ..., "auto_detect": {...}}, ...] in display order."""
        result = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            widget = self.list_widget.itemWidget(item)
            label = widget.layout().itemAt(0).widget().text()
            auto_detect = item.data(Qt.UserRole) or dict(DEFAULT_AUTO_DETECT)
            result.append({"label": label, "auto_detect": auto_detect})
        return result

    def set_items(self, entries):
        self.list_widget.clear()
        for entry in entries:
            if isinstance(entry, str):
                self._add_row(entry)
            else:
                self._add_row(entry["label"], entry.get("auto_detect"))


# ---------------------------------------------------------------------------
# Plain single-select dropdown (used for the "Speakers" bypass target).
# ---------------------------------------------------------------------------

class SingleSelectSection(QWidget):
    def __init__(self, title, placeholder, on_change, parent=None):
        super().__init__(parent)
        self._on_change = on_change
        self._placeholder = placeholder

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel(f"<b>{title}</b>"))

        self.combo = QComboBox()
        self.combo.addItem(placeholder)
        self.combo.activated.connect(lambda _: self._on_change())
        layout.addWidget(self.combo)

    def set_eligible(self, labels):
        current = self.value()
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem(self._placeholder)
        for label in labels:
            self.combo.addItem(label)
        if current and current in labels:
            self.combo.setCurrentText(current)
        self.combo.blockSignals(False)

    def value(self):
        text = self.combo.currentText()
        return None if text == self._placeholder else text

    def set_value(self, label):
        if label:
            self.combo.setCurrentText(label)
        else:
            self.combo.setCurrentIndex(0)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class ConduitWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Conduit")
        self.setWindowIcon(QIcon.fromTheme("conduit"))
        self.resize(760, 480)

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
        outer.addLayout(toolbar)

        panels = QHBoxLayout()
        outer.addLayout(panels)

        # --- Speaker panel (left) ---
        speaker_box = QGroupBox("Speaker")
        speaker_layout = QVBoxLayout(speaker_box)
        self.speaker_inputs = AddListSection("Input", self._save)
        self.speaker_outputs = AddListSection("Output", self._save)
        self.speaker_bypass = AddListSection("Bypass", self._save)
        self.speakers_target = SingleSelectSection("Speakers", SPEAKERS_PLACEHOLDER, self._save)
        speaker_layout.addWidget(self.speaker_inputs)
        speaker_layout.addWidget(_hline())
        speaker_layout.addWidget(self.speaker_outputs)
        speaker_layout.addWidget(_hline())
        speaker_layout.addWidget(self.speaker_bypass)
        speaker_layout.addWidget(self.speakers_target)
        speaker_layout.addStretch()
        panels.addWidget(speaker_box)

        # --- Mic panel (right) ---
        mic_box = QGroupBox("Microphone")
        mic_layout = QVBoxLayout(mic_box)
        self.mic_inputs = AddListSection("Input", self._save)
        self.mic_outputs = AddListSection("Output", self._save)
        mic_layout.addWidget(self.mic_inputs)
        mic_layout.addWidget(_hline())
        mic_layout.addWidget(self.mic_outputs)
        mic_layout.addStretch()
        panels.addWidget(mic_box)

        self._loading = True
        self.load_state()
        self.refresh_devices()
        self._loading = False

    # -- device list refresh --------------------------------------------

    def refresh_devices(self):
        nodes = current_nodes()
        self.mic_inputs.set_eligible(eligible_labels(nodes, cd.is_producer))
        self.mic_outputs.set_eligible(eligible_labels(nodes, cd.is_consumer))
        self.speaker_inputs.set_eligible(eligible_labels(nodes, cd.is_producer))
        self.speaker_outputs.set_eligible(eligible_labels(nodes, cd.is_consumer))
        self.speaker_bypass.set_eligible(eligible_labels(nodes, cd.is_producer))
        self.speakers_target.set_eligible(eligible_labels(nodes, cd.is_hardware_sink))

    # -- persistence ------------------------------------------------------

    def load_state(self):
        cd.ensure_config_exists()
        state = cd.load_state()
        self.mic_inputs.set_items(state["mic"]["inputs"])
        self.mic_outputs.set_items(state["mic"]["outputs"])
        self.speaker_inputs.set_items(state["speaker"]["inputs"])
        self.speaker_outputs.set_items(state["speaker"]["outputs"])
        self.speaker_bypass.set_items(state["speaker"]["bypass"])
        self.speakers_target.set_value(state["speaker"].get("bypass_target"))

    def _current_state(self):
        return {
            "mic": {
                "inputs": self.mic_inputs.items(),
                "outputs": self.mic_outputs.items(),
            },
            "speaker": {
                "inputs": self.speaker_inputs.items(),
                "outputs": self.speaker_outputs.items(),
                "bypass": self.speaker_bypass.items(),
                "bypass_target": self.speakers_target.value(),
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
