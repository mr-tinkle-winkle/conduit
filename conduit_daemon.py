#!/usr/bin/env python3
"""
conduit_daemon.py -- PipeWire router for Conduit.

Two static virtual devices come from the NixOS module (module.nix):

  conduit_virtual_speaker   Audio/Sink            apps play into it; its monitor feeds speakers
  conduit_virtual_mic       Audio/Source/Virtual  real mics are linked into it; apps record from it

Everything else is plumbing driven by ~/.config/conduit/state.json (schema v2 --
see conduit_core.py).  The daemon is EVENT DRIVEN:

  - a `pw-dump --monitor` child wakes it the moment anything in the PipeWire
    graph changes (a stream appears, WirePlumber makes a link, ...), and it
    reconciles ~20 ms later.  This matters: WirePlumber re-links streams to the
    default sink whenever it rescans, and until Conduit removes such a link the
    stream is heard twice (once direct, once through Conduit's own path).  With
    the old 2-second poll that doubling lasted up to 2 s; now it is a few
    tens of milliseconds.
  - it watches state.json's mtime, so GUI edits apply within ~100 ms WITHOUT a
    service restart (restarts used to tear down the noise-suppression helpers on
    every edit).
  - it also re-checks on a timer: the "polling rate" (state.json "daemon": {"poll_ms"}, set
    from the GUI).  With "instant" off the timer is the only trigger.

Each cycle takes one `pw-dump` and:

  1. creates / renames / destroys Custom Conduit nodes  (pw-cli create-node adapter)
  2. creates / re-pins / destroys per-link gain nodes   (one per link whose
     src-cell x link x dst-cell volume isn't 1.0)
  3. starts / stops helper processes: noise suppression and equalisers
     (one `pipewire -c <generated config>` each; they die with the service)
  4. computes the plan (conduit_core.compute_plan) and reconciles links:
       - every configured, unblocked link's port pairs are linked
       - any other link between two nodes Conduit manages a link between is
         pruned (so per-port links mean exactly those ports)
       - blocked links (anti-links) are removed wherever they appear, even when
         WirePlumber made them
       - every link of a blocked cell's nodes is removed
       - what was managed last cycle is cached on disk (.link_cache.json, by
         node NAME) so a link removed from the config is undone once
  5. pushes equaliser control changes live (pw-cli set-param)
  6. pins the default sink / source and the hubs' master volume / mute (wpctl),
     only when they differ from what PipeWire reports
  7. runs cell events (Active / Created / Inactive / Deleted -> block / unblock
     other cells), writing the resulting blocks back into state.json
"""
from __future__ import annotations

import atexit
import collections
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import conduit_core as core  # noqa: E402
import conduit_eq  # noqa: E402

SLOW_INTERVAL = 2.0             # how often values PipeWire doesn't report back are re-pinned
EVENT_DEBOUNCE = 0.02           # let a burst of graph events settle before reconciling
MIN_CYCLE_GAP = 0.1             # never reconcile more often than this ...
BUSY_CYCLE_GAP = 0.5            # ... and back off to this when the graph keeps changing
BUSY_CYCLES_PER_5S = 15
ACTIVE_HOLD_SECONDS = 2.5       # "becomes Active / Inactive" must hold this long
VOLUME_BOOST_LIMIT = 10.0
MASTER_VOLUME_BOOST_LIMIT = 1000.0
VOLUME_TOLERANCE = 0.006

_RNNOISE_LADSPA_PATH = os.environ.get("CONDUIT_RNNOISE_LADSPA_PATH")
_HELPER_CONFIG_DIR = Path(tempfile.gettempdir()) / "conduit-helpers"
MASTER_CACHE_FILE_NAME = ".master_cache.json"


def log(msg: str) -> None:
    print(f"conduit-daemon: {msg}", flush=True)


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


# ---------------------------------------------------------------------------
# dynamic nodes
# ---------------------------------------------------------------------------

def sanitize_description(description: str) -> str:
    return description.replace('"', "'")


def create_dynamic_node(node_name, description, media_class, positions=("FL", "FR")):
    description = sanitize_description(description)
    pos = ",".join(positions)
    args = ("{ factory.name=support.null-audio-sink "
            f'node.name="{node_name}" node.description="{description}" '
            f"media.class={media_class} object.linger=true "
            f"audio.position=[{pos}] monitor.channel-volumes=true }}")
    r = run(["pw-cli", "create-node", "adapter", args])
    if r.returncode != 0:
        log(f"failed to create {node_name!r}: {r.stderr.strip()}")
    else:
        log(f"created dynamic node {node_name!r} ({media_class})")


