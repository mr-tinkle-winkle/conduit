#!/usr/bin/env python3
"""
conduit_core.py -- the model shared by conduit_daemon.py and the GUI.

Everything that has to agree between "what the GUI shows" and "what the daemon
does" lives here: the state.json schema (v2) and its migration from the old
list format, PipeWire graph parsing, auto-detect, and the one function that
turns the config into concrete PipeWire port links (``compute_plan``).  The
GUI calls the exact same ``compute_plan`` to decide whether a configured link
is live (blue / orange / ...) or still pending (gray), so the two can never
drift apart.

STATE v2 (~/.config/conduit/state.json)
---------------------------------------
    {
      "version": 2,
      "speaker": {"enabled": true, "volume": 1.0, "muted": false},
      "mic":     {"enabled": true, "volume": 1.0, "muted": false,
                  "noise_suppression": "none" | "rnnoise" | "webrtc"},
      "custom":  {"next_id": 2, "conduits": [
                   {"id": 1, "name": "Game Mix", "enabled": true,
                    "as_speaker": true, "as_microphone": true,
                    "mic_noise_suppression": "none", "volume": 1.0, "muted": false}]},
      "cells": [
        {"id": "c3", "label": "Spotify", "box": "speaker", "speaker": false,
         "volume": 1.0, "blocked": false, "color": null,
         "auto_detect": {...same six keys as before...},
         "events": [{"kind": "active", "outcomes": {"c5": "block"}}]}
      ],
      "links": [
        {"id": "l7", "src": "c3", "src_port": "FL", "dst": "hub:speaker", "dst_port": "FL",
         "volume": 1.0, "blocked": false, "color": null, "anti": false}
      ],
      "next_cell_id": 4, "next_link_id": 8
    }

Cells
  A cell is one device or app stream, matched by label exactly like the old
  list entries (node.description for devices, application.name for streams),
  plus its own auto-detect sweep.  ``box`` is which canvas box it sits in
  ("speaker", "mic" or "custom.<id>").  ``speaker: true`` marks a real output
  device added with *New Speaker* (bypass semantics, see below).
  The same label may have several cells in different boxes.

Hub cells
  Never stored in "cells" -- they exist implicitly:
    hub:speaker            conduit_virtual_speaker
    hub:mic                conduit_virtual_mic
    hub:custom.<id>        conduit_custom_<id>         (the conduit's sink)
    hub:custom.<id>.mic    conduit_custom_<id>_mic     (only while as_microphone)
    ns:mic, ns:custom.<id> the noise-suppression processor (only while a method is set;
                           its input side is the processor's sink, its output side the
                           processor's source).  With NS off, ns:mic resolves to the hub.

Links
  Stored per port.  ``src_port`` / ``dst_port`` are PipeWire channel names
  (FL, FR, MONO, ...).  The special port "*" means "every port" -- used by
  anti-links (a blocked "*" -> "*" link prunes every link between the two
  cells) and accepted for unblocked links too (pairs channels like the old
  stereo matching).  A link whose channel doesn't exist on the live node falls
  back to the node's only port (mono devices) or the same position (FL=0,
  FR=1, ...), so a cell drawn as stereo still works against a mono mic.

Blocks
  ``blocked`` on a cell: every PipeWire link of every node the cell matches is
  removed each cycle -- including ones WirePlumber makes -- so it's silent.
  ``blocked`` on a link: that port pair is never linked (an anti-link).
  ``anti: true`` marks an anti-link Conduit created automatically for a bypass.

Volumes
  A cell's and a link's volume are inline gain stages, multiplied together per
  link: effective = src_cell.volume * link.volume * dst_cell.volume.  A link
  whose effective volume isn't 1.0 is routed through its own gain node
  (conduit_gain_<hash of link id>).  Hub cells' volume is the master volume
  (wpctl on the virtual device itself) and isn't part of that product.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "conduit"
STATE_FILE = CONFIG_DIR / "state.json"
LAYOUT_FILE = CONFIG_DIR / "layout.json"          # GUI only -- the daemon never reads it
LINK_CACHE_FILE = CONFIG_DIR / ".link_cache.json"
V1_BACKUP_FILE = CONFIG_DIR / "state.v1.backup.json"

SCHEMA_VERSION = 2

VIRTUAL_SPEAKER = "conduit_virtual_speaker"
VIRTUAL_MIC = "conduit_virtual_mic"
CUSTOM_NODE_PREFIX = "conduit_custom_"
GAIN_NODE_PREFIX = "conduit_gain_"
NS_NODE_PREFIX = "conduit_ns_"
OUR_PREFIXES = (CUSTOM_NODE_PREFIX, GAIN_NODE_PREFIX, NS_NODE_PREFIX)
OUR_FIXED_NAMES = {VIRTUAL_SPEAKER, VIRTUAL_MIC}

HUB_SPEAKER = "hub:speaker"
HUB_MIC = "hub:mic"
NS_MIC = "ns:mic"

NOISE_SUPPRESSION_METHODS = ("rnnoise", "webrtc")
EVENT_KINDS = ("active", "created", "inactive", "deleted")
EVENT_KIND_LABELS = {
    "active": "Device becomes Active",
    "created": "Device is Created",
    "inactive": "Device becomes Inactive",
    "deleted": "Device is Deleted",
}

DEFAULT_AUTO_DETECT = {
    "prefix": False, "keyword": False, "keyword_text": "", "same_app": False,
    "anti": False, "anti_keyword_text": "",
}

STEREO = ["FL", "FR"]
_CHANNEL_INDEX = {"FL": 0, "FR": 1, "FC": 2, "LFE": 3, "RL": 4, "RR": 5, "SL": 6, "SR": 7,
                  "MONO": 0, "AUX0": 0, "AUX1": 1, "AUX2": 2, "AUX3": 3}


def default_state() -> dict:
    return {
        "version": SCHEMA_VERSION,
        "speaker": {"enabled": True, "volume": 1.0, "muted": False},
        "mic": {"enabled": True, "volume": 1.0, "muted": False, "noise_suppression": "none"},
        "custom": {"next_id": 1, "conduits": []},
        "cells": [],
        "links": [],
        "next_cell_id": 1,
        "next_link_id": 1,
    }


# ---------------------------------------------------------------------------
# ids / names
# ---------------------------------------------------------------------------

def hub_custom(cid) -> str:
    return f"hub:custom.{cid}"


def hub_custom_mic(cid) -> str:
    return f"hub:custom.{cid}.mic"


def ns_cell_id(consumer_key: str) -> str:
    return f"ns:{consumer_key}"


def custom_node_name(cid) -> str:
    return f"{CUSTOM_NODE_PREFIX}{cid}"


def custom_mic_node_name(cid) -> str:
    return f"{CUSTOM_NODE_PREFIX}{cid}_mic"


def noise_processor_names(consumer_key: str, method: str) -> tuple[str, str]:
    safe_key = re.sub(r"[^a-zA-Z0-9_]", "_", consumer_key)
    base = f"{NS_NODE_PREFIX}{safe_key}_{method}"
    return f"{base}_in", f"{base}_out"


def gain_node_name(link_id: str) -> str:
    return f"{GAIN_NODE_PREFIX}{hashlib.md5(('link:' + link_id).encode()).hexdigest()[:12]}"


def is_hub(cell_id: str) -> bool:
    return cell_id.startswith("hub:") or cell_id.startswith("ns:")


def box_of_hub(cell_id: str) -> str:
    """hub:speaker -> speaker, hub:custom.3.mic -> custom.3, ns:custom.3 -> custom.3"""
    rest = cell_id.split(":", 1)[1]
    if rest.startswith("custom."):
        return "custom." + rest.split(".")[1]
    return rest


def box_label(state: dict, box: str) -> str:
    if box == "speaker":
        return "Speaker"
    if box == "mic":
        return "Microphone"
    c = conduit_by_box(state, box)
    return (c or {}).get("name") or box


def conduit_by_box(state: dict, box: str):
    if not box.startswith("custom."):
        return None
    try:
        cid = int(box.split(".")[1])
    except (IndexError, ValueError):
        return None
    return conduit_by_id(state, cid)


def conduit_by_id(state: dict, cid):
    for c in state.get("custom", {}).get("conduits", []):
        if c["id"] == cid:
            return c
    return None


def all_boxes(state: dict) -> list[str]:
    return ["speaker", "mic"] + [f"custom.{c['id']}" for c in state["custom"]["conduits"]]


def ns_method(state: dict, consumer_key: str) -> str:
    if consumer_key == "mic":
        return state["mic"].get("noise_suppression", "none")
    c = conduit_by_box(state, consumer_key)
    if c and c.get("as_microphone"):
        return c.get("mic_noise_suppression", "none")
    return "none"


def hub_cells_for_box(state: dict, box: str) -> list[str]:
    """The fixed hub cells a box shows, in display order."""
    if box == "speaker":
        return [HUB_SPEAKER]
    if box == "mic":
        out = [HUB_MIC]
        if ns_method(state, "mic") in NOISE_SUPPRESSION_METHODS:
            out.append(NS_MIC)
        return out
    c = conduit_by_box(state, box)
    if c is None:
        return []
    cid = c["id"]
    out = [hub_custom(cid)]
    if c.get("as_microphone"):
        out.append(hub_custom_mic(cid))
        if ns_method(state, box) in NOISE_SUPPRESSION_METHODS:
            out.append(ns_cell_id(box))
    return out


def hub_display_name(state: dict, cell_id: str) -> str:
    if cell_id == HUB_SPEAKER:
        return "Virtual Speaker"
    if cell_id == HUB_MIC:
        return "Virtual Mic"
    box = box_of_hub(cell_id)
    if cell_id.startswith("ns:"):
        method = ns_method(state, box)
        return f"Noise Suppression ({'RNNoise' if method == 'rnnoise' else 'WebRTC'})"
    name = box_label(state, box)
    if cell_id.endswith(".mic"):
        return f"{name} (Mic)"
    return name


def hub_enabled(state: dict, cell_id: str) -> bool:
    box = box_of_hub(cell_id)
    if box == "speaker":
        return state["speaker"].get("enabled", True)
    if box == "mic":
        return state["mic"].get("enabled", True)
    c = conduit_by_box(state, box)
    return bool(c and c.get("enabled", True))


def box_enabled(state: dict, box: str) -> bool:
    if box in ("speaker", "mic"):
        return state[box].get("enabled", True)
    c = conduit_by_box(state, box)
    return bool(c and c.get("enabled", True))


def box_is_microphone(state: dict, box: str) -> bool:
    if box == "mic":
        return True
    c = conduit_by_box(state, box)
    return bool(c and c.get("as_microphone"))


# ---------------------------------------------------------------------------
# state load / save / migration
# ---------------------------------------------------------------------------

def _valid_float(value, default=1.0):
    try:
        f = float(value)
        return f if f == f else default            # NaN guard
    except (TypeError, ValueError):
        return default


def _norm_auto_detect(ad) -> dict:
    out = dict(DEFAULT_AUTO_DETECT)
    if isinstance(ad, dict):
        out.update({k: ad[k] for k in DEFAULT_AUTO_DETECT if k in ad})
    return out


def _migrate_v1_items(loaded) -> list[dict]:
    """Every pre-v2 list shape -> [{label, enabled, volume, mono, auto_detect}]."""
    def entry(label, ad=None, enabled=True, volume=1.0, mono=False):
        return {"label": label, "enabled": enabled if isinstance(enabled, bool) else True,
                "volume": _valid_float(volume), "mono": bool(mono) if isinstance(mono, bool) else False,
                "auto_detect": _norm_auto_detect(ad)}
    if isinstance(loaded, list):
        res = []
        for e in loaded:
            if isinstance(e, str):
                res.append(entry(e))
            elif isinstance(e, dict) and isinstance(e.get("label"), str):
                res.append(entry(e["label"], e.get("auto_detect"), e.get("enabled", True),
                                 e.get("volume", 1.0), e.get("mono", False)))
        return res
    if isinstance(loaded, dict) and isinstance(loaded.get("items"), list):
        return [entry(e, loaded.get("auto_detect")) for e in loaded["items"] if isinstance(e, str)]
    return []


def is_v1(data) -> bool:
    return isinstance(data, dict) and data.get("version", 1) < SCHEMA_VERSION and (
        "mic" in data or "speaker" in data or "custom" in data)


def migrate_v1(data: dict) -> dict:
    """Old list config -> v2 cells + explicit per-port links.  Deterministic (same input, same
    ids) since the daemon may run it every cycle until something writes the file back.

      - every list entry becomes a cell in its box (one cell per label per box) and stereo
        per-port links to / from the hub: FL->FL, FR->FR (mono: every port to every port)
      - entry volume -> the volume of its links; auto_detect -> the cell
      - enabled=false -> its links are blocked
      - bypass entries -> direct links to the destination speaker cell, plus a default
        anti-link (blocked "*" -> "*") to the Virtual Speaker
      - bypass_target -> a speaker cell (speaker: true)
      - mic outputs with noise suppression on come from the processor (ns:mic), as before
    """
    st = default_state()
    mic = data.get("mic") if isinstance(data.get("mic"), dict) else {}
    spk = data.get("speaker") if isinstance(data.get("speaker"), dict) else {}
    st["mic"].update(volume=_valid_float(mic.get("volume")), muted=bool(mic.get("muted", False)))
    ns = mic.get("noise_suppression")
    st["mic"]["noise_suppression"] = ns if ns in NOISE_SUPPRESSION_METHODS else "none"
    st["speaker"].update(volume=_valid_float(spk.get("volume")), muted=bool(spk.get("muted", False)))

    cells_by_key: dict[tuple, dict] = {}

    def cell(box, label, ad=None, speaker=False):
        key = (box, label)
        c = cells_by_key.get(key)
        if c is None:
            c = new_cell(st, label, box)
            cells_by_key[key] = c
        if ad and any(ad.get(k) for k in ("prefix", "keyword", "same_app", "anti")) \
                and c["auto_detect"] == DEFAULT_AUTO_DETECT:
            c["auto_detect"] = _norm_auto_detect(ad)
        if speaker:
            c["speaker"] = True
        return c

    def links(src, dst, entry, mono=None):
        mono = entry.get("mono", False) if mono is None else mono
        pairs = [(a, b) for a in STEREO for b in STEREO] if mono else [(ch, ch) for ch in STEREO]
        for a, b in pairs:
            new_link(st, src, a, dst, b, volume=entry.get("volume", 1.0),
                     blocked=not entry.get("enabled", True))

    def hub_lists(box, hub_in, hub_out, inputs, outputs):
        for e in inputs:
            links(cell(box, e["label"], e["auto_detect"])["id"], hub_in, e)
        for e in outputs:
            links(hub_out, cell(box, e["label"], e["auto_detect"])["id"], e)

    bypass = _migrate_v1_items(spk.get("bypass"))
    bypass_labels = {e["label"] for e in bypass if e.get("enabled", True)}
    speaker_inputs = [e for e in _migrate_v1_items(spk.get("inputs")) if e["label"] not in bypass_labels]
    hub_lists("speaker", HUB_SPEAKER, HUB_SPEAKER, speaker_inputs, _migrate_v1_items(spk.get("outputs")))

    target_label = spk.get("bypass_target")
    target = cell("speaker", target_label, speaker=True) if target_label else None
    for e in bypass:
        app = cell("speaker", e["label"], e["auto_detect"])
        if target is not None:
            links(app["id"], target["id"], e)
        if e.get("enabled", True):
            new_link(st, app["id"], "*", HUB_SPEAKER, "*", blocked=True, anti=True)

    mic_out_hub = NS_MIC if st["mic"]["noise_suppression"] != "none" else HUB_MIC
    hub_lists("mic", HUB_MIC, mic_out_hub, _migrate_v1_items(mic.get("inputs")),
              _migrate_v1_items(mic.get("outputs")))

    custom = data.get("custom") if isinstance(data.get("custom"), dict) else {}
    max_id = 0
    for c in custom.get("conduits", []) if isinstance(custom.get("conduits"), list) else []:
        if not isinstance(c, dict) or not isinstance(c.get("id"), int):
            continue
        cid = c["id"]
        max_id = max(max_id, cid)
        method = c.get("mic_noise_suppression")
        st["custom"]["conduits"].append({
            "id": cid, "name": c.get("name") or f"Custom Conduit {cid}", "enabled": True,
            "as_speaker": bool(c.get("as_speaker", True)), "as_microphone": bool(c.get("as_microphone", False)),
            "mic_noise_suppression": method if method in NOISE_SUPPRESSION_METHODS else "none",
            "volume": _valid_float(c.get("volume")), "muted": bool(c.get("muted", False)),
        })
        box = f"custom.{cid}"
        hub_lists(box, hub_custom(cid), hub_custom(cid), _migrate_v1_items(c.get("inputs")),
                  _migrate_v1_items(c.get("outputs")))
    nid = custom.get("next_id")
    st["custom"]["next_id"] = nid if isinstance(nid, int) and nid > max_id else max_id + 1
    return st


def normalize(data) -> dict:
    """Any on-disk content -> a complete, valid v2 state (unknown keys on cells / links are kept)."""
    if not isinstance(data, dict):
        data = {}
    if is_v1(data):
        return migrate_v1(data)
    st = default_state()
    for hub in ("speaker", "mic"):
        src = data.get(hub) if isinstance(data.get(hub), dict) else {}
        st[hub].update({k: v for k, v in src.items()})
        st[hub]["enabled"] = bool(st[hub].get("enabled", True))
        st[hub]["volume"] = _valid_float(st[hub].get("volume"))
        st[hub]["muted"] = bool(st[hub].get("muted", False))
    if st["mic"].get("noise_suppression") not in NOISE_SUPPRESSION_METHODS:
        st["mic"]["noise_suppression"] = "none"

    custom = data.get("custom") if isinstance(data.get("custom"), dict) else {}
    max_id = 0
    for c in custom.get("conduits", []) if isinstance(custom.get("conduits"), list) else []:
        if not isinstance(c, dict) or not isinstance(c.get("id"), int):
            continue
        max_id = max(max_id, c["id"])
        method = c.get("mic_noise_suppression")
        st["custom"]["conduits"].append({**c,
            "name": c.get("name") or f"Custom Conduit {c['id']}",
            "enabled": bool(c.get("enabled", True)),
            "as_speaker": bool(c.get("as_speaker", True)),
            "as_microphone": bool(c.get("as_microphone", False)),
            "mic_noise_suppression": method if method in NOISE_SUPPRESSION_METHODS else "none",
            "volume": _valid_float(c.get("volume")), "muted": bool(c.get("muted", False))})
    nid = custom.get("next_id")
    st["custom"]["next_id"] = nid if isinstance(nid, int) and nid > max_id else max_id + 1

    valid_boxes = set(all_boxes(st))
    max_cell = 0
    for c in data.get("cells", []) if isinstance(data.get("cells"), list) else []:
        if not isinstance(c, dict) or not isinstance(c.get("id"), str) or not isinstance(c.get("label"), str):
            continue
        m = re.fullmatch(r"c(\d+)", c["id"])
        if m:
            max_cell = max(max_cell, int(m.group(1)))
        events = []
        for ev in c.get("events", []) if isinstance(c.get("events"), list) else []:
            if isinstance(ev, dict) and ev.get("kind") in EVENT_KINDS:
                outs = ev.get("outcomes") if isinstance(ev.get("outcomes"), dict) else {}
                events.append({"kind": ev["kind"],
                               "outcomes": {k: v for k, v in outs.items() if v in ("block", "unblock")}})
        st["cells"].append({**c,
            "box": c.get("box") if c.get("box") in valid_boxes else "speaker",
            "speaker": bool(c.get("speaker", False)),
            "volume": _valid_float(c.get("volume")),
            "blocked": bool(c.get("blocked", False)),
            "color": c.get("color") if isinstance(c.get("color"), str) else None,
            "auto_detect": _norm_auto_detect(c.get("auto_detect")),
            "events": events})
    cell_ids = {c["id"] for c in st["cells"]}

    max_link = 0
    for l in data.get("links", []) if isinstance(data.get("links"), list) else []:
        if not isinstance(l, dict) or not isinstance(l.get("id"), str):
            continue
        if not all(isinstance(l.get(k), str) for k in ("src", "src_port", "dst", "dst_port")):
            continue
        if (l["src"] not in cell_ids and not is_hub(l["src"])) or (l["dst"] not in cell_ids and not is_hub(l["dst"])):
            continue
        m = re.fullmatch(r"l(\d+)", l["id"])
        if m:
            max_link = max(max_link, int(m.group(1)))
        st["links"].append({**l, "volume": _valid_float(l.get("volume")),
                            "blocked": bool(l.get("blocked", False)), "anti": bool(l.get("anti", False)),
                            "color": l.get("color") if isinstance(l.get("color"), str) else None})
    nc, nl = data.get("next_cell_id"), data.get("next_link_id")
    st["next_cell_id"] = nc if isinstance(nc, int) and nc > max_cell else max_cell + 1
    st["next_link_id"] = nl if isinstance(nl, int) and nl > max_link else max_link + 1
    return st


def read_raw_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def load_state() -> dict:
    return normalize(read_raw_state() or {})


def atomic_write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_state(state: dict) -> None:
    out = copy.deepcopy(state)
    out["version"] = SCHEMA_VERSION
    atomic_write_json(STATE_FILE, out)


def ensure_config_exists() -> None:
    """Create a default state, or migrate a v1 file in place (keeping a backup of the original)."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    raw = read_raw_state()
    if raw is None and not STATE_FILE.exists():
        save_state(default_state())
    elif is_v1(raw):
        if not V1_BACKUP_FILE.exists():
            atomic_write_json(V1_BACKUP_FILE, raw)
        save_state(migrate_v1(raw))


