#!/usr/bin/env python3
"""
conduit_daemon.py -- PipeWire router for Conduit.

Two static virtual devices come from the NixOS module (module.nix):

  conduit_virtual_speaker   Audio/Sink            apps play into it; its monitor feeds speakers
  conduit_virtual_mic       Audio/Source/Virtual  real mics are linked into it; apps record from it

Everything else is plumbing driven by ~/.config/conduit/state.json (schema v2 --
see conduit_core.py for the full format).  The GUI writes that file and restarts
this service; the daemon re-reads it every cycle anyway.  Every POLL_INTERVAL
seconds it takes one `pw-dump` and:

  1. creates / renames / destroys Custom Conduit nodes  (pw-cli create-node adapter)
  2. creates / re-pins / destroys per-link gain nodes   (one per link whose
     src-cell x link x dst-cell volume isn't 1.0)
  3. starts / stops noise-suppression helper processes   (one `pipewire -c` each)
  4. computes the plan (conduit_core.compute_plan) and reconciles links:
       - every configured, unblocked link's port pairs are linked
       - any other link between two nodes Conduit manages a link between is
         pruned (so per-port links mean exactly those ports, e.g. "mono")
       - blocked links (anti-links) are removed wherever they appear, even when
         WirePlumber made them -- including the bypass anti-link that keeps an
         app off the Virtual Speaker
       - every link of a blocked cell's nodes is removed
       - what was managed last cycle is cached on disk (.link_cache.json) so a
         link removed from the config is undone once, across the restart
  5. pins the default sink / source and the hubs' master volume / mute (wpctl)
  6. runs cell events (Active / Created / Inactive / Deleted -> block / unblock
     other cells), writing the resulting blocks back into state.json

Blocking something WirePlumber keeps recreating means pruning it each cycle, so
expect a brief flicker (same as bypass always did).

Noise suppression: each consumer (the Virtual Mic, or a Custom Conduit with As
Microphone) that picks a method gets its own `pipewire -c <generated config>`
subprocess contributing two nodes (conduit_ns_<key>_<method>_in / _out).  The
Virtual Mic feeds the processor's input; the processor's output becomes the
default source.  For a Custom Conduit the processor sits between the conduit's
sink and its mic tap.  Subprocesses die with the service (KillMode=control-group).
"""
from __future__ import annotations

import atexit
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import conduit_core as core  # noqa: E402

POLL_INTERVAL = 2
ACTIVE_DEBOUNCE_CYCLES = 2      # "becomes Active / Inactive" must hold this many polls in a row
VOLUME_BOOST_LIMIT = 10.0
MASTER_VOLUME_BOOST_LIMIT = 1000.0

_RNNOISE_LADSPA_PATH = os.environ.get("CONDUIT_RNNOISE_LADSPA_PATH")
_NS_PROCESSES: dict = {}
_NS_CONFIG_DIR = Path(tempfile.gettempdir()) / "conduit-noise-suppression"


def log(msg: str) -> None:
    print(f"conduit-daemon: {msg}", flush=True)


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
    r = subprocess.run(["pw-cli", "create-node", "adapter", args], capture_output=True, text=True)
    if r.returncode != 0:
        log(f"failed to create {node_name!r}: {r.stderr.strip()}")
    else:
        log(f"created dynamic node {node_name!r} ({media_class})")


def destroy_dynamic_node(node_id, node_name):
    r = subprocess.run(["pw-cli", "destroy", str(node_id)], capture_output=True, text=True)
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


def sync_gain_nodes(specs, snap):
    for name, (volume, stereo) in specs.items():
        node = snap.by_name.get(name)
        if node is None:
            create_dynamic_node(name, "Conduit Gain", "Audio/Sink", ("FL", "FR") if stereo else ("MONO",))
        else:
            subprocess.run(["wpctl", "set-volume", str(node.id), f"{volume}", "-l", str(VOLUME_BOOST_LIMIT)],
                           capture_output=True)
    for node in list(snap.nodes.values()):
        if node.name.startswith(core.GAIN_NODE_PREFIX) and node.name not in specs:
            destroy_dynamic_node(node.id, node.name)


# ---------------------------------------------------------------------------
# noise suppression
# ---------------------------------------------------------------------------

_NS_PREREQ_MODULES = """\
    { name = libpipewire-module-rt args = { } flags = [ ifexists nofail ] }
    { name = libpipewire-module-protocol-native }
    { name = libpipewire-module-client-node }
    { name = libpipewire-module-adapter }
"""


