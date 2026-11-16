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
import conduit_eq  # noqa: E402
from fake_pw import standard_graph, fake_spawn, V1_README_STATE  # noqa: E402

FAILS = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def use_tmp_config():
    d = Path(tempfile.mkdtemp())
    core.CONFIG_DIR = d
    daemon._last_cache_written = None
    core.STATE_FILE = d / "state.json"
    core.LAYOUT_FILE = d / "layout.json"
    core.LINK_CACHE_FILE = d / ".link_cache.json"
    core.V1_BACKUP_FILE = d / "state.v1.backup.json"
    return d


CLOCK = [1000.0]
daemon._RNNOISE_LADSPA_PATH = "/fake/librnnoise_ladspa.so"


def new_runtime(pw):
    """A daemon Runtime whose helper processes are nodes appearing in the fake graph."""
    rt = daemon.Runtime()
    rt.helpers.spawn = fake_spawn(pw)
    return rt


def cycles(pw, n=3, rt=None, slow=True):
    rt = rt or new_runtime(pw)
    for _ in range(n):
        CLOCK[0] += 2.0
        state = core.load_state()
        snap = core.Snapshot(pw.dump())
        daemon.run_cycle(state, snap, rt, slow=slow, now=CLOCK[0])
    return rt


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
    # hub to hub (the List editor's "Virtual Speaker (Speaker)" entry in a conduit's Output)
    st = core.load_state()
    for ch in ("FL", "FR"):
        core.new_link(st, core.hub_custom("1"), ch, core.HUB_SPEAKER, ch)
        core.new_link(st, core.HUB_SPEAKER, ch, core.hub_custom("1"), ch)
    core.save_state(st)
    cycles(pw, 3)
    check(links_between(pw, base, ids["vspk"]) == [("FL", "FL"), ("FR", "FR")], "custom conduit hub -> Virtual Speaker (first link wins)")
    # ...but not both ways round: that's a feedback loop (a constant buzz at rate/quantum Hz)
    check(links_between(pw, ids["vspk"], base) == [], "the link back (Virtual Speaker -> conduit) is refused: feedback loop")
    plan = core.compute_plan(core.load_state(), core.Snapshot(pw.dump()))
    back = [l["id"] for l in core.load_state()["links"] if l["src"] == core.HUB_SPEAKER and l["dst"] == core.hub_custom("1")]
    check(back and set(back) <= plan.loops, "the plan names the refused links")
    # a loop through a helper (equaliser) is caught too
    st = core.load_state()
    st["links"] = [l for l in st["links"] if l["id"] not in back]
    core.save_state(st)
    cycles(pw, 2)
    plan = core.compute_plan(core.load_state(), core.Snapshot(pw.dump()))
    check(not plan.loops, "no loop once it's removed")


def test_per_port_and_blocks():
    use_tmp_config()
    pw, ids = standard_graph()
    pw.install(core, daemon)
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
    cycles(pw, 1)
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
    cycles(pw, 3, ev)
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


def test_gain_prunes_direct_path():
    """The doubling bug: WirePlumber's own stream -> default-sink link next to a gained link."""
    use_tmp_config()
    pw, ids = standard_graph()
    pw.install(core, daemon)
    st = core.default_state()
    chrome = core.new_cell(st, "Chromium", "speaker")
    for ch in ("FL", "FR"):
        core.new_link(st, chrome["id"], ch, core.HUB_SPEAKER, ch, volume=0.5)
    core.save_state(st)
    rt = cycles(pw, 3)
    check(links_between(pw, ids["chromium"], ids["vspk"]) == [], "gained link: WirePlumber's direct link is pruned (no doubling)")
    # WirePlumber rescans and puts the direct link back; a fast (event) cycle removes it again
    for ch in ("FL", "FR"):
        pw.link(pw.port(ids["chromium"], "output", ch), pw.port(ids["vspk"], "input", ch))
    n_cmds = len(pw.log)
    cycles(pw, 1, rt, slow=False)
    check(links_between(pw, ids["chromium"], ids["vspk"]) == [], "re-made direct link removed on the next event cycle")
    extra = [c for c in pw.log[n_cmds:] if c[0] == "wpctl"]
    check(not extra, f"event cycle issues no wpctl calls (no feedback loop) {extra[:2]}")
    n_cmds = len(pw.log)
    cycles(pw, 2, rt, slow=False)
    acts = [c for c in pw.log[n_cmds:] if c[0] != "pw-dump"]
    check(not acts, f"a settled graph produces no commands at all {acts[:2]}")