def restart_daemon() -> None:
    subprocess.run(["systemctl", "--user", "restart", "conduit-daemon.service"], capture_output=True)


# ---------------------------------------------------------------------------
# model editing helpers (used by both editors)
# ---------------------------------------------------------------------------

def new_cell(state: dict, label: str, box: str, **extra) -> dict:
    cid = f"c{state['next_cell_id']}"
    state["next_cell_id"] += 1
    cell = {"id": cid, "label": label, "box": box, "speaker": False, "volume": 1.0, "blocked": False,
            "color": None, "auto_detect": dict(DEFAULT_AUTO_DETECT), "events": []}
    cell.update(extra)
    state["cells"].append(cell)
    return cell


def new_link(state: dict, src: str, src_port: str, dst: str, dst_port: str, **extra) -> dict:
    for l in state["links"]:          # never duplicate a port pair: update the existing one instead
        if (l["src"], l["src_port"], l["dst"], l["dst_port"]) == (src, src_port, dst, dst_port):
            for k, v in extra.items():
                l[k] = v
            return l
    lid = f"l{state['next_link_id']}"
    state["next_link_id"] += 1
    link = {"id": lid, "src": src, "src_port": src_port, "dst": dst, "dst_port": dst_port,
            "volume": 1.0, "blocked": False, "color": None, "anti": False}
    link.update(extra)
    state["links"].append(link)
    return link


