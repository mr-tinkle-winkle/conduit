#!/usr/bin/env python3
"""
conduit_daemon.py — PipeWire virtual mic/speaker router for Conduit.

WHAT THIS DOES
---------------
Two static virtual devices are created declaratively by the NixOS module
(see module.nix), NOT by this script:

  conduit_virtual_speaker   Audio/Sink            -- apps play into this
  conduit_virtual_mic       Audio/Source/Virtual  -- a single node that's
                                                       simultaneously
                                                       "linkable into" (like
                                                       a sink) and selectable
                                                       as a recording device
                                                       (like a source) -- the
                                                       standard PipeWire
                                                       recipe for a virtual
                                                       mic (see module.nix)

This daemon's job is purely graph plumbing on top of those two fixed
nodes, driven entirely by ~/.config/conduit/state.json:

  1. Make sure the virtual speaker/mic are the system default sink/source.
  2. Link real microphones (state["mic"]["inputs"]) into the virtual mic.
  3. Link the virtual mic out to any extra apps (state["mic"]["outputs"]).
  4. Link apps (state["speaker"]["inputs"]) into the virtual speaker.
  5. Link the virtual speaker's monitor out to real speakers
     (state["speaker"]["outputs"]).
  6. For anything in state["speaker"]["bypass"]: forcibly disconnect it
     from the virtual speaker and connect it directly to
     state["speaker"]["bypass_target"] instead.

CONFIG-DRIVEN, LIKE PUPPETRY
-----------------------------
Same philosophy as macro_daemon.py: state.json is the single source of
truth, edited by the GUI (conduit_gui.py). Editing it restarts this
service (`systemctl --user restart conduit-daemon`) so a fresh read
happens rather than trying to hot-diff config changes mid-loop.

What DOES need to happen continuously, independent of config edits, is
re-linking when the *audio graph* changes shape -- Bluetooth headphones
reconnecting, an app being relaunched, a USB mic replugged. So this
script polls `pw-dump` every POLL_INTERVAL seconds for as long as it
runs and reconciles the link set every cycle. Reconciliation is
idempotent -- pw-link errors on an already-existing link are swallowed,
missing links get created, and stale managed links get torn down.

CONFIG LAYOUT
-------------
~/.config/conduit/state.json

  {
    "mic": {
      "inputs":  ["Blue Yeti Analog Stereo"],   // real mic node.description values
      "outputs": []                              // extra apps to also feed the virtual mic into
    },
    "speaker": {
      "inputs":  ["Spotify"],                    // apps routed into the virtual speaker
      "outputs": ["Family 17h (HDMI)"],           // real output device(s) the mix is sent to
      "bypass":  ["Spotify"],                     // apps forced around the virtual speaker
      "bypass_target": "Family 17h (HDMI)"        // single real device bypassed apps go to
    }
  }

Devices are matched by node.description (stable across reboots for the
same physical hardware); apps are matched by application.name. An app
listed in both "inputs" and "bypass" is treated as bypass-only --
bypass always wins, since the whole point is to skip the virtual mix
for that one app.
"""

import json
import subprocess
import sys
import time
import traceback
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "conduit"
STATE_FILE = CONFIG_DIR / "state.json"

POLL_INTERVAL = 2  # seconds between graph reconciliation passes

VIRTUAL_SPEAKER = "conduit_virtual_speaker"
VIRTUAL_MIC = "conduit_virtual_mic"
_OUR_VIRTUAL_NODE_NAMES = {VIRTUAL_SPEAKER, VIRTUAL_MIC}

DEFAULT_STATE = {
    "mic": {"inputs": [], "outputs": []},
    "speaker": {"inputs": [], "outputs": [], "bypass": [], "bypass_target": None},
}


# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

def ensure_config_exists():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not STATE_FILE.exists():
        STATE_FILE.write_text(json.dumps(DEFAULT_STATE, indent=2))


