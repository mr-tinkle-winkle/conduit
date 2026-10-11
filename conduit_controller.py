"""
conduit_controller.py -- the one place the GUI's editors read and change the config.

Both editors mutate ``controller.state`` (a v2 state dict) in place and then call
``commit()``.  With Auto-apply on, that writes state.json and (debounced) restarts
the daemon; with it off, the edit is kept pending and the window shows a dirty
marker until Save.  ``state_changed`` tells both editors to resync; ``live_changed``
fires after each PipeWire snapshot (taken on a worker thread -- pw-dump can take a
moment, and widgets must never be touched from a worker thread).
"""
from __future__ import annotations

import copy
import json
import threading

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

import conduit_core as core

RESTART_DEBOUNCE_MS = 600


class Controller(QObject):
    state_changed = Signal()
    live_changed = Signal()
    dirty_changed = Signal(bool)
    _snapshot_ready = Signal(object)

    def __init__(self, layout, parent=None):
        super().__init__(parent)
        self.layout = layout
        core.ensure_config_exists()
        self.state = core.load_state()
        self._last_written = copy.deepcopy(self.state)
        self.snap = core.Snapshot.empty()
        self.plan = core.compute_plan(self.state, self.snap)
        self.dirty = False
        self._status = {}
        self._refreshing = False
        self._refresh_again = False

        self._restart_timer = QTimer(self)
        self._restart_timer.setSingleShot(True)
        self._restart_timer.timeout.connect(core.restart_daemon)

        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(3000)
        self._auto_timer.timeout.connect(self.refresh)

        self._snapshot_ready.connect(self._on_snapshot)

        self._watcher = QFileSystemWatcher(self)
        self._watch()
        self._watcher.fileChanged.connect(self._on_file_changed)
        self._watcher.directoryChanged.connect(self._on_file_changed)
        self._recompute()

    # ------------------------------------------------------------------ editing
    @property
    def auto_apply(self) -> bool:
        return bool(self.layout.ui("auto_apply"))

    def set_auto_apply(self, on: bool):
        self.layout.set_ui("auto_apply", bool(on))
        if on and self.dirty:
            self.save()

    def commit(self):
        """Call after every edit of self.state."""
        self.state = core.normalize(self.state)
        self._recompute()
        if self.auto_apply:
            self._write()
            self._restart_timer.start(RESTART_DEBOUNCE_MS)
        else:
            self._set_dirty(True)
        self.state_changed.emit()

    def save(self):
        self._write()
        self._restart_timer.start(50)
        self._set_dirty(False)

    def revert(self):
        self.state = core.load_state()
        self._last_written = copy.deepcopy(self.state)
        self._set_dirty(False)
        self._recompute()
        self.state_changed.emit()

    def _write(self):
        core.save_state(self.state)
        self._last_written = copy.deepcopy(self.state)
        self._watch()

    def _set_dirty(self, d):
        if d != self.dirty:
            self.dirty = d
            self.dirty_changed.emit(d)

    # ------------------------------------------------------------- live graph
    def set_auto_refresh(self, on: bool):
        self.layout.set_ui("auto_refresh", bool(on))
        if on:
            self._auto_timer.start()
        else:
            self._auto_timer.stop()

    def start(self):
        if self.layout.ui("auto_refresh"):
            self._auto_timer.start()
        self.refresh()

    def refresh(self):
        if self._refreshing:
            self._refresh_again = True
            return
        self._refreshing = True
        threading.Thread(target=lambda: self._snapshot_ready.emit(core.Snapshot.capture(lambda m: None)),
                         daemon=True).start()

    def set_snapshot(self, snap):        # tests / offscreen rendering
        self._on_snapshot(snap)

    def _on_snapshot(self, snap):
        self._refreshing = False
        self.snap = snap
        self.layout.remember(snap)
        self._recompute()
        self.live_changed.emit()
        if self._refresh_again:
            self._refresh_again = False
            self.refresh()

    def _recompute(self):
        self.plan = core.compute_plan(self.state, self.snap)
        self._status = {}

    # ---------------------------------------------------------- external edits
    def _watch(self):
        for p in (str(core.STATE_FILE), str(core.CONFIG_DIR)):
            if p not in self._watcher.files() + self._watcher.directories():
                try:
                    self._watcher.addPath(p)
                except Exception:  # noqa: BLE001
                    pass

    def _on_file_changed(self, *_):
        self._watch()
        raw = core.read_raw_state()
        if raw is None:
            return
        disk = core.normalize(raw)
        if disk == core.normalize(self._last_written):
            return
        if not self.dirty:
            self.state = disk
        else:
            # keep pending edits; take only what the daemon writes (event blocks)
            blocks = {c["id"]: c.get("blocked", False) for c in disk["cells"]}
            for c in self.state["cells"]:
                if c["id"] in blocks:
                    c["blocked"] = blocks[c["id"]]
        self._last_written = copy.deepcopy(disk)
        self._recompute()
        self.state_changed.emit()

    # ------------------------------------------------------------- queries
    def cell_status(self, cell_id: str) -> str:
        """'blocked' | 'closed' | 'inactive' | 'active' (blocked wins)."""
        if cell_id in self._status:
            return self._status[cell_id]
        st = "active"
        if not core.is_hub(cell_id):
            c = core.cell_by_id(self.state, cell_id)
            if c and c.get("blocked"):
                st = "blocked"
        if st != "blocked":
            if not self.snap.ok:
                st = "inactive"
            else:
                nodes = core.cell_all_nodes(self.state, cell_id, self.snap) if not core.is_hub(cell_id) else \
                    [n for n in (core.hub_node(self.state, cell_id, s, self.snap) for s in ("out", "in")) if n]
                if not nodes:
                    st = "closed"
                elif core.is_hub(cell_id):
                    st = "active"
                elif not any(n.running for n in nodes):
                    st = "inactive"
        self._status[cell_id] = st
        return st

    def cell_ports(self, cell_id: str) -> dict:
        """{'in': [channels], 'out': [channels]} to draw for a cell."""
        res = {"in": [], "out": []}
        if core.is_hub(cell_id):
            for side in ("in", "out"):
                n = core.hub_node(self.state, cell_id, side, self.snap)
                chans = [ch for _, ch in n.ports(side)] if n is not None else []
                res[side] = chans or list(core.STEREO)
            if cell_id.endswith(".mic"):
                res["in"] = []           # the tap is fed internally
            return res
        cell = core.cell_by_id(self.state, cell_id)
        if cell is None:
            return res
        for side in ("in", "out"):
            nodes = core.cell_nodes(self.state, cell_id, side, self.snap)
            own = [n for n in nodes if n.display_label == cell["label"]] or nodes
            if own:
                res[side] = [ch for _, ch in own[0].ports(side)]
        if res["in"] or res["out"]:
            return res
        known = self.layout.known(cell["label"])
        if known.get("in") or known.get("out"):
            return {"in": list(known.get("in", [])), "out": list(known.get("out", []))}
        for l in self.state["links"]:
            if l["src"] == cell_id and l["src_port"] != "*" and l["src_port"] not in res["out"]:
                res["out"].append(l["src_port"])
            if l["dst"] == cell_id and l["dst_port"] != "*" and l["dst_port"] not in res["in"]:
                res["in"].append(l["dst_port"])
        if not res["in"] and not res["out"]:
            res = {"in": list(core.STEREO), "out": list(core.STEREO)}
        if cell.get("speaker") and not res["in"]:
            res["in"] = list(core.STEREO)
        return res

    def link_live(self, link_id: str) -> bool:
        return core.link_live(self.plan, self.snap, link_id)

    def eligible_labels(self, side: str) -> list:
        pred = core.is_producer if side == "out" else core.is_consumer
        labels = {n.display_label for n in self.snap.nodes.values() if pred(n) and n.display_label}
        return sorted(labels, key=str.lower)

    # ------------------------------------------------------------- helpers
    def link_cells(self, src, src_ports, dst, dst_ports, src_ch, dst_ch, **extra):
        """Create the links for one drag: a single port, or a Both ("*") port that drags a left
        and a right link together.  Returns the links made."""
        made = []
        s_list = [c for c in src_ports if c != "*"] if src_ch == "*" else [src_ch]
        d_list = [c for c in dst_ports if c != "*"] if dst_ch == "*" else [dst_ch]
        if not s_list or not d_list:
            return made
        if src_ch == "*" and dst_ch == "*":
            pairs = [(c, c) for c in s_list if c in d_list]
            if not pairs:
                if len(s_list) == 1:
                    pairs = [(s_list[0], d) for d in d_list]
                elif len(d_list) == 1:
                    pairs = [(s, d_list[0]) for s in s_list]
                else:
                    pairs = list(zip(s_list, d_list))
        else:
            pairs = [(s, d) for s in s_list for d in d_list]
        for s, d in pairs:
            made.append(core.new_link(self.state, src, s, dst, d, blocked=False, **extra))
        # bypass: an app linked straight to a real speaker stays off the Virtual Speaker
        dst_cell = core.cell_by_id(self.state, dst)
        if made and not core.is_hub(src) and dst_cell is not None and dst_cell.get("speaker"):
            exists = any(l["src"] == src and l["dst"] == core.HUB_SPEAKER and l.get("blocked")
                         for l in self.state["links"])
            if not exists:
                core.new_link(self.state, src, "*", core.HUB_SPEAKER, "*", blocked=True, anti=True)
        return made

    def ensure_cell(self, label: str, box: str, **extra) -> dict:
        for c in self.state["cells"]:
            if c["box"] == box and c["label"] == label:
                for k, v in extra.items():
                    c[k] = v
                return c
        return core.new_cell(self.state, label, box, **extra)

    def snapshot_json(self):
        return json.dumps(self.state, sort_keys=True)