def cell_by_id(state: dict, cell_id: str):
    for c in state["cells"]:
        if c["id"] == cell_id:
            return c
    return None


def link_by_id(state: dict, link_id: str):
    for l in state["links"]:
        if l["id"] == link_id:
            return l
    return None


def remove_links(state: dict, link_ids) -> None:
    ids = set(link_ids)
    removed = [l for l in state["links"] if l["id"] in ids]
    state["links"] = [l for l in state["links"] if l["id"] not in ids]
    # A bypass's automatic anti-link goes away with the last direct link to a speaker it was for.
    for l in removed:
        if l.get("blocked"):
            continue
        dst = cell_by_id(state, l["dst"])
        if dst is None or not dst.get("speaker"):
            continue
        still = any(x["src"] == l["src"] and not x.get("blocked") and
                    (cell_by_id(state, x["dst"]) or {}).get("speaker") for x in state["links"])
        if not still:
            state["links"] = [x for x in state["links"]
                              if not (x.get("anti") and x["src"] == l["src"] and x["dst"] == HUB_SPEAKER)]


def remove_cell(state: dict, cell_id: str) -> None:
    state["cells"] = [c for c in state["cells"] if c["id"] != cell_id]
    state["links"] = [l for l in state["links"] if cell_id not in (l["src"], l["dst"])]
    for c in state["cells"]:
        for ev in c.get("events", []):
            ev.get("outcomes", {}).pop(cell_id, None)


