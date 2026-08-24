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
rather than being forced back to 100% each cycle. This only applies to
the device an entry's own label matches, not to any auto-detected
siblings it sweeps in -- add each sibling as its own entry with its
own volume if you want that.
"""

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

DEFAULT_AUTO_DETECT = {
    "prefix": False, "keyword": False, "keyword_text": "", "same_app": False,
    "anti": False, "anti_keyword_text": "",
}

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


def _migrate_items(loaded):
    """Normalize a config list to
    [{"label", "enabled", "volume", "auto_detect"}, ...], transparently
    handling every prior on-disk shape rather than discarding an
    already-working config:
      - flat list of strings (original format, pre auto-detect)
      - {"items": [...strings...], "auto_detect": {...}} (short-lived
        list-wide auto-detect format)
      - list of {"label", "auto_detect"} dicts (pre enabled/volume)
      - already-current list of {"label", "enabled", "volume", "auto_detect"} dicts
    """
    def fresh_entry(label, auto_detect_seed=None, enabled=True, volume=1.0):
        ad = dict(DEFAULT_AUTO_DETECT)
        if isinstance(auto_detect_seed, dict):
            ad.update({k: v for k, v in auto_detect_seed.items() if k in DEFAULT_AUTO_DETECT})
        return {
            "label": label,
            "enabled": bool(enabled) if isinstance(enabled, bool) else True,
            "volume": float(volume) if isinstance(volume, (int, float)) else 1.0,
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
                    entry.get("enabled", True), entry.get("volume", 1.0),
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
    speaker = data.get("speaker", {}) if isinstance(data.get("speaker"), dict) else {}
    state["speaker"]["inputs"] = _migrate_items(speaker.get("inputs"))
    state["speaker"]["outputs"] = _migrate_items(speaker.get("outputs"))
    state["speaker"]["bypass"] = _migrate_items(speaker.get("bypass"))
    if speaker.get("bypass_target"):
        state["speaker"]["bypass_target"] = speaker["bypass_target"]
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
        for label in expand_items(state["mic"]["inputs"], nodes, is_producer):
            real_mic = find_node_verbose(nodes, label, is_producer, "mic input")
            route(real_mic, virtual_mic)
        for label in expand_items(state["mic"]["outputs"], nodes, is_consumer):
            app = find_node_verbose(nodes, label, is_consumer, "mic output")
            route(virtual_mic, app)

    # --- Speaker side ---
    bypass_set = expand_items(state["speaker"]["bypass"], nodes, is_producer)
    if virtual_speaker:
        for label in expand_items(state["speaker"]["inputs"], nodes, is_producer):
            if label in bypass_set:
                continue  # bypass always wins over inputs
            app = find_node_verbose(nodes, label, is_producer, "speaker input")
            route(app, virtual_speaker)
        for label in expand_items(state["speaker"]["outputs"], nodes, is_consumer):
            destination = find_node_verbose(nodes, label, is_consumer, "speaker output")
            if destination is None or virtual_speaker is None:
                continue
            for pair in pair_ports(virtual_speaker.output_ports, destination.input_ports):
                # virtual_speaker itself has no output ports (it's a sink) --
                # what we actually want is its *monitor* ports, which pw-dump
                # reports as this same node's output ports since a sink's
                # monitor ports live on the sink node itself. The
                # destination can be real hardware or an app that wants to
                # consume the mix (e.g. a screen-share audio capture node).
                desired.add(pair)
            managed_node_pairs.add((virtual_speaker.id, destination.id))

    # --- Bypass: force-disconnect from virtual speaker, connect direct ---
    bypass_target_label = state["speaker"].get("bypass_target")
    bypass_target = (
        find_node_verbose(nodes, bypass_target_label, is_hardware_sink, "bypass target")
        if bypass_target_label else None
    )
    for label in bypass_set:
        app = find_node_verbose(nodes, label, is_producer, "bypass app")
        if app is None:
            continue
        # Always mark (app -> virtual_speaker) as managed so any existing
        # link there gets pruned below, even with no bypass_target chosen yet.
        if virtual_speaker:
            managed_node_pairs.add((app.id, virtual_speaker.id))
        if bypass_target:
            route(app, bypass_target)

    return desired, managed_node_pairs


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


def enforce_defaults(nodes):
    virtual_speaker = find_virtual(nodes, VIRTUAL_SPEAKER)
    virtual_mic = find_virtual(nodes, VIRTUAL_MIC)
    if virtual_speaker:
        subprocess.run(["wpctl", "set-default", str(virtual_speaker.id)], capture_output=True)
    if virtual_mic:
        subprocess.run(["wpctl", "set-default", str(virtual_mic.id)], capture_output=True)


def enforce_volumes(state, nodes):
    """Continuously pin each enabled entry's volume multiplier (see the
    ENABLED / VOLUME section in the module docstring). Applies only to
    the device an entry's own label matches -- not to any auto-detected
    siblings -- and only when volume != 1.0, since 1.0 means "not
    managing this device's volume" rather than "force it to 100%"."""
    all_entries = (
        [(e, is_producer) for e in state["mic"]["inputs"]] +
        [(e, is_consumer) for e in state["mic"]["outputs"]] +
        [(e, is_producer) for e in state["speaker"]["inputs"]] +
        [(e, is_consumer) for e in state["speaker"]["outputs"]] +
        [(e, is_producer) for e in state["speaker"]["bypass"]]
    )
    for entry, predicate in all_entries:
        if not entry.get("enabled", True):
            continue
        volume = entry.get("volume", 1.0)
        if volume == 1.0:
            continue
        node = find_node(nodes, entry["label"], predicate)
        if node is None:
            continue
        subprocess.run(
            ["wpctl", "set-volume", str(node.id), f"{volume}", "-l", str(VOLUME_BOOST_LIMIT)],
            capture_output=True,
        )


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

                enforce_defaults(nodes)
                enforce_volumes(state, nodes)
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