def destroy_dynamic_node(node_id, node_name):
    r = run(["pw-cli", "destroy", str(node_id)])
    if r.returncode != 0:
        log(f"failed to destroy {node_name!r} (id={node_id}): {r.stderr.strip()}")
    else:
        log(f"destroyed dynamic node {node_name!r} (id={node_id})")


def sync_custom_conduits(state, snap):
    expected = set()
    for c in state["custom"]["conduits"]:
        base_name = core.custom_node_name(c["id"])
        expected.add(base_name)
        desc = c.get("name") or base_name
        node = snap.by_name.get(base_name)
        if node is None:
            create_dynamic_node(base_name, desc, "Audio/Sink")
        elif node.description != sanitize_description(desc):
            destroy_dynamic_node(node.id, base_name)
            create_dynamic_node(base_name, desc, "Audio/Sink")
        if c.get("as_microphone"):
            mic_name = core.custom_mic_node_name(c["id"])
            expected.add(mic_name)
            mdesc = f"{desc} (Mic)"
            mnode = snap.by_name.get(mic_name)
            if mnode is None:
                create_dynamic_node(mic_name, mdesc, "Audio/Source/Virtual")
            elif mnode.description != sanitize_description(mdesc):
                destroy_dynamic_node(mnode.id, mic_name)
                create_dynamic_node(mic_name, mdesc, "Audio/Source/Virtual")
    for node in list(snap.nodes.values()):
        if node.name.startswith(core.CUSTOM_NODE_PREFIX) and node.name not in expected:
            destroy_dynamic_node(node.id, node.name)


class Applied:
    """What this process last pushed with wpctl, so a value PipeWire doesn't report back isn't
    re-sent every cycle (each re-send is itself a graph event, i.e. another cycle)."""

    def __init__(self):
        self.volume = {}     # node id -> volume
        self.mute = {}       # node id -> bool

    def forget_missing(self, snap):
        for d in (self.volume, self.mute):
            for nid in [k for k in d if k not in snap.nodes]:
                del d[nid]

    def set_volume(self, node, volume, limit, force=False):
        if node.volume is not None:
            if abs(node.volume - volume) <= VOLUME_TOLERANCE:
                return
        elif not force and self.volume.get(node.id) == volume:
            return
        run(["wpctl", "set-volume", str(node.id), f"{volume}", "-l", str(limit)])
        self.volume[node.id] = volume

    def set_mute(self, node, muted, force=False):
        if node.mute is not None:
            if node.mute == muted:
                return
        elif not force and self.mute.get(node.id) == muted:
            return
        run(["wpctl", "set-mute", str(node.id), "1" if muted else "0"])
        self.mute[node.id] = muted


def sync_gain_nodes(specs, snap, applied, slow):
    for name, (volume, stereo) in specs.items():
        node = snap.by_name.get(name)
        if node is None:
            create_dynamic_node(name, "Conduit Gain", "Audio/Sink", ("FL", "FR") if stereo else ("MONO",))
        else:
            applied.set_volume(node, volume, VOLUME_BOOST_LIMIT, force=slow)
    for node in list(snap.nodes.values()):
        if node.name.startswith(core.GAIN_NODE_PREFIX) and node.name not in specs:
            destroy_dynamic_node(node.id, node.name)


# ---------------------------------------------------------------------------
# helper processes (noise suppression, equalisers)
# ---------------------------------------------------------------------------

_PREREQ_MODULES = """\
    { name = libpipewire-module-rt args = { } flags = [ ifexists nofail ] }
    { name = libpipewire-module-protocol-native }
    { name = libpipewire-module-client-node }
    { name = libpipewire-module-adapter }
"""


def _rnnoise_config(in_name, out_name, description):
    description = sanitize_description(description)
    return f"""
context.modules = [
{_PREREQ_MODULES}    {{ name = libpipewire-module-filter-chain
        args = {{
            node.description = "Conduit Noise Suppression (RNNoise) - {description}"
            filter.graph = {{
                nodes = [
                    {{
                        type = ladspa
                        name = rnnoise
                        plugin = "{_RNNOISE_LADSPA_PATH}"
                        label = noise_suppressor_stereo
                        control = {{ "VAD Threshold (%)" = 50.0 }}
                    }}
                ]
            }}
            capture.props = {{
                node.name = "{in_name}"
                media.class = Audio/Sink
                audio.position = [ FL FR ]
            }}
            playback.props = {{
                node.name = "{out_name}"
                media.class = Audio/Source
                audio.position = [ FL FR ]
            }}
        }}
    }}
]
"""