def load_state():
    try:
        data = json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return json.loads(json.dumps(DEFAULT_STATE))  # deep copy
    # Merge onto defaults so a partially-hand-edited file doesn't crash us.
    state = json.loads(json.dumps(DEFAULT_STATE))
    for section in ("mic", "speaker"):
        state[section].update(data.get(section, {}))
    return state


# ---------------------------------------------------------------------------
# PIPEWIRE GRAPH INTROSPECTION
# ---------------------------------------------------------------------------

def pw_dump():
    """Return the parsed `pw-dump` object list, or [] if pipewire isn't up yet."""
    try:
        out = subprocess.run(
            ["pw-dump"], capture_output=True, text=True, timeout=10, check=True
        ).stdout
        return json.loads(out)
    except FileNotFoundError:
        print("conduit-daemon: pw-dump not found on PATH", flush=True)
        return []
    except subprocess.CalledProcessError as e:
        print(f"conduit-daemon: pw-dump failed (exit {e.returncode}): {e.stderr.strip()}", flush=True)
        return []
    except (subprocess.SubprocessError, json.JSONDecodeError) as e:
        print(f"conduit-daemon: pw-dump error: {e}", flush=True)
        return []


class Node:
    __slots__ = ("id", "name", "description", "app_name", "media_class",
                 "output_ports", "input_ports")

    def __init__(self, id_, props):
        self.id = id_
        self.name = props.get("node.name", "")
        self.description = props.get("node.description", self.name)
        self.app_name = props.get("application.name")
        self.media_class = props.get("media.class", "")
        self.output_ports = []  # list of (port_id, channel)
        self.input_ports = []

    @property
    def is_ours(self):
        return self.name in _OUR_VIRTUAL_NODE_NAMES

    @property
    def display_label(self):
        """What the GUI/config match against: app name for streams, else description."""
        return self.app_name or self.description


def build_graph(dump):
    """Return {node_id: Node}, with ports attached, from a pw-dump object list."""
    nodes = {}
    for obj in dump:
        if obj.get("type") == "PipeWire:Interface:Node":
            props = obj.get("info", {}).get("props", {})
            nodes[obj["id"]] = Node(obj["id"], props)

    for obj in dump:
        if obj.get("type") == "PipeWire:Interface:Port":
            props = obj.get("info", {}).get("props", {})
            node_id = props.get("node.id")
            node = nodes.get(node_id)
            if node is None:
                continue
            port_id = obj["id"]
            channel = props.get("audio.channel", "MONO")
            direction = obj.get("info", {}).get("direction") or props.get("port.direction") or ""
            if direction.startswith("out"):
                node.output_ports.append((port_id, channel))
            elif direction.startswith("in"):
                node.input_ports.append((port_id, channel))

    return nodes


def current_links(dump):
    """Return {(output_port_id, input_port_id)} for every existing link."""
    links = set()
    for obj in dump:
        if obj.get("type") == "PipeWire:Interface:Link":
            props = obj.get("info", {}).get("props", {})
            out_port = props.get("link.output.port")
            in_port = props.get("link.input.port")
            if out_port is not None and in_port is not None:
                links.add((out_port, in_port))
    return links


# ---------------------------------------------------------------------------
# MATCHING
# ---------------------------------------------------------------------------

HW_SINK = "Audio/Sink"
HW_SOURCE = "Audio/Source"
APP_OUTPUT = "Stream/Output/Audio"
APP_INPUT = "Stream/Input/Audio"


def find_node(nodes, label, allowed_classes):
    for node in nodes.values():
        if node.is_ours:
            continue
        if node.media_class in allowed_classes and node.display_label == label:
            return node
    return None


def find_node_verbose(nodes, label, allowed_classes, context):
    """Same as find_node, but logs a hint when a label from state.json
    doesn't match anything currently in the graph -- the single most
    common cause of "it's configured but nothing happens"."""
    node = find_node(nodes, label, allowed_classes)
    if node is None:
        candidates = sorted(
            n.display_label for n in nodes.values()
            if n.media_class in allowed_classes and not n.is_ours
        )
        print(f"conduit-daemon: [{context}] no live node matches "
              f"{label!r} (looked in {sorted(allowed_classes)}); "
              f"currently eligible: {candidates}", flush=True)
    return node