def remove_conduit(state: dict, cid) -> None:
    box = f"custom.{cid}"
    for c in [c for c in state["cells"] if c["box"] == box]:
        remove_cell(state, c["id"])
    hubs = {hub_custom(cid), hub_custom_mic(cid), ns_cell_id(box)}
    state["links"] = [l for l in state["links"] if l["src"] not in hubs and l["dst"] not in hubs]
    state["custom"]["conduits"] = [c for c in state["custom"]["conduits"] if c["id"] != cid]


def add_conduit(state: dict, name: str | None = None) -> dict:
    cid = state["custom"]["next_id"]
    state["custom"]["next_id"] += 1
    c = {"id": cid, "name": name or f"Custom Conduit {cid}", "enabled": True, "as_speaker": True,
         "as_microphone": False, "mic_noise_suppression": "none", "volume": 1.0, "muted": False}
    state["custom"]["conduits"].append(c)
    return c


def set_noise_suppression(state: dict, consumer_key: str, method: str) -> None:
    """Switch a box's processor.  For the main mic, links that read from the Virtual Mic move to
    the processor (and back), so whatever reached the mic keeps getting the same (denoised)
    signal -- same as the old list behaviour."""
    if consumer_key == "mic":
        old = state["mic"].get("noise_suppression", "none")
        state["mic"]["noise_suppression"] = method
        on_before, on_after = old in NOISE_SUPPRESSION_METHODS, method in NOISE_SUPPRESSION_METHODS
        if on_before != on_after:
            frm, to = (HUB_MIC, NS_MIC) if on_after else (NS_MIC, HUB_MIC)
            for l in state["links"]:
                if l["src"] == frm:
                    l["src"] = to
            if not on_after:
                state["links"] = [l for l in state["links"] if NS_MIC not in (l["src"], l["dst"])]
            _dedupe_links(state)
        return
    c = conduit_by_box(state, consumer_key)
    if c is not None:
        c["mic_noise_suppression"] = method
        if method not in NOISE_SUPPRESSION_METHODS:
            ns = ns_cell_id(consumer_key)
            state["links"] = [l for l in state["links"] if ns not in (l["src"], l["dst"])]