def _webrtc_config(in_name, out_name, description):
    description = sanitize_description(description)
    return f"""
context.modules = [
{_PREREQ_MODULES}    {{ name = libpipewire-module-echo-cancel
        args = {{
            library.name = aec/libspa-aec-webrtc
            aec.args = "webrtc.noise_suppression=true webrtc.gain_control=true webrtc.high_pass_filter=true webrtc.voice_detection=false"
            capture.props = {{ node.name = "{in_name}" node.description = "Conduit Noise Suppression (WebRTC) - {description}" node.passive = true }}
            source.props = {{ node.name = "{out_name}" node.description = "Conduit Noise Suppression (WebRTC) - {description}" }}
            sink.props = {{ node.name = "{in_name}_sink_unused" node.passive = true }}
            playback.props = {{ node.name = "{in_name}_playback_unused" node.passive = true }}
        }}
    }}
]
"""


class Helpers:
    """`pipewire -c <config>` children, keyed by a name; ``sig`` says when one must be restarted."""

    RESTART_COOLDOWN = 3.0

    def __init__(self):
        self.procs = {}      # key -> {"popen", "path", "sig"}
        self.last_start = {}
        atexit.register(self.stop_all)

    def spawn(self, config_text, key):
        """Overridable (tests).  Returns a Popen-like object or None."""
        _HELPER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        path = _HELPER_CONFIG_DIR / (key.replace(":", "_").replace(".", "_") + ".conf")
        path.write_text(config_text)
        try:
            return subprocess.Popen(["pipewire", "-c", str(path)]), path
        except FileNotFoundError:
            log("pipewire binary not found on PATH, can't start helper")
            return None, path

    def ensure(self, key, sig, make_config):
        cur = self.procs.get(key)
        if cur is not None:
            if cur["popen"].poll() is not None:
                log(f"helper {key!r} exited (code {cur['popen'].returncode}); restarting")
                self.procs.pop(key, None)
            elif cur["sig"] != sig:
                self.stop(key)
                self.last_start.pop(key, None)
            else:
                return
        if time.monotonic() - self.last_start.get(key, -1e9) < self.RESTART_COOLDOWN:
            return                   # a helper that keeps dying isn't respawned every cycle
        text = make_config()
        if text is None:
            return
        self.last_start[key] = time.monotonic()
        popen, path = self.spawn(text, key)
        if popen is not None:
            self.procs[key] = {"popen": popen, "path": path, "sig": sig}
            log(f"started helper {key!r} (pid {getattr(popen, 'pid', '?')})")

    def stop(self, key):
        entry = self.procs.pop(key, None)
        if entry is None:
            return
        popen = entry["popen"]
        popen.terminate()
        try:
            popen.wait(timeout=2)
        except subprocess.TimeoutExpired:
            popen.kill()
            try:
                popen.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        try:
            if entry.get("path"):
                Path(entry["path"]).unlink(missing_ok=True)
        except OSError:
            pass
        log(f"stopped helper {key!r}")

    def stop_all(self):
        for key in list(self.procs):
            self.stop(key)

    def keep_only(self, wanted):
        for key in list(self.procs):
            if key not in wanted:
                self.stop(key)


def sync_helpers(state, helpers):
    wanted = set()
    for box in core.all_boxes(state):
        desc = {"speaker": "Conduit Virtual Speaker", "mic": "Conduit Virtual Mic"}.get(box) or core.box_label(state, box)
        method = core.ns_method(state, box)
        if method in core.NOISE_SUPPRESSION_METHODS:
            key = f"ns:{box}:{method}"
            wanted.add(key)

            def make(method=method, box=box, desc=desc):
                in_name, out_name = core.noise_processor_names(box, method)
                if method == "rnnoise":
                    if not _RNNOISE_LADSPA_PATH:
                        log(f"can't start RNNoise for {box!r} -- CONDUIT_RNNOISE_LADSPA_PATH isn't set "
                            f"(is services.conduit.noiseSuppression.enable on, and rebuilt?)")
                        return None
                    return _rnnoise_config(in_name, out_name, desc)
                return _webrtc_config(in_name, out_name, desc)
            helpers.ensure(key, method, make)
        if core.eq_enabled(state, box):
            eq = core.eq_config(state, box)
            key = f"eq:{box}"
            wanted.add(key)
            helpers.ensure(key, conduit_eq.structure(eq),
                           lambda box=box, desc=desc, eq=eq: conduit_eq.config_text(box, desc, eq, _PREREQ_MODULES))
    helpers.keep_only(wanted)


