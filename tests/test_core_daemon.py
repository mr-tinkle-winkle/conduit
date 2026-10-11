"""Core + daemon tests against the fake PipeWire.   python3 tests/test_core_daemon.py"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import conduit_core as core  # noqa: E402
import conduit_daemon as daemon  # noqa: E402
from fake_pw import standard_graph, V1_README_STATE  # noqa: E402

FAILS = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def use_tmp_config():
    d = Path(tempfile.mkdtemp())
    core.CONFIG_DIR = d
    core.STATE_FILE = d / "state.json"
    core.LAYOUT_FILE = d / "layout.json"
    core.LINK_CACHE_FILE = d / ".link_cache.json"
    core.V1_BACKUP_FILE = d / "state.v1.backup.json"
    return d


def fake_ns(pw):
    """Replace the `pipewire -c` subprocess with nodes appearing directly in the fake graph."""
    def start(key, method, desc):
        if (key, method) in daemon._NS_PROCESSES:
            return
        i, o = core.noise_processor_names(key, method)
        nin = pw.add_node(i, desc, "Audio/Sink", ins=("FL", "FR"))
        nout = pw.add_node(o, desc, "Audio/Source", outs=("FL", "FR"))

        class P:
            pid = 0
            returncode = None
            def poll(self): return None
            def terminate(self): pw.remove_node(nin); pw.remove_node(nout)
            def wait(self, timeout=None): return 0
        daemon._NS_PROCESSES[(key, method)] = {"popen": P(), "config_path": Path("/nonexistent")}
    daemon._start_ns = start


def cycles(pw, n=3, events=None):
    events = events or daemon.EventEngine()
    for _ in range(n):
        state = core.load_state()
        snap = core.Snapshot(pw.dump())
        daemon.run_cycle(state, snap, events)
    return events


def has_link(pw, a, ach, b, bch):
    try:
        return (pw.port(a, "output", ach), pw.port(b, "input", bch)) in pw.link_set()
    except KeyError:
        return False


def links_between(pw, a, b):
    out = []
    for o, i in pw.link_set():
        if pw.ports[o]["node"] == a and pw.ports[i]["node"] == b:
            out.append((pw.ports[o]["channel"], pw.ports[i]["channel"]))
    return sorted(out)


# ---------------------------------------------------------------- migration
def test_migration():
    st = core.migrate_v1(copy.deepcopy(V1_README_STATE))
    labels = {(c["box"], c["label"]) for c in st["cells"]}
    check(("mic", "Blue Yeti") in labels, "mic input becomes a mic-box cell")
    check(("speaker", "spotify") in labels, "bypass app becomes a speaker-box cell")
    tgt = [c for c in st["cells"] if c["label"] == "Ryzen HD Audio Controller Analog Stereo"]
    check(len(tgt) == 1 and tgt[0]["speaker"], "bypass destination and output share one speaker cell")
    yeti = next(c for c in st["cells"] if c["label"] == "Blue Yeti")
    ylinks = [l for l in st["links"] if l["src"] == yeti["id"]]
    check(len(ylinks) == 4, "mono entry -> every port to every port (4 links)")
    sp = next(c for c in st["cells"] if c["label"] == "spotify")
    anti = [l for l in st["links"] if l["src"] == sp["id"] and l["dst"] == core.HUB_SPEAKER]
    check(len(anti) == 1 and anti[0]["blocked"] and anti[0]["anti"] and anti[0]["src_port"] == "*",
          "bypass gets a default anti-link to the Virtual Speaker")
    direct = [l for l in st["links"] if l["src"] == sp["id"] and l["dst"] == tgt[0]["id"]]
    check(len(direct) == 2 and all(l["volume"] == 1.5 for l in direct), "bypass -> direct per-port links keep volume")
    p4 = next(c for c in st["cells"] if c["label"] == "P4next Analog Stereo")
    check(p4["box"] == "custom.1" and all(l["blocked"] for l in st["links"] if l["src"] == p4["id"]),
          "disabled entry -> blocked links, in its conduit's box")
    vc = next(c for c in st["cells"] if c["label"] == "vencord-screen-share")
    check(vc["auto_detect"]["same_app"] and vc["auto_detect"]["anti_keyword_text"] == "mic", "auto-detect kept on cell")
    check(st["mic"]["noise_suppression"] == "rnnoise" and st["custom"]["conduits"][0]["enabled"], "hub settings kept")
    again = core.migrate_v1(copy.deepcopy(V1_README_STATE))
    check(again == st, "migration is deterministic")
    check(core.normalize(json.loads(json.dumps(st))) == st, "v2 round-trips through normalize")


def test_ensure_migrates_file():
    d = use_tmp_config()
    core.STATE_FILE.write_text(json.dumps(V1_README_STATE))
    core.ensure_config_exists()
    raw = json.loads(core.STATE_FILE.read_text())
    check(raw.get("version") == 2, "v1 file rewritten as v2")
    check(json.loads(core.V1_BACKUP_FILE.read_text()) == V1_README_STATE, "v1 backup kept")


# ---------------------------------------------------------------- daemon
def test_daemon_routing():
    use_tmp_config()
    pw, ids = standard_graph()
    pw.install(core, daemon)
    fake_ns(pw)
    core.save_state(core.migrate_v1(copy.deepcopy(V1_README_STATE)))
    cycles(pw, 4)

    check(links_between(pw, ids["chromium"], ids["vspk"]) == [("FL", "FL"), ("FR", "FR")], "Chromium -> Virtual Speaker")
    check(links_between(pw, ids["spotify"], ids["vspk"]) == [], "bypass anti-link prunes WirePlumber's spotify -> vspk")
    gain = pw.node_by_name(core.gain_node_name(
        next(l for l in core.load_state()["links"] if l["dst_port"] == "FL" and core.cell_label(core.load_state(), l["src"]) == "spotify" and not l["blocked"])["id"]))
    check(gain is not None, "volume 1.5 link has a gain node")
    check(pw.volumes.get(gain) == 1.5, "gain node pinned to 1.5")
    check(links_between(pw, ids["spotify"], gain) == [("FL", "MONO")], "spotify FL -> gain")
    check(links_between(pw, gain, ids["ryzen_sink"]) == [("MONO", "FL")], "gain -> Ryzen FL")
    check(links_between(pw, ids["vspk"], ids["ryzen_sink"]) == [("FL", "FL"), ("FR", "FR")], "vspk monitor -> Ryzen sink (not the source)")
    check(links_between(pw, ids["yeti"], ids["vmic"]) == [("MONO", "FL"), ("MONO", "FR")], "mono Yeti fans out to both mic channels")
    ns_in = pw.node_by_name(core.noise_processor_names("mic", "rnnoise")[0])
    ns_out = pw.node_by_name(core.noise_processor_names("mic", "rnnoise")[1])
    check(links_between(pw, ids["vmic"], ns_in) == [("FL", "FL"), ("FR", "FR")], "vmic -> rnnoise splice")
    check(pw.defaults and pw.defaults[-1] == ns_out, "default source = processor output")
    base = pw.node_by_name("conduit_custom_1")
    tap = pw.node_by_name("conduit_custom_1_mic")
    check(base is not None and tap is not None, "custom conduit + mic tap created")
    wi, wo = (pw.node_by_name(n) for n in core.noise_processor_names("custom.1", "webrtc"))
    check(links_between(pw, base, wi) and links_between(pw, wo, tap), "custom: base -> webrtc -> tap")
    check(links_between(pw, ids["p4"], base) == [], "blocked (disabled) entry not linked")
    check(links_between(pw, ids["vspk"], ids["vencord"]) == [("FL", "FL"), ("FR", "FR")], "vspk -> vencord")
    check(links_between(pw, ids["vmic"], ids["obs"]) == [("FL", "FL"), ("FR", "FR")], "unmanaged WirePlumber link left alone")


def test_per_port_and_blocks():
    use_tmp_config()
    pw, ids = standard_graph()
    pw.install(core, daemon)
    fake_ns(pw)
    st = core.default_state()
    chrome = core.new_cell(st, "Chromium", "speaker")
    core.new_link(st, chrome["id"], "FL", core.HUB_SPEAKER, "FL")
    core.save_state(st)
    cycles(pw, 3)
    check(links_between(pw, ids["chromium"], ids["vspk"]) == [("FL", "FL")], "per-port link: only FL, WirePlumber's FR pruned")

    st = core.load_state()
    obs = core.new_cell(st, "OBS", "mic")
    obs["blocked"] = True
    core.save_state(st)
    cycles(pw, 2)
    check(links_between(pw, ids["vmic"], ids["obs"]) == [], "blocked cell: every link of the node removed")

    st = core.load_state()
    sp = core.new_cell(st, "spotify", "speaker")
    core.new_link(st, sp["id"], "FR", core.HUB_SPEAKER, "FR", blocked=True)
    core.save_state(st)
    cycles(pw, 2)
    check(links_between(pw, ids["spotify"], ids["vspk"]) == [("FL", "FL")], "blocked per-port link prunes just that pair")

    # remove the Chromium link -> undone once through the link cache (even across a 'restart')
    st = core.load_state()
    core.remove_links(st, [l["id"] for l in st["links"] if l["src"] == chrome["id"]])
    core.save_state(st)
    cycles(pw, 1, daemon.EventEngine())
    check(links_between(pw, ids["chromium"], ids["vspk"]) == [], "removed link undone via link cache")

    # cell volume x link volume
    st = core.load_state()
    sp = core.cell_by_id(st, sp["id"])
    sp["volume"] = 2.0
    l = core.new_link(st, sp["id"], "FL", core.HUB_SPEAKER, "FL", volume=0.25)
    core.save_state(st)
    cycles(pw, 3)
    g = pw.node_by_name(core.gain_node_name(l["id"]))
    check(g is not None and pw.volumes.get(g) == 0.5, "effective gain = cell 2.0 x link 0.25")

    # hub disabled -> its links go
    st = core.load_state()
    st["speaker"]["enabled"] = False
    core.save_state(st)
    cycles(pw, 2)
    check(links_between(pw, g, ids["vspk"]) == [], "disabled hub: links into it removed")


def test_events():
    use_tmp_config()
    pw, ids = standard_graph()
    pw.install(core, daemon)
    fake_ns(pw)
    st = core.default_state()
    obs = core.new_cell(st, "OBS", "mic")
    sp = core.new_cell(st, "spotify", "speaker")
    obs["events"] = [{"kind": "active", "outcomes": {sp["id"]: "block"}},
                     {"kind": "inactive", "outcomes": {sp["id"]: "unblock"}}]
    core.save_state(st)
    ev = cycles(pw, 3)
    check(not core.cell_by_id(core.load_state(), sp["id"])["blocked"], "no event for a device already idle at start")
    pw.nodes[ids["obs"]]["state"] = "running"
    cycles(pw, 1, ev)
    check(not core.cell_by_id(core.load_state(), sp["id"])["blocked"], "active is debounced (1 poll isn't enough)")
    cycles(pw, 2, ev)
    check(core.cell_by_id(core.load_state(), sp["id"])["blocked"], "OBS active -> spotify blocked (persisted)")
    check(links_between(pw, ids["spotify"], ids["vspk"]) == [], "...and silenced")
    pw.nodes[ids["obs"]]["state"] = "idle"
    cycles(pw, 3, ev)
    check(not core.cell_by_id(core.load_state(), sp["id"])["blocked"], "OBS inactive -> spotify unblocked")

    st = core.load_state()
    core.cell_by_id(st, obs["id"])["events"] = [{"kind": "deleted", "outcomes": {sp["id"]: "block"}},
                                                {"kind": "created", "outcomes": {sp["id"]: "unblock"}}]
    core.save_state(st)
    ev = cycles(pw, 2)
    pw.remove_node(ids["obs"])
    cycles(pw, 1, ev)
    check(core.cell_by_id(core.load_state(), sp["id"])["blocked"], "OBS deleted -> spotify blocked")
    pw.add_node("OBS", "OBS (Desktop Audio)", "Stream/Input/Audio", app="OBS", ins=("FL", "FR"), state="idle")
    cycles(pw, 1, ev)
    check(not core.cell_by_id(core.load_state(), sp["id"])["blocked"], "OBS created -> spotify unblocked")


def test_unmanaged_and_liveness():
    use_tmp_config()
    pw, ids = standard_graph()
    st = core.default_state()
    chrome = core.new_cell(st, "Chromium", "speaker")
    l1 = core.new_link(st, chrome["id"], "FL", core.HUB_SPEAKER, "FL")
    l2 = core.new_link(st, core.HUB_SPEAKER, "FL", core.new_cell(st, "OBS", "speaker")["id"], "FL")
    snap = core.Snapshot(pw.dump())
    plan = core.compute_plan(st, snap)
    check(core.link_live(plan, snap, l1["id"]), "existing link reads as live")
    check(not core.link_live(plan, snap, l2["id"]), "configured-but-missing link reads as pending")
    um = core.unmanaged_links(st, snap, plan)
    owners = {(snap.nodes[snap.port_owner[o]].display_label, snap.nodes[snap.port_owner[i]].display_label) for o, i in um}
    check(("spotify", "Conduit Virtual Speaker") in owners, "WirePlumber's spotify -> vspk listed as unmanaged")
    check(("Chromium", "Conduit Virtual Speaker") in owners, "Chromium FR (not configured) listed as unmanaged")
    check(len([1 for o, i in um if snap.port_owner[o] == ids["chromium"]]) == 1, "configured Chromium FL not listed")


def test_ns_toggle_moves_links():
    st = core.default_state()
    discord = core.new_cell(st, "Discord", "mic")
    core.new_link(st, core.HUB_MIC, "FL", discord["id"], "FL")
    core.set_noise_suppression(st, "mic", "rnnoise")
    check(st["links"][0]["src"] == core.NS_MIC, "turning NS on moves mic outputs to the processor")
    core.set_noise_suppression(st, "mic", "none")
    check(st["links"][0]["src"] == core.HUB_MIC, "...and back")


def test_remove_bypass_removes_anti():
    st = core.migrate_v1(copy.deepcopy(V1_README_STATE))
    sp = next(c for c in st["cells"] if c["label"] == "spotify")
    direct = [l["id"] for l in st["links"] if l["src"] == sp["id"] and not l["blocked"]]
    core.remove_links(st, direct)
    check(not any(l["src"] == sp["id"] for l in st["links"]), "deleting the bypass links removes its auto anti-link")


if __name__ == "__main__":
    test_migration()
    test_ensure_migrates_file()
    test_daemon_routing()
    test_per_port_and_blocks()
    test_events()
    test_unmanaged_and_liveness()
    test_ns_toggle_moves_links()
    test_remove_bypass_removes_anti()
    print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"))
    sys.exit(1 if FAILS else 0)
