"""
conduit_header.py -- a box's header row, shared by the graph and list editors:
name (editable for Custom Conduits), As Speaker / As Microphone, the
noise-suppression button (only while the box acts as a microphone), master
Vol / Mute, and -- in the graph editor -- fullscreen.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QPoint, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

import qt_ui_kit as kit

import conduit_core as core
import conduit_dialogs as dlg


class BoxHeader(QWidget):
    fullscreen_clicked = Signal()

    def __init__(self, controller, box: str, fullscreen: bool = False, parent=None):
        super().__init__(parent)
        self.c, self.box = controller, box
        self.is_custom = box.startswith("custom.")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 4, 6, 4)
        outer.setSpacing(4)
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        outer.addLayout(row1)
        # Custom Conduits carry more controls: name on its own row, the rest below
        h = QHBoxLayout() if self.is_custom else row1
        h.setSpacing(8)
        if self.is_custom:
            outer.addLayout(h)

        if self.is_custom:
            self.name = kit.CustomLineEdit("")
            self.name.setToolTip("Click to rename")
            self.name.setMinimumWidth(110)
            self.name.setMaximumWidth(360)
            self.name.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.name.editingFinished.connect(self._rename)
            row1.addWidget(self.name, 1)
            row1.addStretch(0)
            self.as_speaker = kit.CustomCheckBox("As Speaker")
            self.as_speaker.setToolTip("Selectable as a playback device. Largely automatic for any patch "
                                       "point -- there's no reliable way to hide it from speaker pickers.")
            self.as_mic = kit.CustomCheckBox("As Microphone")
            self.as_mic.setToolTip("A second linked device makes this conduit's mix selectable as a "
                                   "recording device (\"Stereo Mix\")")
            self.as_speaker.toggled.connect(lambda on: self._set_conduit("as_speaker", on))
            self.as_mic.toggled.connect(lambda on: self._set_conduit("as_microphone", on))
            h.addWidget(self.as_speaker)
            h.addWidget(self.as_mic)
            h.addStretch(1)
        else:
            self.name = QLabel(core.box_label(controller.state, box))
            self.name.setStyleSheet("QLabel { font-weight: bold; font-size: 14px; }")
            h.addWidget(self.name)
            h.addStretch(1)

        self.state_label = dlg.dim_label("", wrap=False)
        row1.addWidget(self.state_label)

        self.ns = kit.CustomButton(dlg.noise_suppression_button_text("none"))
        self.ns.clicked.connect(self._ns_menu)
        self.ns.setVisible(box != "speaker")
        h.addWidget(self.ns)

        h.addWidget(QLabel("Vol"))
        self.vol = kit.CustomLineEdit("1.0")
        self.vol.setFixedWidth(56)
        self.vol.setToolTip("Master volume multiplier -- 1.0 leaves it alone, no upper limit "
                            "(also: conduit --micVolume / --speakerVolume)")
        self.vol.editingFinished.connect(self._set_volume)
        h.addWidget(self.vol)
        self.mute = kit.CustomCheckBox("Mute")
        self.mute.setToolTip("Also: conduit --togglemute=<speaker|mic|conduit name>")
        self.mute.toggled.connect(self._set_mute)
        h.addWidget(self.mute)

        if self.is_custom:
            rm = kit.CustomButton("✕")
            rm.setToolTip("Remove this Custom Conduit")
            rm.clicked.connect(self._remove)
            row1.addWidget(rm)
        if fullscreen:
            self.fs = kit.CustomButton("⛶")
            self.fs.setToolTip("Fullscreen this box (Esc restores)")
            self.fs.clicked.connect(self.fullscreen_clicked.emit)
            row1.addWidget(self.fs)
        self.sync()

    # ---- model access
    def _hub(self) -> dict:
        if self.box in ("speaker", "mic"):
            return self.c.state[self.box]
        return core.conduit_by_box(self.c.state, self.box) or {}

    def sync(self):
        hub = self._hub()
        if not hub:
            return
        for w in (self.vol, self.mute, self.ns) + ((self.as_speaker, self.as_mic) if self.is_custom else ()):
            w.blockSignals(True)
        if self.is_custom:
            if not self.name.hasFocus():
                self.name.setText(hub.get("name", ""))
            self.as_speaker.setChecked(hub.get("as_speaker", True))
            self.as_mic.setChecked(hub.get("as_microphone", False))
        if not self.vol.hasFocus():
            self.vol.setText(f"{hub.get('volume', 1.0):g}")
        self.mute.setChecked(hub.get("muted", False))
        is_mic = core.box_is_microphone(self.c.state, self.box)
        self.ns.setEnabled(is_mic)
        self.ns.setToolTip("Noise suppression for this microphone" if is_mic
                           else "Only while this box acts as a microphone (As Microphone)")
        self.ns.setText(dlg.noise_suppression_button_text(core.ns_method(self.c.state, self.box)))
        self.state_label.setText("" if core.box_enabled(self.c.state, self.box) else "disabled")
        for w in (self.vol, self.mute, self.ns) + ((self.as_speaker, self.as_mic) if self.is_custom else ()):
            w.blockSignals(False)

    def _rename(self):
        hub = self._hub()
        name = self.name.text().strip() or f"Custom Conduit {hub.get('id')}"
        if hub and hub.get("name") != name:
            hub["name"] = name
            self.c.commit()

    def _set_conduit(self, key, on):
        hub = self._hub()
        if hub and hub.get(key) != on:
            hub[key] = on
            self.c.commit()

    def _set_volume(self):
        try:
            v = float(self.vol.text().replace("x", ""))
        except ValueError:
            self.sync()
            return
        hub = self._hub()
        if hub and hub.get("volume") != v:
            hub["volume"] = v
            self.c.commit()

    def _set_mute(self, on):
        hub = self._hub()
        if hub and hub.get("muted") != on:
            hub["muted"] = on
            self.c.commit()

    def _ns_menu(self):
        def pick(method):
            def apply():
                core.set_noise_suppression(self.c.state, "mic" if self.box == "mic" else self.box, method)
                self.c.commit()
            QTimer.singleShot(0, apply)      # never rebuild while the menu's exec() is running
        menu = dlg.noise_suppression_menu(self, core.ns_method(self.c.state, self.box), pick)
        menu.exec(self.ns.mapToGlobal(QPoint(0, self.ns.height())))

    def _remove(self):
        hub = self._hub()
        if not hub:
            return
        if kit.ask_confirm(self.window(), "Remove Custom Conduit",
                           f"Remove “{hub.get('name')}” and every cell and link in it?", "Remove", danger=True):
            core.remove_conduit(self.c.state, hub["id"])
            self.c.commit()