class EqControl:
    """Pushes frequency / Q / gain changes into running equalisers without restarting them."""
    MAX_TRIES = 4

    def __init__(self):
        self.sent = {}       # box -> controls last sent (when PipeWire doesn't report them back)
        self.tries = {}      # box -> consecutive pushes that didn't stick

    def sync(self, state, snap, helpers):
        for box in core.all_boxes(state):
            if not core.eq_enabled(state, box):
                self.sent.pop(box, None)
                self.tries.pop(box, None)
                continue
            ein, eout = core.eq_nodes(state, box, snap)
            if ein is None:
                continue
            want = conduit_eq.controls(core.eq_config(state, box))
            carrier = next((n for n in (ein, eout) if n.controls and "eq_pre:Gain" in n.controls), None)
            if carrier is not None:
                diff = {k: v for k, v in want.items() if abs(float(carrier.controls.get(k, 1e9)) - v) > 1e-3}
                if not diff:
                    self.tries.pop(box, None)
                    continue
                self.tries[box] = self.tries.get(box, 0) + 1
                if self.tries[box] > self.MAX_TRIES:
                    # the live push isn't sticking on this PipeWire: restart with the values baked in
                    log(f"equaliser {box!r}: live update didn't apply, restarting helper")
                    helpers.stop(f"eq:{box}")
                    helpers.last_start.pop(f"eq:{box}", None)
                    self.tries.pop(box, None)
                    continue
                target = carrier
            else:
                if self.sent.get(box) == want:
                    continue
                diff, target = want, ein
            r = run(["pw-cli", "set-param", str(target.id), "Props", conduit_eq.set_param_arg(diff)])
            if r.returncode != 0:
                log(f"equaliser {box!r}: set-param failed: {r.stderr.strip()}")
            self.sent[box] = want


# ---------------------------------------------------------------------------
# links
# ---------------------------------------------------------------------------

def pw_link(o, i):
    r = run(["pw-link", str(o), str(i)])
    err = r.stderr.strip().lower()
    if r.returncode != 0 and "already linked" not in err and "file exists" not in err:
        log(f"pw-link {o} {i} failed: {r.stderr.strip()}")
    else:
        log(f"linked {o} -> {i}")


def pw_unlink(o, i):
    run(["pw-link", "-d", str(o), str(i)])
    log(f"unlinked {o} -> {i}")


def _load_link_cache(snap):
    """Node pairs managed last cycle, stored by node NAME (ids are reused across PipeWire
    restarts -- an id-based cache could prune links between unrelated nodes after a reboot)."""
    try:
        data = json.loads(core.LINK_CACHE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError, OSError):
        return set()
    ids = {}
    for n in snap.nodes.values():
        ids.setdefault(n.name, []).append(n.id)
    out = set()
    for p in data if isinstance(data, list) else []:
        if isinstance(p, list) and len(p) == 2 and all(isinstance(x, str) for x in p):
            for a in ids.get(p[0], ()):
                for b in ids.get(p[1], ()):
                    out.add((a, b))
    return out


_last_cache_written = None


def _save_link_cache(pairs, snap):
    global _last_cache_written
    names = sorted({(snap.nodes[a].name, snap.nodes[b].name) for a, b in pairs
                    if a in snap.nodes and b in snap.nodes})
    if names == _last_cache_written:
        return
    try:
        core.LINK_CACHE_FILE.write_text(json.dumps([list(p) for p in names]))
        _last_cache_written = names
    except OSError:
        pass


def reconcile(plan, snap):
    prunable = plan.managed_pairs | _load_link_cache(snap)
    for o, i in snap.links:
        no, ni = snap.port_owner.get(o), snap.port_owner.get(i)
        if (o, i) in plan.forbidden or no in plan.blocked_nodes or ni in plan.blocked_nodes:
            pw_unlink(o, i)
        elif (no, ni) in prunable and (o, i) not in plan.desired:
            pw_unlink(o, i)
    for o, i in plan.desired:
        if (o, i) not in snap.links:
            pw_link(o, i)
    _save_link_cache(plan.managed_pairs, snap)


