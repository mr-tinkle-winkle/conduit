"""Render every theme offscreen against the fake PipeWire graph.
   QT_QPA_PLATFORM=offscreen python3 tests/render_screens.py OUTDIR [theme ...]
Per theme: <key>.png (graph, with a link mid-intro, a blocked and a closed cell),
<key>_list.png, <key>_eq.png; plus desk_console.png for the Mixing Desk.  "metro:dark" renders
the dark version of a light theme (as metro_dark*.png); with no theme list every theme and dark version."""
from __future__ import annotations
import json, math, sys, tempfile, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE))
import conduit_core as core
from fake_pw import standard_graph, V1_README_STATE


def fresh_config():
    d = Path(tempfile.mkdtemp())
    core.CONFIG_DIR = d; core.STATE_FILE = d / "state.json"; core.LAYOUT_FILE = d / "layout.json"
    core.LINK_CACHE_FILE = d / ".link_cache.json"; core.V1_BACKUP_FILE = d / "state.v1.backup.json"
    core.STATE_FILE.write_text(json.dumps(V1_README_STATE))
    core.notify_daemon = lambda: None


def build(c):
    pw, ids = standard_graph()
    pw.add_node("conduit_custom_1", "Game Audio Mix", "Audio/Sink", ins=("FL", "FR"), outs=("FL", "FR"))
    pw.add_node("conduit_custom_1_mic", "Game Audio Mix (Mic)", "Audio/Source/Virtual", ins=("FL", "FR"), outs=("FL", "FR"))
    for k, m in (("mic", "rnnoise"), ("custom.1", "webrtc")):
        i, o = core.noise_processor_names(k, m)
        pw.add_node(i, "ns", "Audio/Sink", ins=("FL", "FR")); pw.add_node(o, "ns", "Audio/Source", outs=("FL", "FR"))
    pw.add_node("Discord", "Discord", "Stream/Output/Audio", app="Discord", outs=("FL", "FR"), client=77)
    pw.add_node("headphones", "Headphones", "Audio/Sink", ins=("FL", "FR"), outs=("FL", "FR"), state="idle")
    pw.remove_node(ids["vencord"])                       # -> a closed cell
    st = c.state
    for l in st["links"]:
        l["volume"] = 1.0
    chrome = next(x for x in st["cells"] if x["label"] == "Chromium")
    chrome["volume"] = 1.5
    hp = core.new_cell(st, "Headphones", "speaker", speaker=True, blocked=True)
    obs = core.new_cell(st, "OBS", "mic")
    obs["events"] = [{"kind": "active", "outcomes": {chrome["id"]: "block"}}]
    for ch in ("FL", "FR"):
        core.new_link(st, core.HUB_SPEAKER, ch, hp["id"], ch)
        core.new_link(st, core.NS_MIC, ch, obs["id"], ch)
        core.new_link(st, core.hub_custom(1), ch, core.HUB_SPEAKER, ch, volume=0.8 if ch == "FL" else 1.0)
    ci = core.new_cell(st, "Chromium input", "custom.1")
    for ch in ("FL", "FR"):
        core.new_link(st, core.hub_custom_mic(1), ch, ci["id"], ch)
    st["speaker"]["eq"] = {"enabled": True, "preamp": -3.0, "bands": [
        {"type": "highpass", "freq": 45, "q": 0.7, "gain": 0, "enabled": True},
        {"type": "lowshelf", "freq": 120, "q": 0.7, "gain": 5.0, "enabled": True},
        {"type": "peaking", "freq": 900, "q": 1.4, "gain": -4.0, "enabled": True},
        {"type": "peaking", "freq": 3200, "q": 1.0, "gain": 3.5, "enabled": True},
        {"type": "highshelf", "freq": 9000, "q": 0.7, "gain": 2.5, "enabled": True}]}
    c.commit()
    snap = core.Snapshot(pw.dump())
    plan = core.compute_plan(c.state, snap)
    for pairs in plan.link_paths.values():
        for o, i in pairs:
            pw.link(o, i)
    return pw


