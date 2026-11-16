"""
conduit_meter.py -- real signal levels for the themes' meters (VU needles, LED bars, dB readouts,
the Mixing Desk's strips).

For every device the graph shows as carrying audio, one small `pw-record` capture (mono, 12 kHz,
f32) is linked to it with *passive* links: a meter never keeps a device awake, so when the device
goes quiet and suspends, its meter just reads silence and is stopped a few seconds later.

    device output ports --(passive)--> conduit_meter_<cell>:input_MONO   (pw-link)

Sinks (the Virtual Speaker, hardware outputs) are read from their monitor ports, capture-only
streams from whatever feeds them.  The capture nodes are named conduit_meter_* so neither the
daemon nor the graph routes or shows them.  Levels are dBFS with meter ballistics: peaks jump up
and fall back at RELEASE_DB_S, the RMS (what a VU needle shows) eases both ways.
"""
from __future__ import annotations

import array
import math
import subprocess
import time

from PySide6.QtCore import QObject, QProcess, QTimer, Signal
from PySide6.QtWidgets import QApplication

import conduit_core as core

RATE = 12000
FLOOR_DB = -60.0
RELEASE_DB_S = 24.0        # how fast a peak falls back
RMS_TAU_S = 0.12           # VU-ish integration
LINGER_S = 4.0             # keep a meter this long after its device stops carrying audio
MAX_METERS = 32
NODE_PREFIX = core.METER_NODE_PREFIX


def db(x: float) -> float:
    return 20.0 * math.log10(x) if x > 1e-6 else -120.0


def norm(level_db: float) -> float:
    """dBFS -> 0..1 for drawing (FLOOR_DB .. 0 dB)."""
    return max(0.0, min(1.0, (level_db - FLOOR_DB) / -FLOOR_DB))


class Meter:
    def __init__(self, key, node, ports):
        self.key, self.node_id, self.ports = key, node.id, tuple(ports)
        safe = "".join(ch if ch.isalnum() else "_" for ch in key)
        self.name = f"{NODE_PREFIX}{safe}"
        self.proc = None
        self.linked = False
        self.tries = 0
        self.buf = b""
        self.peak = 0.0            # since the last poll
        self.sumsq = 0.0
        self.count = 0
        self.peak_db = -120.0      # with ballistics
        self.rms_db = -120.0
        self.last_data = 0.0
        self.wanted_at = time.monotonic()


