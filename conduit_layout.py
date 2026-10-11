"""
conduit_layout.py -- the GUI's own file (~/.config/conduit/layout.json).

Cell positions, per-box zoom / scroll, splitter sizes, UI toggles and the
devices Conduit has seen (so a closed device can still be offered in New Cell
and drawn with the right ports).  The daemon never reads it, and saving it
never restarts the daemon.
"""
from __future__ import annotations

import json
import time

from PySide6.QtCore import QObject, QTimer

import conduit_core as core

KNOWN_DEVICE_LIMIT = 400

DEFAULT_UI = {
    "mode": "graphical",          # "graphical" | "list"
    "auto_apply": True,
    "auto_refresh": True,
    "show_unmanaged": False,
}


class Layout(QObject):
    def __init__(self, path=None, parent=None):
        super().__init__(parent)
        self.path = path or core.LAYOUT_FILE
        try:
            data = json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        self.data = {
            "positions": data.get("positions") if isinstance(data.get("positions"), dict) else {},
            "views": data.get("views") if isinstance(data.get("views"), dict) else {},
            "splitters": data.get("splitters") if isinstance(data.get("splitters"), dict) else {},
            "known_devices": data.get("known_devices") if isinstance(data.get("known_devices"), dict) else {},
            "ui": {**DEFAULT_UI, **(data.get("ui") if isinstance(data.get("ui"), dict) else {})},
            "window": data.get("window") if isinstance(data.get("window"), dict) else {},
        }
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self.save_now)

    # -- persistence
    def touch(self):
        self._timer.start()

    def save_now(self):
        self._timer.stop()
        try:
            core.atomic_write_json(self.path, self.data)
        except OSError:
            pass

    # -- positions
    def pos(self, cell_id):
        p = self.data["positions"].get(cell_id)
        if isinstance(p, list) and len(p) == 2:
            return float(p[0]), float(p[1])
        return None

    def set_pos(self, cell_id, x, y):
        self.data["positions"][cell_id] = [round(x, 1), round(y, 1)]
        self.touch()

    def forget(self, cell_id):
        if self.data["positions"].pop(cell_id, None) is not None:
            self.touch()

    # -- views
    def view(self, box):
        v = self.data["views"].get(box)
        return v if isinstance(v, dict) else {}

    def set_view(self, box, zoom, cx, cy):
        self.data["views"][box] = {"zoom": round(zoom, 4), "center": [round(cx, 1), round(cy, 1)]}
        self.touch()

    # -- splitters
    def splitter(self, name):
        s = self.data["splitters"].get(name)
        return s if isinstance(s, list) else None

    def set_splitter(self, name, sizes):
        self.data["splitters"][name] = list(sizes)
        self.touch()

    # -- ui
    def ui(self, key):
        return self.data["ui"].get(key, DEFAULT_UI.get(key))

    def set_ui(self, key, value):
        self.data["ui"][key] = value
        self.touch()

    # -- known devices
    def remember(self, snap: core.Snapshot):
        if not snap.ok:
            return
        now = int(time.time())
        changed = False
        for n in snap.nodes.values():
            if n.is_ours or not n.display_label:
                continue
            rec = self.data["known_devices"].get(n.display_label) or {}
            new = dict(rec)
            if n.output_ports:
                new["out"] = [ch for _, ch in n.output_ports]
            if n.input_ports:
                new["in"] = [ch for _, ch in n.input_ports]
            if n.media_class == "Audio/Sink":
                new["hw_sink"] = True
            new["class"] = n.media_class
            if new != rec or now - int(rec.get("seen", 0)) > 600:
                new["seen"] = now
            if new != rec:
                self.data["known_devices"][n.display_label] = new
                changed = True
        kd = self.data["known_devices"]
        if len(kd) > KNOWN_DEVICE_LIMIT:
            for label in sorted(kd, key=lambda k: kd[k].get("seen", 0))[:len(kd) - KNOWN_DEVICE_LIMIT]:
                del kd[label]
            changed = True
        if changed:
            self.touch()

    def known(self, label):
        rec = self.data["known_devices"].get(label)
        return rec if isinstance(rec, dict) else {}