def enforce_defaults(snap, mic_default, slow):
    vs = snap.by_name.get(core.VIRTUAL_SPEAKER)
    target = mic_default or snap.by_name.get(core.VIRTUAL_MIC)
    known = bool(snap.defaults)
    if vs and ((known and snap.defaults.get("sink") != vs.name) or (not known and slow)):
        run(["wpctl", "set-default", str(vs.id)])
    if target and ((known and snap.defaults.get("source") != target.name) or (not known and slow)):
        run(["wpctl", "set-default", str(target.id)])


def _master_cache_path():
    return core.CONFIG_DIR / MASTER_CACHE_FILE_NAME


def _load_master_cache():
    try:
        d = json.loads(_master_cache_path().read_text())
        return d if isinstance(d, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def enforce_master_controls(state, snap, applied, slow):
    """Master volume: 1.0 means "leave the device's volume alone" -- except right after it was
    something else, when it is put back to 1.0 once (remembered across restarts)."""
    boosted = _load_master_cache()
    changed = False
    hubs = [(core.VIRTUAL_SPEAKER, state["speaker"]), (core.VIRTUAL_MIC, state["mic"])]
    hubs += [(core.custom_node_name(c["id"]), c) for c in state["custom"]["conduits"]]
    for name, hub in hubs:
        node = snap.by_name.get(name)
        if node is None:
            continue
        volume = hub["volume"]
        if volume != 1.0:
            applied.set_volume(node, volume, MASTER_VOLUME_BOOST_LIMIT, force=slow)
            if boosted.get(name) != volume:
                boosted[name] = volume
                changed = True
        elif name in boosted:
            applied.set_volume(node, 1.0, MASTER_VOLUME_BOOST_LIMIT, force=True)
            del boosted[name]
            changed = True
        applied.set_mute(node, bool(hub["muted"]), force=slow)
    if changed:
        try:
            core.atomic_write_json(_master_cache_path(), boosted)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# events
# ---------------------------------------------------------------------------

class EventEngine:
    """Watches every cell that has events.  The first observation of a cell is only a
    baseline (events never fire for what already existed when the daemon started)."""

    def __init__(self, hold=ACTIVE_HOLD_SECONDS):
        self.hold = hold
        self.seen = {}      # cell id -> {"exists", "active", "pending", "since"}

    def step(self, state, snap, now=None):
        now = time.monotonic() if now is None else now
        fired = []
        for cell in state["cells"]:
            if not cell.get("events"):
                continue
            nodes = core.cell_all_nodes(state, cell["id"], snap)
            exists = bool(nodes)
            active = any(n.running for n in nodes)
            prev = self.seen.get(cell["id"])
            if prev is None:
                self.seen[cell["id"]] = {"exists": exists, "active": active, "pending": active, "since": now}
                continue
            if exists != prev["exists"]:
                fired.append((cell, "created" if exists else "deleted"))
                prev["exists"] = exists
            if active != prev["pending"]:
                prev["pending"], prev["since"] = active, now
            if prev["pending"] != prev["active"] and now - prev["since"] >= self.hold:
                prev["active"] = prev["pending"]
                fired.append((cell, "active" if prev["active"] else "inactive"))
        live_ids = {c["id"] for c in state["cells"]}
        for cid in list(self.seen):
            if cid not in live_ids:
                del self.seen[cid]
        return fired

    @staticmethod
    def apply(state, fired):
        """Apply outcomes to the in-memory state and persist only the block flags, re-reading the
        file first so a concurrent GUI edit isn't overwritten wholesale."""
        changes = {}
        for cell, kind in fired:
            for ev in cell.get("events", []):
                if ev.get("kind") != kind:
                    continue
                for target, action in ev.get("outcomes", {}).items():
                    changes[target] = action == "block"
                    log(f"event: {cell['label']!r} {core.EVENT_KIND_LABELS[kind].lower()} -> {action} {target}")
        if not changes:
            return False
        changed = False
        for c in state["cells"]:
            if c["id"] in changes and c.get("blocked", False) != changes[c["id"]]:
                c["blocked"] = changes[c["id"]]
                changed = True
        if not changed:
            return False
        raw = core.read_raw_state()
        if not isinstance(raw, dict) or core.is_v1(raw):
            return True
        for c in raw.get("cells", []):
            if isinstance(c, dict) and c.get("id") in changes:
                c["blocked"] = changes[c["id"]]
        try:
            core.atomic_write_json(core.STATE_FILE, raw)
        except OSError as e:
            log(f"couldn't persist event blocks: {e}")
        return True


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

class Runtime:
    def __init__(self):
        self.events = EventEngine()
        self.helpers = Helpers()
        self.applied = Applied()
        self.eq = EqControl()


def print_inventory(snap):
    log(f"node inventory ({len(snap.nodes)} nodes):")
    for n in sorted(snap.nodes.values(), key=lambda n: n.display_label.lower()):
        log(f"  {n.display_label!r} (name={n.name!r} class={n.media_class!r} "
            f"in_ports={len(n.input_ports)} out_ports={len(n.output_ports)} "
            f"client_id={n.client_id} state={n.state})")


def run_cycle(state, snap, rt, slow=True, now=None):
    """One reconcile pass.  Returns True when an event changed the config (so the caller
    should run another pass right away)."""
    rt.applied.forget_missing(snap)
    sync_custom_conduits(state, snap)
    sync_gain_nodes(core.gain_specs(state), snap, rt.applied, slow)
    sync_helpers(state, rt.helpers)
    plan = core.compute_plan(state, snap)
    reconcile(plan, snap)
    rt.eq.sync(state, snap, rt.helpers)
    enforce_defaults(snap, plan.mic_default, slow)
    enforce_master_controls(state, snap, rt.applied, slow)
    fired = rt.events.step(state, snap, now)
    return EventEngine.apply(state, fired) if fired else False


class GraphMonitor(threading.Thread):
    """Runs `pw-dump --monitor` and sets ``wake`` whenever it prints anything, i.e. whenever
    the PipeWire graph changed.  The output itself is discarded -- each cycle takes its own
    fresh, complete `pw-dump`."""

    def __init__(self, wake):
        super().__init__(daemon=True)
        self.wake = wake
        self.alive = False

    def run(self):
        while True:
            try:
                proc = subprocess.Popen(["pw-dump", "--monitor"], stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, bufsize=0)
            except OSError as e:
                log(f"can't start pw-dump --monitor ({e}); falling back to polling")
                time.sleep(10)
                continue
            self.alive = True
            try:
                while True:
                    chunk = proc.stdout.read(65536)
                    if not chunk:
                        break
                    self.wake.set()
            except OSError:
                pass
            self.alive = False
            try:
                proc.kill()
                proc.wait(timeout=2)
            except (OSError, subprocess.SubprocessError):
                pass
            time.sleep(2)       # PipeWire restarting: try again shortly


def _state_mtime():
    try:
        return core.STATE_FILE.stat().st_mtime_ns
    except OSError:
        return None


def main():
    core.ensure_config_exists()
    log(f"starting, watching {core.STATE_FILE}")
    rt = Runtime()
    wake = threading.Event()
    GraphMonitor(wake).start()
    printed = False
    last_cycle = last_slow = 0.0
    seen_mtime = object()
    state = core.load_state()
    recent = collections.deque(maxlen=64)
    while True:
        try:
            cfg = state.get("daemon") or core.DEFAULT_DAEMON
            poll = cfg["poll_ms"] / 1000.0        # the "polling rate" setting
            fired = wake.wait(min(0.1, poll / 2))
            if fired and not cfg["instant"]:
                wake.clear()                      # events are switched off: timer only
                fired = False
            now = time.monotonic()
            mtime = _state_mtime()
            state_changed = mtime != seen_mtime
            poll_due = now - last_cycle >= poll
            slow_due = now - last_slow >= SLOW_INTERVAL
            if not (fired or state_changed or poll_due):
                continue
            if fired and not state_changed:
                time.sleep(EVENT_DEBOUNCE)
                busy = sum(1 for t in recent if now - t < 5.0) >= BUSY_CYCLES_PER_5S
                gap = BUSY_CYCLE_GAP if busy else MIN_CYCLE_GAP
                wait = gap - (time.monotonic() - last_cycle)
                if wait > 0:
                    time.sleep(wait)
            wake.clear()
            if state_changed:
                seen_mtime = mtime
                state = core.load_state()
            snap = core.Snapshot.capture(log)
            last_cycle = time.monotonic()
            recent.append(last_cycle)
            if not snap.ok:
                last_slow = last_cycle
                continue
            if not printed:
                print_inventory(snap)
                printed = True
            slow = slow_due or state_changed
            if slow:
                last_slow = last_cycle
            if run_cycle(state, snap, rt, slow=slow):
                wake.set()          # an event blocked / unblocked something: apply it now
        except Exception:
            traceback.print_exc()
            time.sleep(0.5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