def _dedupe_links(state: dict) -> None:
    seen, out = set(), []
    for l in state["links"]:
        k = (l["src"], l["src_port"], l["dst"], l["dst_port"])
        if k not in seen:
            seen.add(k)
            out.append(l)
    state["links"] = out


def cell_label(state: dict, cell_id: str) -> str:
    if is_hub(cell_id):
        return hub_display_name(state, cell_id)
    c = cell_by_id(state, cell_id)
    return c["label"] if c else cell_id


# ---------------------------------------------------------------------------
# PipeWire graph
# ---------------------------------------------------------------------------

def pw_dump(log=print):
    try:
        out = subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=10, check=True).stdout
        return json.loads(out)
    except FileNotFoundError:
        log("conduit: pw-dump not found on PATH")
    except subprocess.CalledProcessError as e:
        log(f"conduit: pw-dump failed (exit {e.returncode}): {e.stderr.strip()}")
    except (subprocess.SubprocessError, json.JSONDecodeError) as e:
        log(f"conduit: pw-dump error: {e}")
    return []


class Node:
    __slots__ = ("id", "name", "description", "app_name", "media_class", "client_id", "state",
                 "output_ports", "input_ports")

    def __init__(self, id_, props, state=""):
        self.id = id_
        self.name = props.get("node.name", "") or ""
        self.description = props.get("node.description", self.name) or self.name
        self.app_name = props.get("application.name")
        self.media_class = props.get("media.class", "") or ""
        self.client_id = props.get("client.id")
        self.state = state or ""
        self.output_ports = []          # [(port_id, channel)]
        self.input_ports = []

    @property
    def is_ours(self) -> bool:
        return self.name in OUR_FIXED_NAMES or self.name.startswith(OUR_PREFIXES)

    @property
    def display_label(self) -> str:
        return self.app_name or self.description

    @property
    def running(self) -> bool:
        return self.state == "running"

    def ports(self, side: str):
        return self.output_ports if side == "out" else self.input_ports