def find_virtual(nodes, name):
    for node in nodes.values():
        if node.name == name:
            return node
    return None


def pair_ports(src_ports, dst_ports):
    """Match output ports to input ports by channel (FL<->FL, FR<->FR),
    falling back to positional order if channel names don't line up
    (e.g. one side is MONO and the other is stereo)."""
    src_by_chan = {c: p for p, c in src_ports}
    dst_by_chan = {c: p for p, c in dst_ports}
    common = set(src_by_chan) & set(dst_by_chan)
    if common:
        return [(src_by_chan[c], dst_by_chan[c]) for c in common]
    # Fallback: pair in order, up to the shorter list.
    return list(zip((p for p, _ in src_ports), (p for p, _ in dst_ports)))


# ---------------------------------------------------------------------------
# RECONCILIATION
# ---------------------------------------------------------------------------

def pw_link(src_port, dst_port):
    result = subprocess.run(["pw-link", str(src_port), str(dst_port)], capture_output=True, text=True)
    stderr = result.stderr.strip()
    if result.returncode != 0 and "already linked" not in stderr.lower() and "file exists" not in stderr.lower():
        print(f"conduit-daemon: pw-link {src_port} {dst_port} failed: {stderr}", flush=True)
    else:
        print(f"conduit-daemon: linked {src_port} -> {dst_port}", flush=True)


def pw_unlink(src_port, dst_port):
    result = subprocess.run(["pw-link", "-d", str(src_port), str(dst_port)], capture_output=True, text=True)
    print(f"conduit-daemon: unlinked {src_port} -> {dst_port}", flush=True)


def compute_desired_links(state, nodes):
    """Return {(src_port, dst_port)} for every link Conduit wants to exist,
    and the set of "managed scopes" -- (node_id, node_id) pairs whose
    existing links we're allowed to prune if they're no longer desired."""
    desired = set()
    managed_node_pairs = set()

    virtual_speaker = find_virtual(nodes, VIRTUAL_SPEAKER)
    virtual_mic = find_virtual(nodes, VIRTUAL_MIC)

    def route(src_node, dst_node):
        if src_node is None or dst_node is None:
            return
        for pair in pair_ports(src_node.output_ports, dst_node.input_ports):
            desired.add(pair)
        managed_node_pairs.add((src_node.id, dst_node.id))

    # --- Mic side ---
    # conduit_virtual_mic is one node that's both linkable-into (its
    # input ports, same as a sink would have) and selectable as a
    # recording device (its output ports feed whatever selects it as
    # a source) -- see module.nix for why one node covers both roles.
    if virtual_mic:
        for label in state["mic"]["inputs"]:
            real_mic = find_node_verbose(nodes, label, {HW_SOURCE}, "mic input")
            route(real_mic, virtual_mic)
        for label in state["mic"]["outputs"]:
            app = find_node_verbose(nodes, label, {APP_INPUT, HW_SINK}, "mic output")
            route(virtual_mic, app)

    # --- Speaker side ---
    bypass_set = set(state["speaker"]["bypass"])
    if virtual_speaker:
        for label in state["speaker"]["inputs"]:
            if label in bypass_set:
                continue  # bypass always wins over inputs
            app = find_node_verbose(nodes, label, {APP_OUTPUT, HW_SOURCE}, "speaker input")
            route(app, virtual_speaker)
        for label in state["speaker"]["outputs"]:
            real_speaker = find_node_verbose(nodes, label, {HW_SINK}, "speaker output")
            if real_speaker is None or virtual_speaker is None:
                continue
            for pair in pair_ports(virtual_speaker.output_ports, real_speaker.input_ports):
                # virtual_speaker itself has no output ports (it's a sink) --
                # what we actually want is its *monitor* ports, which pw-dump
                # reports as this same node's output ports since a sink's
                # monitor ports live on the sink node itself.
                desired.add(pair)
            managed_node_pairs.add((virtual_speaker.id, real_speaker.id))

    # --- Bypass: force-disconnect from virtual speaker, connect direct ---
    bypass_target_label = state["speaker"].get("bypass_target")
    bypass_target = (
        find_node_verbose(nodes, bypass_target_label, {HW_SINK}, "bypass target")
        if bypass_target_label else None
    )
    for label in bypass_set:
        app = find_node_verbose(nodes, label, {APP_OUTPUT}, "bypass app")
        if app is None:
            continue
        # Always mark (app -> virtual_speaker) as managed so any existing
        # link there gets pruned below, even with no bypass_target chosen yet.
        if virtual_speaker:
            managed_node_pairs.add((app.id, virtual_speaker.id))
        if bypass_target:
            route(app, bypass_target)

    return desired, managed_node_pairs