def _rnnoise_config(in_name, out_name, description):
    description = sanitize_description(description)
    return f"""
context.modules = [
{_NS_PREREQ_MODULES}    {{ name = libpipewire-module-filter-chain
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
{_NS_PREREQ_MODULES}    {{ name = libpipewire-module-echo-cancel
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


def _start_ns(key, method, description):
    if (key, method) in _NS_PROCESSES:
        return
    in_name, out_name = core.noise_processor_names(key, method)
    if method == "rnnoise":
        if not _RNNOISE_LADSPA_PATH:
            log(f"can't start RNNoise for {key!r} -- CONDUIT_RNNOISE_LADSPA_PATH isn't set "
                f"(is services.conduit.noiseSuppression.enable on, and rebuilt?)")
            return
        text = _rnnoise_config(in_name, out_name, description)
    elif method == "webrtc":
        text = _webrtc_config(in_name, out_name, description)
    else:
        return
    _NS_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path = _NS_CONFIG_DIR / f"{in_name}.conf"
    path.write_text(text)
    try:
        popen = subprocess.Popen(["pipewire", "-c", str(path)])
    except FileNotFoundError:
        log("pipewire binary not found on PATH, can't start noise suppression")
        return
    _NS_PROCESSES[(key, method)] = {"popen": popen, "config_path": path}
    log(f"started {method} noise suppression for {key!r} (pid {popen.pid}, nodes {in_name!r}/{out_name!r})")


def _stop_ns(key, method):
    entry = _NS_PROCESSES.pop((key, method), None)
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
        entry["config_path"].unlink(missing_ok=True)
    except OSError:
        pass
    log(f"stopped {method} noise suppression for {key!r}")


def _cleanup_ns():
    for key, method in list(_NS_PROCESSES):
        _stop_ns(key, method)


atexit.register(_cleanup_ns)


def sync_noise_processors(state):
    desired = set()
    m = core.ns_method(state, "mic")
    if m in core.NOISE_SUPPRESSION_METHODS:
        desired.add(("mic", m))
    for c in state["custom"]["conduits"]:
        key = f"custom.{c['id']}"
        m = core.ns_method(state, key)
        if m in core.NOISE_SUPPRESSION_METHODS:
            desired.add((key, m))
    for key, method in list(_NS_PROCESSES):
        if (key, method) not in desired:
            _stop_ns(key, method)
        elif _NS_PROCESSES[(key, method)]["popen"].poll() is not None:
            log(f"noise suppression for {key!r} exited (code {_NS_PROCESSES[(key, method)]['popen'].returncode}); restarting")
            _NS_PROCESSES.pop((key, method), None)
    for key, method in desired:
        if (key, method) not in _NS_PROCESSES:
            desc = "Conduit Virtual Mic" if key == "mic" else core.box_label(state, key)
            _start_ns(key, method, desc)


# ---------------------------------------------------------------------------
# links
# ---------------------------------------------------------------------------

def pw_link(o, i):
    r = subprocess.run(["pw-link", str(o), str(i)], capture_output=True, text=True)
    err = r.stderr.strip().lower()
    if r.returncode != 0 and "already linked" not in err and "file exists" not in err:
        log(f"pw-link {o} {i} failed: {r.stderr.strip()}")
    else:
        log(f"linked {o} -> {i}")


def pw_unlink(o, i):
    subprocess.run(["pw-link", "-d", str(o), str(i)], capture_output=True, text=True)
    log(f"unlinked {o} -> {i}")


def _load_link_cache():
    try:
        data = json.loads(core.LINK_CACHE_FILE.read_text())
        return {tuple(p) for p in data if isinstance(p, list) and len(p) == 2}
    except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError, OSError):
        return set()


def _save_link_cache(pairs):
    try:
        core.LINK_CACHE_FILE.write_text(json.dumps([list(p) for p in pairs]))
    except OSError:
        pass


def reconcile(plan, snap):
    prunable = plan.managed_pairs | _load_link_cache()
    for o, i in snap.links:
        no, ni = snap.port_owner.get(o), snap.port_owner.get(i)
        if (o, i) in plan.forbidden or no in plan.blocked_nodes or ni in plan.blocked_nodes:
            pw_unlink(o, i)
        elif (no, ni) in prunable and (o, i) not in plan.desired:
            pw_unlink(o, i)
    for o, i in plan.desired:
        if (o, i) not in snap.links:
            pw_link(o, i)
    _save_link_cache(plan.managed_pairs)


def enforce_defaults(snap, mic_default):
    vs = snap.by_name.get(core.VIRTUAL_SPEAKER)
    if vs:
        subprocess.run(["wpctl", "set-default", str(vs.id)], capture_output=True)
    target = mic_default or snap.by_name.get(core.VIRTUAL_MIC)
    if target:
        subprocess.run(["wpctl", "set-default", str(target.id)], capture_output=True)


def _master(node, volume, muted):
    if node is None:
        return
    if volume != 1.0:
        subprocess.run(["wpctl", "set-volume", str(node.id), f"{volume}", "-l", str(MASTER_VOLUME_BOOST_LIMIT)],
                       capture_output=True)
    subprocess.run(["wpctl", "set-mute", str(node.id), "1" if muted else "0"], capture_output=True)


def enforce_master_controls(state, snap):
    _master(snap.by_name.get(core.VIRTUAL_SPEAKER), state["speaker"]["volume"], state["speaker"]["muted"])
    _master(snap.by_name.get(core.VIRTUAL_MIC), state["mic"]["volume"], state["mic"]["muted"])
    for c in state["custom"]["conduits"]:
        _master(snap.by_name.get(core.custom_node_name(c["id"])), c["volume"], c["muted"])


# ---------------------------------------------------------------------------
# events
# ---------------------------------------------------------------------------

class EventEngine:
    """Watches every cell that has events.  The first observation of a cell is only a
    baseline (events never fire for what already existed when the daemon started)."""

    def __init__(self):
        self.seen = {}      # cell id -> {"exists", "active", "pending", "count"}

    def step(self, state, snap):
        fired = []
        for cell in state["cells"]:
            if not cell.get("events"):
                continue
            nodes = core.cell_all_nodes(state, cell["id"], snap)
            exists = bool(nodes)
            active = any(n.running for n in nodes)
            prev = self.seen.get(cell["id"])
            if prev is None:
                self.seen[cell["id"]] = {"exists": exists, "active": active, "pending": active, "count": 0}
                continue
            if exists != prev["exists"]:
                fired.append((cell, "created" if exists else "deleted"))
                prev["exists"] = exists
            if active == prev["pending"]:
                prev["count"] += 1
            else:
                prev["pending"], prev["count"] = active, 1
            if prev["pending"] != prev["active"] and prev["count"] >= ACTIVE_DEBOUNCE_CYCLES:
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
            return
        changed = False
        for c in state["cells"]:
            if c["id"] in changes and c.get("blocked", False) != changes[c["id"]]:
                c["blocked"] = changes[c["id"]]
                changed = True
        if not changed:
            return
        raw = core.read_raw_state()
        if not isinstance(raw, dict) or core.is_v1(raw):
            return
        for c in raw.get("cells", []):
            if isinstance(c, dict) and c.get("id") in changes:
                c["blocked"] = changes[c["id"]]
        try:
            core.atomic_write_json(core.STATE_FILE, raw)
        except OSError as e:
            log(f"couldn't persist event blocks: {e}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def print_inventory(snap):
    log(f"node inventory ({len(snap.nodes)} nodes):")
    for n in sorted(snap.nodes.values(), key=lambda n: n.display_label.lower()):
        log(f"  {n.display_label!r} (name={n.name!r} class={n.media_class!r} "
            f"in_ports={len(n.input_ports)} out_ports={len(n.output_ports)} "
            f"client_id={n.client_id} state={n.state})")


def run_cycle(state, snap, events):
    sync_custom_conduits(state, snap)
    sync_gain_nodes(core.gain_specs(state), snap)
    sync_noise_processors(state)
    plan = core.compute_plan(state, snap)
    reconcile(plan, snap)
    enforce_defaults(snap, plan.mic_default)
    enforce_master_controls(state, snap)
    fired = events.step(state, snap)
    if fired:
        EventEngine.apply(state, fired)


def main():
    core.ensure_config_exists()
    log(f"starting, watching {core.STATE_FILE}")
    printed = False
    events = EventEngine()
    while True:
        try:
            state = core.load_state()
            snap = core.Snapshot.capture(log)
            if snap.ok:
                if not printed:
                    print_inventory(snap)
                    printed = True
                run_cycle(state, snap, events)
        except Exception:
            traceback.print_exc()
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