def build_graph(dump) -> dict:
    nodes = {}
    for obj in dump:
        if obj.get("type") == "PipeWire:Interface:Node":
            info = obj.get("info") or {}
            nodes[obj["id"]] = Node(obj["id"], info.get("props") or {}, info.get("state") or "")
    for obj in dump:
        if obj.get("type") == "PipeWire:Interface:Port":
            info = obj.get("info") or {}
            props = info.get("props") or {}
            node = nodes.get(props.get("node.id"))
            if node is None:
                continue
            channel = props.get("audio.channel", "MONO")
            direction = info.get("direction") or props.get("port.direction") or ""
            if direction.startswith("out"):
                node.output_ports.append((obj["id"], channel))
            elif direction.startswith("in"):
                node.input_ports.append((obj["id"], channel))
    for node in nodes.values():
        node.output_ports.sort(key=lambda p: _CHANNEL_INDEX.get(p[1], 99))
        node.input_ports.sort(key=lambda p: _CHANNEL_INDEX.get(p[1], 99))
    return nodes


def current_links(dump) -> set:
    links = set()
    for obj in dump:
        if obj.get("type") == "PipeWire:Interface:Link":
            props = (obj.get("info") or {}).get("props") or {}
            o, i = props.get("link.output.port"), props.get("link.input.port")
            if o is not None and i is not None:
                links.add((o, i))
    return links


class Snapshot:
    """One pw-dump, parsed.  ``Snapshot.empty()`` when PipeWire isn't reachable."""

    def __init__(self, dump):
        self.ok = bool(dump)
        self.nodes = build_graph(dump) if dump else {}
        self.links = current_links(dump) if dump else set()
        self.by_name = {n.name: n for n in self.nodes.values()}
        self.port_owner = {}
        self.port_channel = {}
        for n in self.nodes.values():
            for pid, ch in n.output_ports + n.input_ports:
                self.port_owner[pid] = n.id
                self.port_channel[pid] = ch

    @classmethod
    def capture(cls, log=print) -> "Snapshot":
        return cls(pw_dump(log))

    @classmethod
    def empty(cls) -> "Snapshot":
        return cls([])


def is_producer(node: Node) -> bool:
    return not node.is_ours and len(node.output_ports) > 0


def is_consumer(node: Node) -> bool:
    return not node.is_ours and len(node.input_ports) > 0


def is_hardware_sink(node: Node) -> bool:
    return not node.is_ours and node.media_class == "Audio/Sink"


# ---------------------------------------------------------------------------
# auto-detect
# ---------------------------------------------------------------------------

_TRAILING_COUNTER_RE = re.compile(r'[\s\-_#(]*\d+\)?\s*$')


def strip_counter_suffix(label: str) -> str:
    return _TRAILING_COUNTER_RE.sub('', label).strip()


def expand_auto_detect(label: str, auto_detect: dict, nodes: dict, predicate) -> set:
    """The labels ONE cell covers: its own, plus live siblings its strategies sweep in."""
    result = {label}
    ad = auto_detect or DEFAULT_AUTO_DETECT
    if not (ad.get("prefix") or ad.get("keyword") or ad.get("same_app")):
        return result
    candidates = [n for n in nodes.values() if predicate(n)]
    if ad.get("prefix"):
        seed = strip_counter_suffix(label)
        result |= {n.display_label for n in candidates if strip_counter_suffix(n.display_label) == seed}
    kw = (ad.get("keyword_text") or "").strip().lower()
    if ad.get("keyword") and kw:
        result |= {n.display_label for n in candidates if kw in n.display_label.lower()}
    if ad.get("same_app"):
        seeds = {n.client_id for n in candidates if n.display_label == label and n.client_id is not None}
        if seeds:
            result |= {n.display_label for n in candidates if n.client_id in seeds}
    anti = [t.strip().lower() for t in (ad.get("anti_keyword_text") or "").split(",") if t.strip()]
    if ad.get("anti") and anti:
        result = {x for x in result if x == label or not any(t in x.lower() for t in anti)}
    return result


def _prefer_for_side(cands: list, side: str) -> list:
    """A device's sink and source often share one description ("... Analog Stereo"): for the
    output side prefer real sources over a sink's monitor, for the input side prefer sinks."""
    if len(cands) < 2:
        return cands
    if side == "out":
        pref = [n for n in cands if not n.media_class.endswith("Sink")]
    else:
        pref = [n for n in cands if not n.media_class.startswith("Audio/Source")]
    return pref or cands


