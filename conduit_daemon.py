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
      "inputs":  [{"label": "Blue Yeti Analog Stereo", "auto_detect": {...}}],
      "outputs": []
    },
    "speaker": {
      "inputs":  [{"label": "Spotify", "auto_detect": {...}}],
      "outputs": [{"label": "Family 17h (HDMI)", "auto_detect": {...}},
                  {"label": "vencord-screen-share", "auto_detect": {...}}],
      "bypass":  [{"label": "Spotify", "auto_detect": {...}}],
      "bypass_target": "Family 17h (HDMI)"
    }
  }

Devices are matched by node.description (stable across reboots for the
same physical hardware); apps are matched by application.name. An app
listed in both "inputs" and "bypass" is treated as bypass-only --
bypass always wins, since the whole point is to skip the virtual mix
for that one app.

AUTO-DETECT
-----------
Each entry in a list carries its own "auto_detect":

  {"prefix": bool, "keyword": bool, "keyword_text": str, "same_app": bool,
   "anti": bool, "anti_keyword_text": str}

This is deliberately per-device rather than per-list -- a list often
mixes real hardware (which never needs this) with an app-created
virtual device (which might), and a single list-wide setting would
apply prefix/keyword/same-app matching to entries that have nothing to
do with each other. Every reconcile cycle, each entry's *own* label is
always routed (if the entry is enabled -- see below), and if any of
its strategies are on, any other currently-live device matching that
strategy *relative to that one entry* gets swept in too -- without
ever being written back into the config. This is deliberately
ephemeral: state.json (and the GUI's visible list) always reflects
only what you explicitly added, and auto-detected siblings come and go
from routing as PipeWire's graph changes, which is exactly what you
want for something like Discord spinning up a differently-numbered
capture stream per screen share.

  - prefix:   strip a trailing counter/suffix ("-2", " (3)", "#4", ...)
              from both this entry's label and a candidate's, and match
              if what's left is identical. Catches "Chromium input-2"
              off of a saved "Chromium input".
  - keyword:  substring match (case-insensitive) against keyword_text,
              independent of this entry's own label.
  - same_app: match by the underlying PipeWire client (client.id) --
              catches every stream a single running app/process
              creates, however differently each one is named. Only
              works while a node whose label exactly matches this
              entry is currently live, since that's what supplies the
              reference client.id to expand from. Matching is by
              client identity, not by which list entry supplied the
              seed -- if that client also owns some other stream you
              didn't expect, same_app will sweep it in too, since as
              far as PipeWire is concerned it genuinely is the same
              process.
  - anti:     the inverse -- a comma-separated list of substrings
              (case-insensitive) that, if present in a candidate's
              label, exclude it from this entry's prefix/keyword/
              same_app matches. Never removes the entry's own literal
              label, only auto-detected siblings, since the point is
              to keep an unwanted sibling out of the sweep, not to
              undo something you added by hand.

ENABLED / VOLUME
-----------------
Each entry also carries:

  {"enabled": bool, "volume": float}

"enabled": false takes an entry out of routing entirely (as if it
weren't in the list at all, auto-detect included) without deleting
it, so it's a one-click toggle to bring back later.

"volume" is a per-device multiplier (1.0 = unchanged, 2.0 = double,
0.5 = half), continuously re-applied via `wpctl set-volume` every
reconcile cycle -- it always sits at exactly that level, overriding
manual adjustments made elsewhere. A value of exactly 1.0 is treated
as "not managing this device's volume" and is left alone entirely,
CUSTOM CONDUITS
---------------
A "Custom Conduit" is a user-created virtual patch point, distinct
from the two fixed devices above (which come from your NixOS config
and never change without a rebuild). Since these are created and
renamed live from the GUI with no rebuild step, the daemon itself
creates and destroys the underlying PipeWire nodes at runtime, via
`pw-cli create-node` / `pw-cli destroy` -- the same
adapter+support.null-audio-sink building block module.nix uses
statically, just invoked live instead of declared once at boot.

  "custom": {
    "next_id": 2,
    "conduits": [
      {
        "id": 1,
        "name": "Custom Conduit 1",
        "as_speaker": true,
        "as_microphone": false,
        "inputs": [...same shape as any other list...],
        "outputs": [...same shape as any other list...]
      }
    ]
  }

"id" is permanent and never reused, even after the conduit is deleted
(that's what "next_id" tracks) -- it's what derives the underlying
node's stable technical name (conduit_custom_<id>), decoupled from the
user-facing "name", which is purely a Conduit-side label. Renaming a
conduit only updates that label; it doesn't touch the live PipeWire
node (so a third-party tool like qpwgraph will keep showing whatever
description it had at creation time -- a deliberate, documented
trade-off in exchange for renaming being instant and never needing to
tear down an existing conduit's connections just to relabel it).

Every conduit always gets one real underlying node --
"conduit_custom_<id>", media.class Audio/Sink -- which is where its
own Input list feeds in and its own Output list is read from, exactly
like the main Virtual Speaker. That's what "as_speaker" describes: it
comes structurally free just from being a sink-style patch point (the
checkbox doesn't need to add anything -- there's currently no reliable
cross-desktop way to make a Sink-classed node NOT show up as a
selectable output device, so it may appear in speaker pickers whether
or not the box is ticked; this is an honest limitation, not a bug).

"as_microphone" is where the two checkboxes actually diverge: ticking
it creates a SECOND, lightweight node -- "conduit_custom_<id>_mic",
media.class Audio/Source/Virtual -- and links the primary node's
monitor ports into it. That second node doesn't do any mixing of its
own; it's a passive tap on whatever the primary node is already
carrying, so apps can select it as a "microphone" and hear the exact
same signal the conduit's Output list is also sending elsewhere (the
classic "Stereo Mix" pattern). Unticking it destroys that second node.

Every reconcile cycle, any live node named "conduit_custom_*" that no
longer corresponds to a configured conduit (or a still-enabled
as_microphone flag) gets destroyed -- this is what cleans up after a
whole conduit is deleted, or after as_microphone is turned back off.

NOISE SUPPRESSION
-----------------
Only the Virtual Mic and a Custom Conduit with "As Microphone" ticked
can request this (state["mic"]["noise_suppression"] and each custom
conduit's "mic_noise_suppression", both "none" | "rnnoise" | "webrtc").
Unlike everything else that's created dynamically, the actual DSP
processors are a FIXED POOL of statically nix-declared nodes (see
module.nix's noiseSuppression option) -- filter-chain plugin graphs are
too complex to trust loading dynamically without a live PipeWire
instance to verify against, unlike the simple adapter nodes Custom
Conduits and gain nodes use. The daemon's job is just to allocate a
free pool slot to whoever's asking and route through it -- plain
pw-link rewiring, the same mechanism proven everywhere else in this
file.

For the Virtual Mic specifically, the processor's OUTPUT node becomes
the system default audio source (instead of Virtual Mic itself) and
the target for any mic.outputs entries -- so it doesn't matter whether
an app reaches the mic by picking "the default" or by an explicit
route Conduit set up, either way it gets denoised audio. For a Custom
Conduit, the processor sits between the base node and the as_microphone
tap node instead of the tap linking directly to the base.

Slot assignment is a simple sorted-list index over current requesters,
recomputed fresh each cycle -- simple, but means an unrelated
requester's slot CAN shift (a brief relink) when the SET of active
requesters changes, not just when its own request changes. Given this
is realistically used by a handful of things at once, that's an
accepted tradeoff rather than something worth persisting slot
assignments to avoid.
"""

import hashlib
import json
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "conduit"
STATE_FILE = CONFIG_DIR / "state.json"
LINK_CACHE_FILE = CONFIG_DIR / ".link_cache.json"

POLL_INTERVAL = 2  # seconds between graph reconciliation passes
VOLUME_BOOST_LIMIT = 10.0  # ceiling passed to `wpctl set-volume -l`, allows >100%

VIRTUAL_SPEAKER = "conduit_virtual_speaker"
VIRTUAL_MIC = "conduit_virtual_mic"
_OUR_VIRTUAL_NODE_NAMES = {VIRTUAL_SPEAKER, VIRTUAL_MIC}
_CUSTOM_NODE_PREFIX = "conduit_custom_"

DEFAULT_AUTO_DETECT = {
    "prefix": False, "keyword": False, "keyword_text": "", "same_app": False,
    "anti": False, "anti_keyword_text": "",
}

DEFAULT_STATE = {
    "mic": {"inputs": [], "outputs": [], "noise_suppression": "none"},
    "speaker": {"inputs": [], "outputs": [], "bypass": [], "bypass_target": None},
    "custom": {"next_id": 1, "conduits": []},
}

# Must match module.nix's services.conduit.noiseSuppression.poolSize
# (default there is also 4) -- these are two sides of the same
# statically-declared pool, so they have to agree on its size.
NOISE_SUPPRESSION_METHODS = ("rnnoise", "webrtc")
NOISE_SUPPRESSION_POOL_SIZE = 4


def noise_processor_names(method, slot):
    return (f"conduit_ns_{method}_{slot}_in", f"conduit_ns_{method}_{slot}_out")


def custom_node_name(conduit_id):
    return f"{_CUSTOM_NODE_PREFIX}{conduit_id}"


def custom_mic_node_name(conduit_id):
    return f"{_CUSTOM_NODE_PREFIX}{conduit_id}_mic"


# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

def ensure_config_exists():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not STATE_FILE.exists():
        STATE_FILE.write_text(json.dumps(DEFAULT_STATE, indent=2))


def _migrate_items(loaded):
    """Normalize a config list to
    [{"label", "enabled", "volume", "mono", "auto_detect"}, ...], transparently
    handling every prior on-disk shape rather than discarding an
    already-working config:
      - flat list of strings (original format, pre auto-detect)
      - {"items": [...strings...], "auto_detect": {...}} (short-lived
        list-wide auto-detect format)
      - list of {"label", "auto_detect"} dicts (pre enabled/volume)
      - list of {"label", "enabled", "volume", "auto_detect"} dicts (pre mono)
      - already-current list of {"label", "enabled", "volume", "mono", "auto_detect"} dicts
    """
    def fresh_entry(label, auto_detect_seed=None, enabled=True, volume=1.0, mono=False):
        ad = dict(DEFAULT_AUTO_DETECT)
        if isinstance(auto_detect_seed, dict):
            ad.update({k: v for k, v in auto_detect_seed.items() if k in DEFAULT_AUTO_DETECT})
        return {
            "label": label,
            "enabled": bool(enabled) if isinstance(enabled, bool) else True,
            "volume": float(volume) if isinstance(volume, (int, float)) else 1.0,
            "mono": bool(mono) if isinstance(mono, bool) else False,
            "auto_detect": ad,
        }

    if isinstance(loaded, list):
        result = []
        for entry in loaded:
            if isinstance(entry, str):
                result.append(fresh_entry(entry))
            elif isinstance(entry, dict) and isinstance(entry.get("label"), str):
                result.append(fresh_entry(
                    entry["label"], entry.get("auto_detect"),
                    entry.get("enabled", True), entry.get("volume", 1.0), entry.get("mono", False),
                ))
        return result

    if isinstance(loaded, dict) and isinstance(loaded.get("items"), list):
        # The brief list-wide auto_detect format -- apply it to each item
        # as a starting point rather than dropping the setting entirely.
        shared_ad = loaded.get("auto_detect") if isinstance(loaded.get("auto_detect"), dict) else None
        return [fresh_entry(e, shared_ad) for e in loaded["items"] if isinstance(e, str)]

    return []


def load_state():
    try:
        data = json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    state = json.loads(json.dumps(DEFAULT_STATE))
    mic = data.get("mic", {}) if isinstance(data.get("mic"), dict) else {}
    state["mic"]["inputs"] = _migrate_items(mic.get("inputs"))
    state["mic"]["outputs"] = _migrate_items(mic.get("outputs"))
    mic_ns = mic.get("noise_suppression")
    state["mic"]["noise_suppression"] = mic_ns if mic_ns in ("none",) + NOISE_SUPPRESSION_METHODS else "none"
    speaker = data.get("speaker", {}) if isinstance(data.get("speaker"), dict) else {}
    state["speaker"]["inputs"] = _migrate_items(speaker.get("inputs"))
    state["speaker"]["outputs"] = _migrate_items(speaker.get("outputs"))
    state["speaker"]["bypass"] = _migrate_items(speaker.get("bypass"))
    if speaker.get("bypass_target"):
        state["speaker"]["bypass_target"] = speaker["bypass_target"]

    custom = data.get("custom", {}) if isinstance(data.get("custom"), dict) else {}
    raw_conduits = custom.get("conduits", []) if isinstance(custom.get("conduits"), list) else []
    conduits = []
    max_seen_id = 0
    for entry in raw_conduits:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), int):
            continue
        max_seen_id = max(max_seen_id, entry["id"])
        conduit_ns = entry.get("mic_noise_suppression")
        conduits.append({
            "id": entry["id"],
            "name": entry.get("name") or f"Custom Conduit {entry['id']}",
            "as_speaker": bool(entry.get("as_speaker", True)),
            "as_microphone": bool(entry.get("as_microphone", False)),
            "mic_noise_suppression": conduit_ns if conduit_ns in ("none",) + NOISE_SUPPRESSION_METHODS else "none",
            "inputs": _migrate_items(entry.get("inputs")),
            "outputs": _migrate_items(entry.get("outputs")),
        })
    next_id = custom.get("next_id")
    if not isinstance(next_id, int) or next_id <= max_seen_id:
        next_id = max_seen_id + 1
    state["custom"] = {"next_id": next_id, "conduits": conduits}

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
                 "client_id", "output_ports", "input_ports")

    def __init__(self, id_, props):
        self.id = id_
        self.name = props.get("node.name", "")
        self.description = props.get("node.description", self.name)
        self.app_name = props.get("application.name")
        self.media_class = props.get("media.class", "")
        self.client_id = props.get("client.id")
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
#
# Eligibility is based on which ports a node actually has, not on its
# media.class string. This matches the original spec literally ("for
# inputs, any outputs are eligible; for outputs, any inputs are
# eligible") and, importantly, is robust to apps that create their own
# virtual devices with nonstandard media.class values -- e.g. Vesktop
# creates a node for its screen-share audio capture that behaves like
# a sink (it has input ports to receive the mix) but isn't necessarily
# tagged "Audio/Sink". Filtering by "does it have the right kind of
# port" catches those automatically; filtering by class string doesn't.
#
# HW_SINK is kept separately for the one place a class check is still
# useful: the "Speakers" bypass-target picker, which should only ever
# offer real physical output hardware, not a random app.

HW_SINK = "Audio/Sink"


def is_producer(node):
    """Eligible for anything wanting an audio *source* to pull from --
    real mics, apps that are playing sound, virtual devices with
    output ports, etc."""
    return not node.is_ours and len(node.output_ports) > 0


def is_consumer(node):
    """Eligible for anything wanting an audio *destination* to feed --
    real speakers, apps that are listening/recording, virtual devices
    with input ports, etc."""
    return not node.is_ours and len(node.input_ports) > 0


def is_hardware_sink(node):
    return not node.is_ours and node.media_class == HW_SINK


def find_node(nodes, label, predicate):
    for node in nodes.values():
        if predicate(node) and node.display_label == label:
            return node
    return None


def find_node_verbose(nodes, label, predicate, context):
    """Same as find_node, but logs a hint when a label from state.json
    doesn't match anything currently in the graph -- the single most
    common cause of "it's configured but nothing happens"."""
    node = find_node(nodes, label, predicate)
    if node is None:
        candidates = sorted(n.display_label for n in nodes.values() if predicate(n))
        print(f"conduit-daemon: [{context}] no live node matches "
              f"{label!r}; currently eligible: {candidates}", flush=True)
    return node


def find_virtual(nodes, name):
    for node in nodes.values():
        if node.name == name:
            return node
    return None


# ---------------------------------------------------------------------------
# AUTO-DETECT
# ---------------------------------------------------------------------------

_TRAILING_COUNTER_RE = re.compile(r'[\s\-_#(]*\d+\)?\s*$')


def _strip_counter_suffix(label):
    """'Chromium input-2' -> 'Chromium input'; 'Chromium input' unchanged."""
    return _TRAILING_COUNTER_RE.sub('', label).strip()


def expand_item_auto_detect(label, auto_detect, nodes, predicate):
    """Return the effective set of labels covered by ONE saved entry: its
    own label, plus any currently-live node matching one of that entry's
    enabled auto-detect strategies relative to it specifically. Scoped to
    a single entry rather than a whole list, since a list often mixes
    real hardware (which never needs this) with an app-created virtual
    device (which might) -- a list-wide setting would wrongly apply
    prefix/keyword/same-app matching to entries that have nothing to do
    with each other."""
    result = {label}
    if not auto_detect.get("prefix") and not auto_detect.get("keyword") and not auto_detect.get("same_app"):
        return result

    candidates = [n for n in nodes.values() if predicate(n)]

    if auto_detect.get("prefix"):
        seed_prefix = _strip_counter_suffix(label)
        for node in candidates:
            if _strip_counter_suffix(node.display_label) == seed_prefix:
                result.add(node.display_label)

    keyword = (auto_detect.get("keyword_text") or "").strip().lower()
    if auto_detect.get("keyword") and keyword:
        for node in candidates:
            if keyword in node.display_label.lower():
                result.add(node.display_label)

    if auto_detect.get("same_app"):
        seed_client_ids = {
            node.client_id for node in candidates
            if node.display_label == label and node.client_id is not None
        }
        if seed_client_ids:
            for node in candidates:
                if node.client_id in seed_client_ids:
                    result.add(node.display_label)

    # Anti-Auto-Detect: drop any AUTO-DETECTED sibling matching an
    # excluded keyword. Never removes the entry's own label -- that was
    # added by hand, not swept in, so it's out of scope for exclusion.
    anti_terms = [t.strip().lower() for t in (auto_detect.get("anti_keyword_text") or "").split(",") if t.strip()]
    if auto_detect.get("anti") and anti_terms:
        result = {
            item for item in result
            if item == label or not any(term in item.lower() for term in anti_terms)
        }

    return result


def expand_items(entries, nodes, predicate):
    """Union expand_item_auto_detect() over every enabled entry in a list
    -- the full effective label set to route for that whole section.
    Disabled entries are skipped entirely, auto-detect sweep included,
    as if they weren't in the list at all."""
    result = set()
    for entry in entries:
        if not entry.get("enabled", True):
            continue
        result |= expand_item_auto_detect(entry["label"], entry.get("auto_detect", DEFAULT_AUTO_DETECT), nodes, predicate)
    return result


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


def pair_ports_for_entry(src_ports, dst_ports, mono):
    """Like pair_ports(), but when `mono` is set, links EVERY source port
    to EVERY destination port instead of matching by channel. This is
    what makes a single-channel mic properly reach both FL and FR of a
    stereo destination (plain pair_ports() would only fill one side and
    leave the other silent), and symmetrically sums a stereo source down
    into a single-channel destination."""
    if mono:
        return [(sp, dp) for sp, _ in src_ports for dp, _ in dst_ports]
    return pair_ports(src_ports, dst_ports)


_GAIN_NODE_PREFIX = "conduit_gain_"


def gain_node_name(scope_key):
    """Deterministic technical node name for a per-entry gain node, so the
    same (list, label) pair maps to the same node across cycles/restarts
    instead of leaking a new one every time."""
    digest = hashlib.md5(scope_key.encode("utf-8")).hexdigest()[:12]
    return f"{_GAIN_NODE_PREFIX}{digest}"


def enumerate_gain_specs(state):
    """Return {gain_node_name: volume} for every enabled entry, across
    every list including custom conduits, whose volume != 1.0. Computed
    directly from config (no live PipeWire data needed) so it can run
    before compute_desired_links needs to know whether these nodes
    exist yet -- same ordering as sync_custom_conduits."""
    specs = {}

    def add(scope, entries):
        for entry in entries:
            if not entry.get("enabled", True):
                continue
            volume = entry.get("volume", 1.0)
            if volume == 1.0:
                continue
            specs[gain_node_name(f"{scope}:{entry['label']}")] = volume

    add("mic.inputs", state["mic"]["inputs"])
    add("mic.outputs", state["mic"]["outputs"])
    add("speaker.inputs", state["speaker"]["inputs"])
    add("speaker.outputs", state["speaker"]["outputs"])
    add("speaker.bypass", state["speaker"]["bypass"])
    for conduit in state.get("custom", {}).get("conduits", []):
        add(f"custom.{conduit['id']}.inputs", conduit.get("inputs", []))
        add(f"custom.{conduit['id']}.outputs", conduit.get("outputs", []))
    return specs


def sync_gain_nodes(gain_specs, nodes):
    """Ensure a dedicated adapter node exists for every entry that needs
    independent volume, continuously re-apply its volume (same "pin it"
    behavior as everything else volume-related), and destroy any gain
    node that's no longer needed -- volume back to 1.0, entry disabled,
    or removed entirely."""
    for name, volume in gain_specs.items():
        node = find_virtual(nodes, name)
        if node is None:
            _create_dynamic_node(name, "Conduit Gain", "Audio/Sink")
        else:
            subprocess.run(
                ["wpctl", "set-volume", str(node.id), f"{volume}", "-l", str(VOLUME_BOOST_LIMIT)],
                capture_output=True,
            )
    for node in list(nodes.values()):
        if node.name.startswith(_GAIN_NODE_PREFIX) and node.name not in gain_specs:
            _destroy_dynamic_node(node.id, node.name)


def collect_noise_requests(state):
    """Return {consumer_key: method} for the Virtual Mic ("mic") and any
    as_microphone Custom Conduit ("custom.<id>") currently requesting
    noise suppression."""
    requests = {}
    mic_method = state["mic"].get("noise_suppression", "none")
    if mic_method in NOISE_SUPPRESSION_METHODS:
        requests["mic"] = mic_method
    for conduit in state.get("custom", {}).get("conduits", []):
        if not conduit.get("as_microphone"):
            continue
        method = conduit.get("mic_noise_suppression", "none")
        if method in NOISE_SUPPRESSION_METHODS:
            requests[f"custom.{conduit['id']}"] = method
    return requests


def allocate_noise_processors(requests):
    """Assign each requester a pool slot for its method, sorted by
    consumer key for determinism. Returns {consumer_key: (in_name, out_name)}."""
    alloc = {}
    for method in NOISE_SUPPRESSION_METHODS:
        consumers = sorted(key for key, m in requests.items() if m == method)
        for i, key in enumerate(consumers):
            if i >= NOISE_SUPPRESSION_POOL_SIZE:
                print(f"conduit-daemon: no free {method} noise-suppression slot for {key!r} "
                      f"(pool size {NOISE_SUPPRESSION_POOL_SIZE} exceeded)", flush=True)
                continue
            alloc[key] = noise_processor_names(method, i + 1)
    return alloc


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
    the set of "managed scopes" -- (node_id, node_id) pairs whose existing
    links we're allowed to prune if they're no longer desired -- and the
    node that should act as the mic's effective output (Virtual Mic
    itself, or its noise-suppression processor's output if one is
    active) for enforce_defaults() to point the system default source at."""
    desired = set()
    managed_node_pairs = set()
    gain_specs = enumerate_gain_specs(state)
    noise_alloc = allocate_noise_processors(collect_noise_requests(state))

    virtual_speaker = find_virtual(nodes, VIRTUAL_SPEAKER)
    virtual_mic = find_virtual(nodes, VIRTUAL_MIC)

    def route(src_node, dst_node, gain_key=None, mono=False):
        """src_node -> dst_node, or src_node -> gain_node -> dst_node if
        gain_key names an entry whose volume != 1.0 (see enumerate_gain_specs).
        `mono` only affects the segment touching src_node/dst_node directly
        -- see pair_ports_for_entry."""
        if src_node is None or dst_node is None:
            return
        gname = gain_node_name(gain_key) if gain_key else None
        if gname and gname in gain_specs:
            gain_node = find_virtual(nodes, gname)
            if gain_node is None:
                return  # sync_gain_nodes will create it; links follow next cycle
            for pair in pair_ports_for_entry(src_node.output_ports, gain_node.input_ports, mono):
                desired.add(pair)
            managed_node_pairs.add((src_node.id, gain_node.id))
            for pair in pair_ports(gain_node.output_ports, dst_node.input_ports):
                desired.add(pair)
            managed_node_pairs.add((gain_node.id, dst_node.id))
            return
        for pair in pair_ports_for_entry(src_node.output_ports, dst_node.input_ports, mono):
            desired.add(pair)
        managed_node_pairs.add((src_node.id, dst_node.id))

    def route_from_hub(hub_node, dest_node, gain_key=None, mono=False):
        """hub_node -> dest_node -- the mirror of route(), used where the
        source side is always-known-to-exist (Virtual Speaker's monitor, a
        custom conduit's base node) rather than something resolved from a
        label. Same gain/mono handling, just applied to the destination
        segment instead of the source segment."""
        if dest_node is None or hub_node is None:
            return
        gname = gain_node_name(gain_key) if gain_key else None
        if gname and gname in gain_specs:
            gain_node = find_virtual(nodes, gname)
            if gain_node is None:
                return
            for pair in pair_ports(hub_node.output_ports, gain_node.input_ports):
                desired.add(pair)
            managed_node_pairs.add((hub_node.id, gain_node.id))
            for pair in pair_ports_for_entry(gain_node.output_ports, dest_node.input_ports, mono):
                desired.add(pair)
            managed_node_pairs.add((gain_node.id, dest_node.id))
            return
        for pair in pair_ports_for_entry(hub_node.output_ports, dest_node.input_ports, mono):
            desired.add(pair)
        managed_node_pairs.add((hub_node.id, dest_node.id))

    def route_entries(scope, entries, nodes, predicate, sink_fn):
        """Iterate one list's entries, expanding each one's own
        auto-detect sweep individually (rather than unioning the whole
        list at once) so gain/mono only ever apply to an entry's own
        literal label, never to an auto-detected sibling it swept in --
        same rule volume already followed before mono/gain existed."""
        for entry in entries:
            if not entry.get("enabled", True):
                continue
            swept = expand_item_auto_detect(entry["label"], entry.get("auto_detect", DEFAULT_AUTO_DETECT), nodes, predicate)
            for label in swept:
                is_own = label == entry["label"]
                gain_key = f"{scope}:{label}" if is_own else None
                mono = entry.get("mono", False) if is_own else False
                sink_fn(label, gain_key, mono)

    # --- Mic side ---
    # conduit_virtual_mic is one node that's both linkable-into (its
    # input ports, same as a sink would have) and selectable as a
    # recording device (its output ports feed whatever selects it as
    # a source) -- see module.nix for why one node covers both roles.
    mic_effective_source = virtual_mic
    if virtual_mic:
        # If noise suppression is requested and its pool slot's processor
        # nodes actually exist (nix option enabled + rebuilt), splice it
        # in: Virtual Mic's own output feeds the processor's input, and
        # the processor's output becomes what everything downstream --
        # mic.outputs entries AND the system default source -- reads
        # from instead of Virtual Mic directly.
        mic_ns_names = noise_alloc.get("mic")
        if mic_ns_names:
            processor_in = find_virtual(nodes, mic_ns_names[0])
            processor_out = find_virtual(nodes, mic_ns_names[1])
            if processor_in:
                for pair in pair_ports(virtual_mic.output_ports, processor_in.input_ports):
                    desired.add(pair)
                managed_node_pairs.add((virtual_mic.id, processor_in.id))
            if processor_out:
                mic_effective_source = processor_out

        def mic_input(label, gain_key, mono):
            real_mic = find_node_verbose(nodes, label, is_producer, "mic input")
            route(real_mic, virtual_mic, gain_key, mono)
        route_entries("mic.inputs", state["mic"]["inputs"], nodes, is_producer, mic_input)

        def mic_output(label, gain_key, mono):
            app = find_node_verbose(nodes, label, is_consumer, "mic output")
            route(mic_effective_source, app, gain_key, mono)
        route_entries("mic.outputs", state["mic"]["outputs"], nodes, is_consumer, mic_output)

    # --- Speaker side ---
    bypass_set = expand_items(state["speaker"]["bypass"], nodes, is_producer)
    if virtual_speaker:
        def speaker_input(label, gain_key, mono):
            if label in bypass_set:
                return  # bypass always wins over inputs
            app = find_node_verbose(nodes, label, is_producer, "speaker input")
            route(app, virtual_speaker, gain_key, mono)
        route_entries("speaker.inputs", state["speaker"]["inputs"], nodes, is_producer, speaker_input)

        def speaker_output(label, gain_key, mono):
            destination = find_node_verbose(nodes, label, is_consumer, "speaker output")
            # destination can be real hardware or an app that wants to
            # consume the mix (e.g. a screen-share audio capture node).
            route_from_hub(virtual_speaker, destination, gain_key, mono)
        route_entries("speaker.outputs", state["speaker"]["outputs"], nodes, is_consumer, speaker_output)

    # --- Bypass: force-disconnect from virtual speaker, connect direct ---
    bypass_target_label = state["speaker"].get("bypass_target")
    bypass_target = (
        find_node_verbose(nodes, bypass_target_label, is_hardware_sink, "bypass target")
        if bypass_target_label else None
    )
    for entry in state["speaker"]["bypass"]:
        if not entry.get("enabled", True):
            continue
        swept = expand_item_auto_detect(entry["label"], entry.get("auto_detect", DEFAULT_AUTO_DETECT), nodes, is_producer)
        for label in swept:
            app = find_node_verbose(nodes, label, is_producer, "bypass app")
            if app is None:
                continue
            # Always mark (app -> virtual_speaker) as managed so any existing
            # link there gets pruned below, even with no bypass_target chosen yet.
            if virtual_speaker:
                managed_node_pairs.add((app.id, virtual_speaker.id))
            if bypass_target:
                is_own = label == entry["label"]
                gain_key = f"speaker.bypass:{label}" if is_own else None
                mono = entry.get("mono", False) if is_own else False
                route(app, bypass_target, gain_key, mono)

    # --- Custom conduits ---
    # Each conduit's own Input/Output lists behave exactly like the
    # Speaker panel's (same route()/route_from_hub() helpers); the only
    # extra piece is the optional internal tap into its as_microphone
    # sibling node, wired the same way Speaker Output routes to a
    # destination -- the primary node's monitor ports feed the tap's
    # input ports, so the tap always carries whatever the primary is
    # currently carrying.
    for conduit in state.get("custom", {}).get("conduits", []):
        base = find_virtual(nodes, custom_node_name(conduit["id"]))
        if base is None:
            continue  # not created by sync_custom_conduits() yet -- next cycle will pick it up
        cid = conduit["id"]
        label_prefix = f"custom[{conduit.get('name', cid)}]"

        def custom_input(label, gain_key, mono):
            src = find_node_verbose(nodes, label, is_producer, f"{label_prefix} input")
            route(src, base, gain_key, mono)
        route_entries(f"custom.{cid}.inputs", conduit.get("inputs", []), nodes, is_producer, custom_input)

        def custom_output(label, gain_key, mono):
            destination = find_node_verbose(nodes, label, is_consumer, f"{label_prefix} output")
            route_from_hub(base, destination, gain_key, mono)
        route_entries(f"custom.{cid}.outputs", conduit.get("outputs", []), nodes, is_consumer, custom_output)

        if conduit.get("as_microphone"):
            mic_tap = find_virtual(nodes, custom_mic_node_name(cid))
            if mic_tap:
                tap_source = base
                ns_names = noise_alloc.get(f"custom.{cid}")
                if ns_names:
                    processor_in = find_virtual(nodes, ns_names[0])
                    processor_out = find_virtual(nodes, ns_names[1])
                    if processor_in:
                        for pair in pair_ports(base.output_ports, processor_in.input_ports):
                            desired.add(pair)
                        managed_node_pairs.add((base.id, processor_in.id))
                    if processor_out:
                        tap_source = processor_out
                for pair in pair_ports(tap_source.output_ports, mic_tap.input_ports):
                    desired.add(pair)
                managed_node_pairs.add((tap_source.id, mic_tap.id))

    return desired, managed_node_pairs, mic_effective_source


def sync_custom_conduits(state, nodes):
    """Ensure every configured custom conduit's underlying PipeWire
    node(s) exist with the currently-configured name, and destroy any
    leftover ones that are no longer configured (a whole conduit
    deleted, or as_microphone turned back off). Nodes created here won't
    be visible until the NEXT cycle's fresh pw-dump -- fine, since this
    loop runs continuously anyway.

    node.description is set once at creation and PipeWire doesn't
    support changing it on a live node, so a rename is handled as
    destroy-then-recreate with the same technical node.name (which is
    what routing actually keys off, so nothing else needs to change) --
    any links to it get torn down and rebuilt by the next reconcile
    cycle via the usual undo-on-remove link cache, same as any other
    config edit."""
    configured = state.get("custom", {}).get("conduits", [])
    expected_names = set()
    for conduit in configured:
        base_name = custom_node_name(conduit["id"])
        expected_names.add(base_name)
        desired_desc = conduit.get("name", base_name)
        base_node = find_virtual(nodes, base_name)
        if base_node is None:
            _create_dynamic_node(base_name, desired_desc, "Audio/Sink")
        elif base_node.description != _sanitize_description(desired_desc):
            _destroy_dynamic_node(base_node.id, base_name)
            _create_dynamic_node(base_name, desired_desc, "Audio/Sink")

        if conduit.get("as_microphone"):
            mic_name = custom_mic_node_name(conduit["id"])
            expected_names.add(mic_name)
            desired_mic_desc = f"{desired_desc} (Mic)"
            mic_node = find_virtual(nodes, mic_name)
            if mic_node is None:
                _create_dynamic_node(mic_name, desired_mic_desc, "Audio/Source/Virtual")
            elif mic_node.description != _sanitize_description(desired_mic_desc):
                _destroy_dynamic_node(mic_node.id, mic_name)
                _create_dynamic_node(mic_name, desired_mic_desc, "Audio/Source/Virtual")

    for node in list(nodes.values()):
        if node.name.startswith(_CUSTOM_NODE_PREFIX) and node.name not in expected_names:
            _destroy_dynamic_node(node.id, node.name)


def _sanitize_description(description):
    """Same sanitization _create_dynamic_node applies before handing a
    description to pw-cli -- kept as its own function so sync_custom_
    conduits can compare against a live node's actual description using
    the exact same transform, rather than comparing against the raw
    (potentially still-quoted) configured name and false-triggering a
    rename loop every cycle."""
    return description.replace('"', "'")


def _create_dynamic_node(node_name, description, media_class):
    description = _sanitize_description(description)
    args = (
        "{ factory.name=support.null-audio-sink "
        f'node.name="{node_name}" node.description="{description}" '
        f"media.class={media_class} object.linger=true "
        "audio.position=[FL,FR] monitor.channel-volumes=true }"
    )
    result = subprocess.run(["pw-cli", "create-node", "adapter", args], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"conduit-daemon: failed to create {node_name!r}: {result.stderr.strip()}", flush=True)
    else:
        print(f"conduit-daemon: created dynamic node {node_name!r} ({media_class})", flush=True)


def _destroy_dynamic_node(node_id, node_name):
    result = subprocess.run(["pw-cli", "destroy", str(node_id)], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"conduit-daemon: failed to destroy {node_name!r} (id={node_id}): {result.stderr.strip()}", flush=True)
    else:
        print(f"conduit-daemon: destroyed dynamic node {node_name!r} (id={node_id})", flush=True)


def _load_link_cache():
    try:
        data = json.loads(LINK_CACHE_FILE.read_text())
        return {tuple(pair) for pair in data if isinstance(pair, list) and len(pair) == 2}
    except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
        return set()


def _save_link_cache(pairs):
    try:
        LINK_CACHE_FILE.write_text(json.dumps([list(p) for p in pairs]))
    except OSError:
        pass


def reconcile(state, nodes, dump):
    """Returns the mic_effective_source node from compute_desired_links,
    passed through so main() can feed it to enforce_defaults()."""
    desired, managed_pairs, mic_effective_source = compute_desired_links(state, nodes)
    existing = current_links(dump)

    # Build a lookup from port id -> owning node id, so we can tell whether
    # an existing link falls inside a pair of nodes we manage.
    port_owner = {}
    for node in nodes.values():
        for pid, _ in node.output_ports:
            port_owner[pid] = node.id
        for pid, _ in node.input_ports:
            port_owner[pid] = node.id

    # Undo-on-remove: a pair stops appearing in managed_pairs the instant
    # its config entry is removed or disabled, so pruning against
    # managed_pairs alone would leave that stale link connected forever
    # (nothing keeps recomputing "this used to be managed" once the entry
    # is gone). The GUI restarts this daemon on every edit too, so there's
    # no in-memory history to fall back on -- instead, the previous
    # cycle's managed_pairs is cached to disk and unioned in here, then
    # replaced with THIS cycle's fresh set at the end. A removed entry's
    # pair rides along for exactly one more cycle (enough to get pruned)
    # and then drops out of the cache on its own. Node ids are stable
    # within a single PipeWire session, so this survives daemon restarts
    # without needing to survive -- or caring about surviving -- a reboot.
    cached_pairs = _load_link_cache()
    prunable_pairs = managed_pairs | cached_pairs

    for out_port, in_port in existing:
        pair = (port_owner.get(out_port), port_owner.get(in_port))
        if pair in prunable_pairs and (out_port, in_port) not in desired:
            pw_unlink(out_port, in_port)

    for out_port, in_port in desired:
        if (out_port, in_port) not in existing:
            pw_link(out_port, in_port)

    _save_link_cache(managed_pairs)
    return mic_effective_source


def enforce_defaults(nodes, mic_default_node=None):
    virtual_speaker = find_virtual(nodes, VIRTUAL_SPEAKER)
    if virtual_speaker:
        subprocess.run(["wpctl", "set-default", str(virtual_speaker.id)], capture_output=True)
    target = mic_default_node or find_virtual(nodes, VIRTUAL_MIC)
    if target:
        subprocess.run(["wpctl", "set-default", str(target.id)], capture_output=True)


# ---------------------------------------------------------------------------
# MAIN LOOP
# ---------------------------------------------------------------------------

def main():
    ensure_config_exists()
    print("conduit-daemon: starting, watching", STATE_FILE, flush=True)
    printed_inventory = False
    while True:
        try:
            state = load_state()
            dump = pw_dump()
            if dump:
                nodes = build_graph(dump)

                if not printed_inventory:
                    # One-time-per-restart full inventory: name, class, and
                    # port counts for every node currently in the graph.
                    # `journalctl --user -u conduit-daemon | grep -i vencord`
                    # (or whatever you're chasing) shows exactly why a
                    # device isn't eligible -- most commonly 0 ports on
                    # both sides, which no matching logic can work around.
                    print(f"conduit-daemon: node inventory ({len(nodes)} nodes):", flush=True)
                    for node in sorted(nodes.values(), key=lambda n: n.display_label.lower()):
                        print(f"conduit-daemon:   {node.display_label!r} "
                              f"(name={node.name!r} class={node.media_class!r} "
                              f"in_ports={len(node.input_ports)} out_ports={len(node.output_ports)} "
                              f"client_id={node.client_id})", flush=True)
                    printed_inventory = True

                sync_custom_conduits(state, nodes)
                sync_gain_nodes(enumerate_gain_specs(state), nodes)
                mic_effective_source = reconcile(state, nodes, dump)
                enforce_defaults(nodes, mic_effective_source)
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