class MeterManager(QObject):
    """Owns the captures; `levels(cell_id)` gives (peak dB, rms dB) or None."""
    changed = Signal()

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.c = controller
        self.meters: dict = {}
        self._wants: dict = {}      # requester -> [cell ids]
        self.available = True       # pw-record / pw-link exist
        self._last = time.monotonic()
        self._link_timer = QTimer(self)
        self._link_timer.setInterval(120)
        self._link_timer.timeout.connect(self._link_pending)
        self.run = lambda cmd: subprocess.run(cmd, capture_output=True, text=True, timeout=2)   # tests replace
        self.spawn = self._spawn
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.stop_all)

    # -- what to meter
    @staticmethod
    def source_ports(state, cell_id, snap):
        """(node, [output port ids]) carrying what this cell plays / receives, or (None, [])."""
        nodes = core.cell_nodes(state, cell_id, "out", snap) or core.cell_nodes(state, cell_id, "in", snap)
        for n in nodes:
            outs = [pid for pid, _ch in n.output_ports]
            if outs:
                return n, outs
            ins = {pid for pid, _ch in n.input_ports}
            feeders = sorted({o for o, i in snap.links if i in ins})
            if feeders:
                return n, feeders
        return None, []

    def want(self, cell_ids, who="graph"):
        """`who` (the graph, the Mixing Desk) wants these cells metered; the union of every
        requester's cells is metered, the rest lapse after LINGER_S."""
        self._wants[who] = list(cell_ids)
        if not self.available:
            return
        st, snap = self.c.state, self.c.snap
        now = time.monotonic()
        wanted = {}
        union = list(dict.fromkeys(cid for ids in self._wants.values() for cid in ids))
        for cid in union[:MAX_METERS]:
            node, ports = self.source_ports(st, cid, snap)
            if node is not None and not node.name.startswith(core.GUI_PREFIXES):
                wanted[cid] = (node, ports)
        for cid, m in list(self.meters.items()):
            if cid in wanted:
                node, ports = wanted[cid]
                if (node.id, tuple(ports)) != (m.node_id, m.ports):
                    self._stop(cid)               # the device restarted / changed ports
                else:
                    m.wanted_at = now
            elif now - m.wanted_at > LINGER_S:
                self._stop(cid)
        for cid, (node, ports) in wanted.items():
            if cid not in self.meters:
                self._start(cid, node, ports)

    # -- reading
    def poll(self):
        """Fold the samples read since the last poll into each meter (call once per frame)."""
        now = time.monotonic()
        dt = max(1e-3, min(0.5, now - self._last))
        self._last = now
        fall = RELEASE_DB_S * dt
        k = 1.0 - math.exp(-dt / RMS_TAU_S)
        for m in self.meters.values():
            if m.count:
                pk = db(m.peak)
                rms = db(math.sqrt(m.sumsq / m.count))
                m.peak, m.sumsq, m.count = 0.0, 0.0, 0
            else:
                pk = rms = -120.0
            m.peak_db = pk if pk > m.peak_db else max(pk, m.peak_db - fall)
            m.rms_db = max(-120.0, m.rms_db + (rms - m.rms_db) * k) if rms > -120 or m.rms_db > -119 else -120.0

    def levels(self, cell_id):
        m = self.meters.get(cell_id)
        if m is None or not m.linked:
            return None
        return m.peak_db, m.rms_db

    # -- processes
    def _start(self, cid, node, ports):
        m = Meter(cid, node, ports)
        props = (f'{{ node.name = "{m.name}" node.description = "Conduit Meter" media.name = "Conduit Meter" '
                 'node.autoconnect = false node.passive = true node.dont-reconnect = true }')
        cmd = ["pw-record", "--target", "0", "--rate", str(RATE), "--channels", "1", "--format", "f32",
               "--latency", "40ms", "--raw", "-P", props, "-"]
        m.proc = self.spawn(cmd, m)
        if m.proc is None:
            return
        self.meters[cid] = m
        if not self._link_timer.isActive():
            self._link_timer.start()

    def _spawn(self, cmd, meter):
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.SeparateChannels)
        proc.readyReadStandardOutput.connect(lambda m=meter, p=proc: self._read(m, p))
        proc.errorOccurred.connect(lambda err, m=meter: self._error(m, err))
        proc.finished.connect(lambda *_a, m=meter: self._ended(m))
        proc.start(cmd[0], cmd[1:])
        return proc

    def _link_pending(self):
        pending = [m for m in self.meters.values() if not m.linked]
        if not pending:
            self._link_timer.stop()
            return
        for m in pending:
            m.tries += 1
            ok = True
            for pid in m.ports:
                try:
                    r = self.run(["pw-link", "--passive", str(pid), f"{m.name}:input_MONO"])
                except (OSError, subprocess.SubprocessError):
                    self.available = False
                    self.stop_all()
                    return
                if r.returncode != 0 and "exists" not in (r.stderr or ""):
                    ok = False
            if ok:
                m.linked = True
            elif m.tries > 25:                     # its port never showed up: try again later
                self._stop(m.key)

    def _read(self, m, proc):
        data = m.buf + bytes(proc.readAllStandardOutput())
        n = len(data) // 4
        m.buf = data[n * 4:]
        if not n:
            return
        a = array.array("f")
        a.frombytes(data[:n * 4])
        hi, lo = max(a), min(a)
        m.peak = max(m.peak, hi, -lo)
        m.sumsq += sum(x * x for x in a)
        m.count += n
        m.last_data = time.monotonic()

    def _error(self, m, err):
        if err == QProcess.FailedToStart:
            self.available = False
            self.stop_all()

    def _ended(self, m):
        if self.meters.get(m.key) is m:
            del self.meters[m.key]          # next want() starts it again if still needed
            self.changed.emit()

    def _stop(self, cid):
        m = self.meters.pop(cid, None)
        if m is None or m.proc is None:
            return
        proc = m.proc
        if isinstance(proc, QProcess):
            for sig in (proc.finished, proc.errorOccurred, proc.readyReadStandardOutput):
                try:
                    sig.disconnect()
                except (RuntimeError, TypeError):
                    pass
            proc.kill()
            proc.waitForFinished(200)
            proc.deleteLater()
        elif hasattr(proc, "kill"):
            proc.kill()

    def stop_all(self):
        for cid in list(self.meters):
            self._stop(cid)


_managers: dict = {}


def manager(controller) -> MeterManager:
    m = _managers.get(id(controller))
    if m is None:
        m = _managers[id(controller)] = MeterManager(controller, controller)
    return m