def cell_nodes(state: dict, cell_id: str, side: str, snap: Snapshot) -> list:
    """Live nodes a cell stands for on one side ("out" = its output ports, "in" = its inputs)."""
    if is_hub(cell_id):
        n = hub_node(state, cell_id, side, snap)
        return [n] if n is not None else []
    cell = cell_by_id(state, cell_id)
    if cell is None:
        return []
    pred = is_producer if side == "out" else is_consumer
    labels = expand_auto_detect(cell["label"], cell.get("auto_detect"), snap.nodes, pred)
    out = []
    for label in sorted(labels):
        cands = [n for n in snap.nodes.values() if pred(n) and n.display_label == label]
        out.extend(_prefer_for_side(cands, side))
    return out


def cell_all_nodes(state: dict, cell_id: str, snap: Snapshot) -> list:
    """Every live node a cell matches, either side (for blocks, events, closed / inactive)."""
    seen, out = set(), []
    for side in ("out", "in"):
        for n in cell_nodes(state, cell_id, side, snap):
            if n.id not in seen:
                seen.add(n.id)
                out.append(n)
    if not out and not is_hub(cell_id):
        cell = cell_by_id(state, cell_id)
        if cell:   # a node with no ports at all (yet) still "exists"
            out = [n for n in snap.nodes.values() if not n.is_ours and n.display_label == cell["label"]]
    return out


def hub_node(state: dict, cell_id: str, side: str, snap: Snapshot):
    if cell_id == HUB_SPEAKER:
        return snap.by_name.get(VIRTUAL_SPEAKER)
    if cell_id == HUB_MIC:
        return snap.by_name.get(VIRTUAL_MIC)
    box = box_of_hub(cell_id)
    if cell_id.startswith("ns:"):
        method = ns_method(state, box)
        if method in NOISE_SUPPRESSION_METHODS:
            in_name, out_name = noise_processor_names(box, method)
            n = snap.by_name.get(out_name if side == "out" else in_name)
            if n is not None:
                return n
        # processor off / not up yet: behave like the hub it hangs off
        return hub_node(state, HUB_MIC if box == "mic" else hub_custom_mic(box.split(".")[1]), side, snap)
    c = conduit_by_box(state, box)
    if c is None:
        return None
    if cell_id.endswith(".mic"):
        return snap.by_name.get(custom_mic_node_name(c["id"])) if c.get("as_microphone") else None
    return snap.by_name.get(custom_node_name(c["id"]))


# ---------------------------------------------------------------------------
# port resolution
# ---------------------------------------------------------------------------

def channel_index(channel: str, fallback_list=None) -> int:
    if channel in _CHANNEL_INDEX:
        return _CHANNEL_INDEX[channel]
    if fallback_list and channel in fallback_list:
        return fallback_list.index(channel)
    m = re.search(r"(\d+)$", channel)
    return int(m.group(1)) if m else 0


def pick_ports(ports: list, channel: str) -> list:
    """Port ids on one side of a node for a stored channel name ("*" = all)."""
    if not ports:
        return []
    if channel == "*":
        return [p for p, _ in ports]
    for pid, ch in ports:
        if ch == channel:
            return [pid]
    if len(ports) == 1:
        return [ports[0][0]]
    idx = channel_index(channel)
    return [ports[idx][0]] if idx < len(ports) else []


def pair_ports(src_ports: list, dst_ports: list) -> list:
    """Channel-matched pairs, positional fallback (the old stereo behaviour)."""
    s = {c: p for p, c in src_ports}
    d = {c: p for p, c in dst_ports}
    common = [c for c in s if c in d]
    if common:
        return [(s[c], d[c]) for c in common]
    if len(src_ports) == 1:
        return [(src_ports[0][0], p) for p, _ in dst_ports]
    if len(dst_ports) == 1:
        return [(p, dst_ports[0][0]) for p, _ in src_ports]
    return list(zip((p for p, _ in src_ports), (p for p, _ in dst_ports)))


def port_pairs(src_node: Node, src_port: str, dst_node: Node, dst_port: str) -> list:
    sp, dp = src_node.output_ports, dst_node.input_ports
    if src_port == "*" and dst_port == "*":
        return pair_ports(sp, dp)
    return [(a, b) for a in pick_ports(sp, src_port) for b in pick_ports(dp, dst_port)]


# ---------------------------------------------------------------------------
# the plan: config + live graph -> concrete links
# ---------------------------------------------------------------------------

def effective_volume(state: dict, link: dict) -> float:
    v = _valid_float(link.get("volume"))
    for end in (link["src"], link["dst"]):
        if not is_hub(end):
            c = cell_by_id(state, end)
            if c:
                v *= _valid_float(c.get("volume"))
    return round(v, 6)


def link_active(state: dict, link: dict) -> bool:
    """Configured, unblocked, both ends exist in config and neither end is blocked / disabled."""
    if link.get("blocked"):
        return False
    for end in (link["src"], link["dst"]):
        if is_hub(end):
            if not hub_enabled(state, end):
                return False
            if end.endswith(".mic"):
                c = conduit_by_box(state, box_of_hub(end))
                if not (c and c.get("as_microphone")):
                    return False
        else:
            c = cell_by_id(state, end)
            if c is None or c.get("blocked"):
                return False
    return True


def gain_specs(state: dict) -> dict:
    """{gain node name: (volume, stereo?)} for every active link that needs its own gain stage.
    Computed from config alone, so the daemon can create them before routing through them."""
    specs = {}
    for l in state["links"]:
        if not link_active(state, l):
            continue
        v = effective_volume(state, l)
        if v != 1.0:
            specs[gain_node_name(l["id"])] = (v, l["src_port"] == "*" and l["dst_port"] == "*")
    return specs