def main(out, only):
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    import qt_ui_kit as kit
    import conduit_skins as skins
    app = QApplication(sys.argv)
    kit.install(app)
    from conduit_layout import Layout
    from conduit_controller import Controller
    from conduit_gui import ConduitWindow
    import conduit_eqview
    keys = only or (list(skins.ORDER) + [k + ":dark" for k in skins.ORDER if skins.SKINS[k].light])
    for tag in keys:
        key, _, variant = tag.partition(":")
        fresh_config()
        skins.activate(key, app, dark=variant == "dark")
        key = key + ("_dark" if variant == "dark" else "")
        layout = Layout(); layout.set_ui("auto_refresh", False); layout.set_ui("mode", "graphical"); layout.set_ui("desk_view", "patch")
        c = Controller(layout)
        pw = build(c)
        c.set_snapshot(core.Snapshot(pw.dump()))
        w = ConduitWindow(c); w.resize(1500, 950); w.show()
        QTest.qWait(250)
        g = w.graph
        g.sync()
        for bw in g.box_widgets.values():
            bw.view.fit()
        QTest.qWait(1300)
        # stage the one-shot effects mid-flight: a new Discord link, a fresh block
        st = c.state
        d = core.new_cell(st, "Discord", "speaker")
        layout.set_pos(d["id"], -340, 150)
        made = c.link_cells(d["id"], ["*", "FL", "FR"], core.HUB_SPEAKER, ["*", "FL", "FR"], "*", "*")
        c.commit()
        snap = core.Snapshot(pw.dump()); plan = core.compute_plan(c.state, snap)
        for l in made:
            for o, i in plan.link_paths.get(l["id"], []):
                pw.link(o, i)
        c.set_snapshot(core.Snapshot(pw.dump()))
        now = time.monotonic()
        for l in made:
            g._fx[("link", l["id"])] = now - 0.42
        g._fx[("block", next(x["id"] for x in st["cells"] if x["label"] == "Headphones"))] = now - 5
        g._fx = {k: v for k, v in g._fx.items() if k[0] != "boot"}
        for bw in g.box_widgets.values():
            bw.view.fit()
        g._tick(); QTest.qWait(60)
        w.grab().save(str(out / f"{key}.png"))
        if w.desk is not None:
            w.mode_group.button(0).click(); QTest.qWait(500)
            w.grab().save(str(out / "desk_console.png"))
        w.btn_list.click(); QTest.qWait(450)
        w.grab().save(str(out / f"{key}_list.png"))
        dlg = conduit_eqview.EqDialog(c, "speaker", w, live=False)
        dlg.show(); QTest.qWait(150)
        dlg.graph.select(3)
        # a plausible spectrum: pink-ish noise plus two tones, fed straight into the analyser
        import random
        random.seed(4)
        n = conduit_eqview.FFT_N
        acc = 0.0
        samples = []
        for i in range(n * 2):
            acc = 0.97 * acc + 0.03 * random.uniform(-1, 1)
            samples.append(acc * 2.2 + 0.05 * math.sin(2 * math.pi * 220 * i / 48000) + 0.02 * math.sin(2 * math.pi * 3100 * i / 48000)
                           + 0.01 * random.uniform(-1, 1))
        # two frames (a quiet one, then the loud one) so time smoothing has something to hold back
        dlg.tap.feed([v * 0.08 for v in samples]); dlg.tap._analyse(); dlg._frame()
        dlg.tap.feed(samples); dlg.tap._analyse(); dlg._frame()
        QTest.qWait(80)
        dlg.grab().save(str(out / f"{key}_eq.png"))
        dlg.close()
        w.c._auto_timer.stop(); w._rebuilding = True; w.close(); w.deleteLater(); QTest.qWait(60)
        print("rendered", key, flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/conduit_shots", sys.argv[2:])