def test_equaliser():
    use_tmp_config()
    pw, ids = standard_graph()
    pw.install(core, daemon)
    st = core.default_state()
    ry = core.new_cell(st, "Ryzen HD Audio Controller Analog Stereo", "speaker", speaker=True)
    for ch in ("FL", "FR"):
        core.new_link(st, core.HUB_SPEAKER, ch, ry["id"], ch)
    st["speaker"]["eq"] = {"enabled": True, "preamp": -3.0,
                           "bands": [{"type": "peaking", "freq": 1000, "q": 1.0, "gain": 4.0, "enabled": True}]}
    core.save_state(st)
    rt = cycles(pw, 3)
    ein, eout = (pw.node_by_name(n) for n in conduit_eq.node_names("speaker"))
    check(ein is not None and eout is not None, "EQ helper started for the speaker hub")
    check(links_between(pw, ids["vspk"], ein) == [("FL", "FL"), ("FR", "FR")], "hub -> EQ in")
    check(links_between(pw, eout, ids["ryzen_sink"]) == [("FL", "FL"), ("FR", "FR")], "EQ out -> speaker")
    check(links_between(pw, ids["vspk"], ids["ryzen_sink"]) == [], "no un-equalised path next to it")
    check(pw.controls[ein].get("eq_b1:Gain") == 4.0 and pw.controls[ein].get("eq_pre:Gain") == -3.0, "config carries band + preamp")
    # live tweak: same structure -> set-param, no restart
    st = core.load_state()
    st["speaker"]["eq"]["bands"][0].update(freq=2500.0, gain=-6.0)
    core.save_state(st)
    cycles(pw, 2, rt)
    check(pw.node_by_name(conduit_eq.node_names("speaker")[0]) == ein, "tweaking a band doesn't restart the helper")
    check(pw.controls[ein].get("eq_b1:Freq") == 2500.0 and pw.controls[ein].get("eq_b1:Gain") == -6.0, "band change pushed live")
    # a long drag (values change every cycle, the graph hasn't reported them back yet) never
    # restarts the helper -- a restart drops the GUI's analyser and Listen
    pw.ignore_set_param = True
    for k in range(10):
        st = core.load_state()
        st["speaker"]["eq"]["bands"][0]["gain"] = -24.0 + 2.5 * k
        core.save_state(st)
        cycles(pw, 1, rt)
    check(pw.node_by_name(conduit_eq.node_names("speaker")[0]) == ein, "a long drag doesn't restart the helper")
    # ...but values that never show up while unchanged do, eventually
    cycles(pw, 8, rt)
    ein_r = pw.node_by_name(conduit_eq.node_names("speaker")[0])
    check(ein_r is not None and ein_r != ein, "a PipeWire that ignores live updates gets a restart with the values baked in")
    st = core.load_state()
    st["speaker"]["eq"]["bands"][0]["gain"] = 3.0
    core.save_state(st)
    cycles(pw, 10, rt)
    check(pw.node_by_name(conduit_eq.node_names("speaker")[0]) == ein_r, "...only once: later edits are pushed without restarts")
    pw.ignore_set_param = False
    rt.eq.blind.clear()
    cycles(pw, 2, rt)
    ein = pw.node_by_name(conduit_eq.node_names("speaker")[0])
    st = core.load_state()
    st["speaker"]["eq"]["bands"][0].update(freq=2500.0, gain=-6.0)
    core.save_state(st)
    cycles(pw, 2, rt)
    # structural change -> restart
    st = core.load_state()
    st["speaker"]["eq"]["bands"].append({"type": "highpass", "freq": 80, "q": 0.7, "gain": 0, "enabled": True})
    core.save_state(st)
    cycles(pw, 3, rt)
    ein2 = pw.node_by_name(conduit_eq.node_names("speaker")[0])
    check(ein2 is not None and ein2 != ein, "adding a band restarts the helper")
    check(links_between(pw, pw.node_by_name(conduit_eq.node_names("speaker")[1]), ids["ryzen_sink"]), "...and it is re-linked")
    # off -> back to the plain hub
    st = core.load_state()
    st["speaker"]["eq"]["enabled"] = False
    core.save_state(st)
    cycles(pw, 3, rt)
    check(pw.node_by_name(conduit_eq.node_names("speaker")[0]) is None, "EQ off stops the helper")
    check(links_between(pw, ids["vspk"], ids["ryzen_sink"]) == [("FL", "FL"), ("FR", "FR")], "...and the hub feeds the speaker directly again")
    # mic: EQ then noise suppression, default source follows
    st = core.load_state()
    st["mic"]["eq"] = {"enabled": True, "preamp": 0.0, "bands": []}
    st["mic"]["noise_suppression"] = "rnnoise"
    core.save_state(st)
    cycles(pw, 4, rt)
    min_, mout = (pw.node_by_name(n) for n in conduit_eq.node_names("mic"))
    nin, nout = (pw.node_by_name(n) for n in core.noise_processor_names("mic", "rnnoise"))
    check(links_between(pw, ids["vmic"], min_) and links_between(pw, mout, nin), "mic chain: hub -> EQ -> noise suppression")
    check(pw.defaults[-1] == nout, "default source is the end of the chain")
    # response maths sanity
    eq = {"preamp": 0, "bands": [{"type": "peaking", "freq": 1000, "q": 1, "gain": 6, "enabled": True}]}
    r = conduit_eq.response_db(eq, [1000, 20, 20000])
    check(abs(r[0] - 6) < 0.05 and abs(r[1]) < 0.2 and abs(r[2]) < 0.3, f"bell response peaks at its gain {r}")
    hp = conduit_eq.response_db({"preamp": 0, "bands": [{"type": "highpass", "freq": 100, "q": 0.707, "gain": 0, "enabled": True}]}, [100, 1000])
    check(abs(hp[0] + 3) < 0.2 and abs(hp[1]) < 0.1, f"high-pass is -3 dB at its corner {hp}")