class Plan:
    def __init__(self):
        self.desired = set()          # {(out_port, in_port)} Conduit wants
        self.managed_pairs = set()    # {(node_id, node_id)} whose other links may be pruned
        self.forbidden = set()        # {(out_port, in_port)} blocked links -- always removed
        self.blocked_nodes = set()    # node ids of blocked cells -- every link removed
        self.link_paths = {}          # link id -> [(out_port, in_port)] it expects (GUI liveness)
        self.internal = set()         # {(out_port, in_port)} hub plumbing (ns splice, mic taps)
        self.mic_default = None       # node the system default source should be
        self.gain_nodes = set()       # node ids of our gain nodes


def compute_plan(state: dict, snap: Snapshot) -> Plan:
    plan = Plan()
    if not snap.ok:
        return plan
    gains = gain_specs(state)
    plan.gain_nodes = {n.id for n in snap.nodes.values() if n.name.startswith(GAIN_NODE_PREFIX)}

    for c in state["cells"]:
        if c.get("blocked"):
            plan.blocked_nodes |= {n.id for n in cell_all_nodes(state, c["id"], snap)}

    def add_internal(src, dst):
        if src is None or dst is None:
            return
        for pair in pair_ports(src.output_ports, dst.input_ports):
            plan.desired.add(pair)
            plan.internal.add(pair)
        plan.managed_pairs.add((src.id, dst.id))

    # -- hub plumbing: noise-suppression splices and custom mic taps
    vmic = snap.by_name.get(VIRTUAL_MIC)
    plan.mic_default = vmic
    m = ns_method(state, "mic")
    if m in NOISE_SUPPRESSION_METHODS:
        pin, pout = (snap.by_name.get(n) for n in noise_processor_names("mic", m))
        add_internal(vmic, pin)
        if pout is not None:
            plan.mic_default = pout
    for c in state["custom"]["conduits"]:
        if not c.get("as_microphone"):
            continue
        base, tap = snap.by_name.get(custom_node_name(c["id"])), snap.by_name.get(custom_mic_node_name(c["id"]))
        src = base
        m = ns_method(state, f"custom.{c['id']}")
        if m in NOISE_SUPPRESSION_METHODS:
            pin, pout = (snap.by_name.get(n) for n in noise_processor_names(f"custom.{c['id']}", m))
            if pin is not None and pout is not None:
                add_internal(base, pin)
                src = pout
        add_internal(src, tap)

    # -- configured links
    for l in state["links"]:
        srcs = [n for n in cell_nodes(state, l["src"], "out", snap) if n.id not in plan.blocked_nodes]
        dsts = [n for n in cell_nodes(state, l["dst"], "in", snap) if n.id not in plan.blocked_nodes]
        if l.get("blocked"):
            for s in srcs:
                for d in dsts:
                    if s.id != d.id:
                        plan.forbidden.update(port_pairs(s, l["src_port"], d, l["dst_port"]))
            continue
        if not link_active(state, l):
            continue
        path = []
        gname = gain_node_name(l["id"])
        if gname in gains:
            g = snap.by_name.get(gname)
            if g is None:
                plan.link_paths[l["id"]] = []      # gain node not up yet: next cycle
                continue
            stereo = gains[gname][1]
            for s in srcs:
                pairs = (pair_ports(s.output_ports, g.input_ports) if stereo else
                         [(a, b) for a in pick_ports(s.output_ports, l["src_port"]) for b in pick_ports(g.input_ports, "*")])
                path += pairs
                if pairs:
                    plan.managed_pairs.add((s.id, g.id))
            for d in dsts:
                pairs = (pair_ports(g.output_ports, d.input_ports) if stereo else
                         [(a, b) for a in pick_ports(g.output_ports, "*") for b in pick_ports(d.input_ports, l["dst_port"])])
                path += pairs
                if pairs:
                    plan.managed_pairs.add((g.id, d.id))
        else:
            for s in srcs:
                for d in dsts:
                    if s.id == d.id:
                        continue
                    pairs = port_pairs(s, l["src_port"], d, l["dst_port"])
                    path += pairs
                    if pairs:
                        plan.managed_pairs.add((s.id, d.id))
        plan.link_paths[l["id"]] = path
        plan.desired.update(path)

    plan.desired -= plan.forbidden
    if plan.blocked_nodes:
        plan.desired = {(o, i) for o, i in plan.desired
                        if snap.port_owner.get(o) not in plan.blocked_nodes
                        and snap.port_owner.get(i) not in plan.blocked_nodes}
    return plan


def link_live(plan: Plan, snap: Snapshot, link_id: str) -> bool:
    path = plan.link_paths.get(link_id) or []
    return bool(path) and all(p in snap.links for p in path)


def unmanaged_links(state: dict, snap: Snapshot, plan: Plan) -> list:
    """Live PipeWire links Conduit didn't make, touching a node some cell / hub stands for:
    [(out_port, in_port)].  Gain nodes and hub plumbing are Conduit's own, never listed."""
    ours = set(plan.desired) | set(plan.internal)
    watched = set()
    for c in state["cells"]:
        watched |= {n.id for n in cell_all_nodes(state, c["id"], snap)}
    for box in all_boxes(state):
        for h in hub_cells_for_box(state, box):
            for side in ("out", "in"):
                n = hub_node(state, h, side, snap)
                if n is not None:
                    watched.add(n.id)
    out = []
    for o, i in snap.links:
        if (o, i) in ours:
            continue
        no, ni = snap.port_owner.get(o), snap.port_owner.get(i)
        if no is None or ni is None or no in plan.gain_nodes or ni in plan.gain_nodes:
            continue
        if no in watched or ni in watched:
            out.append((o, i))
    return out