def reconcile(state, nodes, dump):
    desired, managed_pairs = compute_desired_links(state, nodes)
    existing = current_links(dump)

    # Build a lookup from port id -> owning node id, so we can tell whether
    # an existing link falls inside a pair of nodes we manage.
    port_owner = {}
    for node in nodes.values():
        for pid, _ in node.output_ports:
            port_owner[pid] = node.id
        for pid, _ in node.input_ports:
            port_owner[pid] = node.id

    for out_port, in_port in existing:
        pair = (port_owner.get(out_port), port_owner.get(in_port))
        if pair in managed_pairs and (out_port, in_port) not in desired:
            pw_unlink(out_port, in_port)

    for out_port, in_port in desired:
        if (out_port, in_port) not in existing:
            pw_link(out_port, in_port)


def enforce_defaults(nodes):
    virtual_speaker = find_virtual(nodes, VIRTUAL_SPEAKER)
    virtual_mic = find_virtual(nodes, VIRTUAL_MIC)
    if virtual_speaker:
        subprocess.run(["wpctl", "set-default", str(virtual_speaker.id)], capture_output=True)
    if virtual_mic:
        subprocess.run(["wpctl", "set-default", str(virtual_mic.id)], capture_output=True)


# ---------------------------------------------------------------------------
# MAIN LOOP
# ---------------------------------------------------------------------------

def main():
    ensure_config_exists()
    print("conduit-daemon: starting, watching", STATE_FILE, flush=True)
    printed_debug_sample = False
    while True:
        try:
            state = load_state()
            print(f"conduit-daemon: loaded state: {json.dumps(state)}", flush=True)
            dump = pw_dump()
            print(f"conduit-daemon: pw-dump returned {len(dump)} objects", flush=True)
            if dump:
                if not printed_debug_sample:
                    # One-time raw sample so we can confirm the actual JSON
                    # shape pw-dump produces on this PipeWire version,
                    # instead of assuming it matches the docs.
                    sample_node = next((o for o in dump if o.get("type") == "PipeWire:Interface:Node"), None)
                    sample_ports = [o for o in dump if o.get("type") == "PipeWire:Interface:Port"][:2]
                    print("conduit-daemon: SAMPLE NODE: " + json.dumps(sample_node), flush=True)
                    for p in sample_ports:
                        print("conduit-daemon: SAMPLE PORT: " + json.dumps(p), flush=True)
                    printed_debug_sample = True

                nodes = build_graph(dump)
                total_out = sum(len(n.output_ports) for n in nodes.values())
                total_in = sum(len(n.input_ports) for n in nodes.values())
                print(f"conduit-daemon: built graph: {len(nodes)} nodes, "
                      f"{total_out} output ports, {total_in} input ports", flush=True)
                enforce_defaults(nodes)
                reconcile(state, nodes, dump)
        except Exception:
            # Belt-and-suspenders: a silently-dying loop is much harder to
            # debug than a noisy one. Print the full traceback and keep
            # going rather than letting one bad cycle kill the service.
            traceback.print_exc()
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