def test_master_volume_and_cache():
    use_tmp_config()
    pw, ids = standard_graph()
    pw.install(core, daemon)
    st = core.default_state()
    st["speaker"]["volume"] = 2.0
    core.save_state(st)
    rt = cycles(pw, 2)
    check(pw.volumes.get(ids["vspk"]) == 2.0, "master volume applied")
    st["speaker"]["volume"] = 1.0
    core.save_state(st)
    cycles(pw, 2)          # a fresh runtime: remembered across a restart
    check(pw.volumes.get(ids["vspk"]) == 1.0, "going back to 1.0 resets the device (it used to stay boosted)")
    pw.volumes[ids["vspk"]] = 0.7     # the user's own wpctl change is then left alone
    cycles(pw, 2)
    check(pw.volumes.get(ids["vspk"]) == 0.7, "at 1.0 the device's own volume is left alone")
    # link cache is by name: a stale id-based cache is ignored
    core.LINK_CACHE_FILE.write_text(json.dumps([[ids["spotify"], ids["vspk"]]]))
    cycles(pw, 1)
    check(links_between(pw, ids["spotify"], ids["vspk"]) == [("FL", "FL"), ("FR", "FR")], "old id-based link cache can't prune anything")


def test_loop_through_other_apps_loopbacks():
    """Jeeves-style: an app's loopback captures the Conduit mic into its own sink, whose
    monitor comes back out as a source -- routing that source into the Conduit mic closes a
    loop that no single link shows (the loopback carries it internally)."""
    print("-- loops through other apps' loopback modules")
    pw, ids = standard_graph()
    def mk(name, cls, ins=(), outs=(), group=None):
        nid = pw.add_node(name, name, cls, ins=ins, outs=outs)
        if group:
            pw.nodes[nid]["props"]["node.link-group"] = group
        return nid
    def port(nid, d, ch):
        return next(p for p, v in pw.ports.items() if v["node"] == nid and v["dir"] == d and v["channel"] == ch)
    lb_in = mk("input.loopback-1-14", "Stream/Input/Audio", ins=("FL", "FR"), group="loopback-1-14")
    lb_out = mk("output.loopback-1-14", "Stream/Output/Audio", outs=("FL", "FR"), group="loopback-1-14")
    jsink = mk("jeeves-mic", "Audio/Sink", ins=("FL", "FR"), outs=("FL", "FR"))
    jin = mk("input.jeeves-mic-source", "Stream/Input/Audio", ins=("FL", "FR"), group="loopback-1-13")
    jsrc = mk("jeeves-mic-source", "Audio/Source", outs=("FL", "FR"), group="loopback-1-13")
    for ch in ("FL", "FR"):
        pw.link(port(ids["vmic"], "output", ch), port(lb_in, "input", ch))     # WirePlumber: default mic -> loopback
        pw.link(port(lb_out, "output", ch), port(jsink, "input", ch))
        pw.link(port(jsink, "output", ch), port(jin, "input", ch))
    st = core.default_state()
    cell = core.new_cell(st, "jeeves-mic-source", "mic")
    for ch in ("FL", "FR"):
        core.new_link(st, cell["id"], ch, core.HUB_MIC, ch)
    snap = core.Snapshot(pw.dump())
    plan = core.compute_plan(st, snap)
    check(len(plan.loops) == 2 and not any(snap.port_owner.get(i) == ids["vmic"] and snap.port_owner.get(o) == jsrc
                                           for o, i in plan.desired),
          "routing the app's source back into the Conduit mic is refused (loop through its loopbacks)")
    for nid in (lb_in, lb_out, jin, jsrc):
        pw.nodes[nid]["props"].pop("node.link-group", None)
    plan = core.compute_plan(st, core.Snapshot(pw.dump()))
    check(not plan.loops, "(without the modules' link groups there is no visible path -- the groups are what reveal it)")


def test_restart_helpers():
    print("-- restart EQ / NS helpers on request")
    use_tmp_config()
    pw, ids = standard_graph()
    pw.install(core, daemon)
    st = core.default_state()
    st["mic"]["eq"] = {"enabled": True, "preamp": 0.0,
                       "bands": [{"type": "peaking", "freq": 1000, "q": 1.0, "gain": 4.0, "enabled": True}]}
    core.save_state(st)
    rt = cycles(pw, 4)
    ein = pw.node_by_name(conduit_eq.node_names("mic")[0])
    check(ein is not None, "mic EQ running")
    st = core.load_state()
    st["daemon"]["restart_helpers"] = 1
    core.save_state(st)
    cycles(pw, 3, rt)
    ein2 = pw.node_by_name(conduit_eq.node_names("mic")[0])
    check(ein2 is not None and ein2 != ein, "bumping daemon.restart_helpers restarts the helpers")
    cycles(pw, 3, rt)
    check(pw.node_by_name(conduit_eq.node_names("mic")[0]) == ein2, "...once")


if __name__ == "__main__":
    test_restart_helpers()
    test_loop_through_other_apps_loopbacks()
    test_gain_prunes_direct_path()
    test_equaliser()
    test_master_volume_and_cache()
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
