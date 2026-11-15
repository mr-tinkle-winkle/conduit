"""
conduit_listen.py -- "Listen to this device": hear a hub or cell through your real speakers.

One `pw-loopback` per listened device, owned by the GUI and gone when Conduit closes (it is a
monitoring aid, not routing, so it is never saved to state.json).  The loopback's nodes are
named conduit_listen_* so neither the daemon nor the graph treats them as devices to route.

It follows the graph: when the device or the speakers change, restart (same name, new node --
e.g. an equaliser helper) or appear later, the loopback is restarted on the new pair; while the
device doesn't exist it just waits.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QObject, QProcess, QTimer, Signal
from PySide6.QtWidgets import QApplication

import conduit_core as core

RETRY_S = 3.0


class ListenManager(QObject):
    changed = Signal()

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.c = controller
        self.want: set = set()            # cell ids the user wants to hear
        self.procs: dict = {}             # cell id -> (QProcess, route)
        self.failed: dict = {}            # cell id -> (route, time) of a loopback that died
        self.spawn = self._spawn          # tests replace this
        controller.live_changed.connect(self.refresh)
        controller.state_changed.connect(self.refresh)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.stop_all)

    # -- api
    def is_on(self, cell_id: str) -> bool:
        return cell_id in self.want

    def is_playing(self, cell_id: str) -> bool:
        return cell_id in self.procs

    def toggle(self, cell_id: str, on=None):
        on = (cell_id not in self.want) if on is None else bool(on)
        if on and core.listen_supported(self.c.state, cell_id):
            self.want.add(cell_id)
        else:
            self.want.discard(cell_id)
        self.failed.pop(cell_id, None)
        self.refresh()

    def status(self, cell_id: str) -> str:
        """Why it is (not) playing, for tooltips / the EQ window."""
        if cell_id not in self.want:
            return "off"
        if cell_id in self.procs:
            return "listening"
        st, snap = self.c.state, self.c.snap
        if core.listen_source(st, cell_id, snap) is None:
            return "waiting for the device"
        if core.listen_sink(st, snap) is None:
            return "no speakers found"
        return "starting"

    def stop_all(self):
        self.want.clear()
        for key in list(self.procs):
            self._kill(key)

    # -- work
    def refresh(self):
        st, snap = self.c.state, self.c.snap
        for key in list(self.want):
            if not core.listen_supported(st, key):          # the cell was deleted
                self.want.discard(key)
        for key in list(self.procs):
            if key not in self.want:
                self._kill(key)
        for key in self.want:
            src = core.listen_source(st, key, snap)
            sink = core.listen_sink(st, snap)
            # ids too: a device that restarts keeps its name but gets a new node, and the loopback
            # (node.dont-reconnect) would otherwise sit on the dead one, silent
            route = ((src.name, src.id, sink.name, sink.id)
                     if src is not None and sink is not None and src.id != sink.id else None)
            cur = self.procs.get(key)
            if cur is not None and cur[1] == route:
                continue
            if cur is not None:
                self._kill(key)
            if route is None:
                continue
            fail = self.failed.get(key)
            if fail and fail[0] == route and time.monotonic() - fail[1] < RETRY_S:
                QTimer.singleShot(int(RETRY_S * 1000), self.refresh)
                continue
            label = core.cell_label(st, key) if not core.is_hub(key) else core.hub_display_name(st, key)
            proc = self.spawn(core.listen_command(key, label, src, sink))
            if proc is not None:
                self.procs[key] = (proc, route)
        self.changed.emit()

    def _spawn(self, cmd):
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.finished.connect(lambda *_a, p=proc: self._ended(p))
        proc.errorOccurred.connect(lambda *_a, p=proc: self._ended(p))
        proc.start(cmd[0], cmd[1:])
        return proc

    def _ended(self, proc):
        for key, (p, route) in list(self.procs.items()):
            if p is proc:
                del self.procs[key]
                self.failed[key] = (route, time.monotonic())
                QTimer.singleShot(int(RETRY_S * 1000) + 50, self.refresh)
                self.changed.emit()
        proc.deleteLater()

    def _kill(self, key):
        proc, _route = self.procs.pop(key)
        if isinstance(proc, QProcess):
            try:
                proc.finished.disconnect()
                proc.errorOccurred.disconnect()
            except (RuntimeError, TypeError):
                pass
            proc.kill()
            proc.waitForFinished(300)
            proc.deleteLater()
        elif hasattr(proc, "kill"):
            proc.kill()


_managers: dict = {}


def manager(controller) -> ListenManager:
    m = _managers.get(id(controller))
    if m is None:
        m = _managers[id(controller)] = ListenManager(controller, controller)
    return m
