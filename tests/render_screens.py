"""Render the window offscreen against the fake PipeWire graph.
   QT_QPA_PLATFORM=offscreen python3 tests/render_screens.py OUTDIR"""
from __future__ import annotations
import copy, sys, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE))
import conduit_core as core
from fake_pw import standard_graph, V1_README_STATE

def setup_config():
    d = Path(tempfile.mkdtemp())
    core.CONFIG_DIR = d; core.STATE_FILE = d / "state.json"; core.LAYOUT_FILE = d / "layout.json"
    core.LINK_CACHE_FILE = d / ".link_cache.json"; core.V1_BACKUP_FILE = d / "state.v1.backup.json"
    core.STATE_FILE.write_text(__import__("json").dumps(V1_README_STATE))
    core.restart_daemon = lambda: None
    return d

def main(out):
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    setup_config()
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer
    import qt_ui_kit as kit
    app = QApplication(sys.argv)
    kit.install(app)
    from conduit_layout import Layout
    from conduit_controller import Controller
    from conduit_gui import ConduitWindow
    pw, ids = standard_graph()
    # the daemon would have created these
    pw.add_node("conduit_custom_1", "Game Audio Mix", "Audio/Sink", ins=("FL","FR"), outs=("FL","FR"))
    pw.add_node("conduit_custom_1_mic", "Game Audio Mix (Mic)", "Audio/Source/Virtual", ins=("FL","FR"), outs=("FL","FR"))
    for k, m in (("mic", "rnnoise"), ("custom.1", "webrtc")):
        i, o = core.noise_processor_names(k, m)
        pw.add_node(i, "ns", "Audio/Sink", ins=("FL","FR")); pw.add_node(o, "ns", "Audio/Source", outs=("FL","FR"))
    # a few links live
    vs, ry = ids["vspk"], ids["ryzen_sink"]
    for ch in ("FL","FR"):
        pw.link(pw.port(vs,"output",ch), pw.port(ry,"input",ch))
        pw.link(pw.port(ids["yeti"],"output","MONO"), pw.port(ids["vmic"],"input",ch))
    layout = Layout()
    c = Controller(layout)
    # some graph-only features: a colour, a cell volume, an event, a blocked cell, a direct link
    st = c.state
    chrome = next(x for x in st["cells"] if x["label"] == "Chromium")
    chrome["volume"] = 1.5
    obs = core.new_cell(st, "OBS", "mic")
    obs["events"] = [{"kind": "active", "outcomes": {chrome["id"]: "block"}}]
    core.new_link(st, core.NS_MIC, "FL", obs["id"], "FL"); core.new_link(st, core.NS_MIC, "FR", obs["id"], "FR")
    core.new_link(st, core.hub_custom(1), "FL", core.HUB_SPEAKER, "FL", color="#ec4899")
    core.new_link(st, core.hub_custom(1), "FR", core.HUB_SPEAKER, "FR", color="#ec4899")
    ci = core.new_cell(st, "Chromium input", "custom.1")
    core.new_link(st, core.hub_custom_mic(1), "FL", ci["id"], "FL")
    core.new_link(st, core.hub_custom_mic(1), "FR", ci["id"], "FR")
    c.commit()
    from conduit_core import Snapshot
    c.set_snapshot(Snapshot(pw.dump()))
    layout.set_ui("show_unmanaged", True)
    w = ConduitWindow(c)
    w.resize(1500, 950)
    w.show()
    shots = []
    def step0():
        w.graph.sync()
        for bw in w.graph.box_widgets.values(): bw.view.fit()
    def step1():
        w.grab().save(str(out / "graph.png")); shots.append("graph")
        w.graph.selected = {st["links"][0]["id"]} if st["links"] else set()
        w.graph.toggle_fullscreen("mic")
    def step2():
        w.grab().save(str(out / "graph_fullscreen_mic.png")); shots.append("fs")
        w.graph.toggle_fullscreen("mic")
        w.mode_group.button(1).click()
    def step3():
        w.grab().save(str(out / "list.png")); shots.append("list")
        w.auto_apply.setChecked(False)
    def step4():
        w.grab().save(str(out / "list_manual.png"))
        app.quit()
    for i, f in enumerate((step0, step1, step2, step3, step4)):
        QTimer.singleShot(300 + i * 500, f)
    app.exec()
    print("rendered", shots)

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/conduit_shots")
