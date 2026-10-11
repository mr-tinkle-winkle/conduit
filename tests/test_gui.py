"""Drive the graph / list editors with real mouse events, offscreen.
   QT_QPA_PLATFORM=offscreen python3 tests/test_gui.py"""
from __future__ import annotations
import json, sys, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE))
import conduit_core as core
from fake_pw import standard_graph

FAILS = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: FAILS.append(msg)

d = Path(tempfile.mkdtemp())
core.CONFIG_DIR = d; core.STATE_FILE = d / "state.json"; core.LAYOUT_FILE = d / "layout.json"
core.LINK_CACHE_FILE = d / ".link_cache.json"; core.V1_BACKUP_FILE = d / "state.v1.backup.json"
RESTARTS = []
core.restart_daemon = lambda: RESTARTS.append(1)

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import qt_ui_kit as kit
app = QApplication(sys.argv); kit.install(app)
from conduit_layout import Layout
from conduit_controller import Controller
from conduit_gui import ConduitWindow
import conduit_graph as cg

st = core.default_state()
core.add_conduit(st, "Game Mix")
spotify = core.new_cell(st, "spotify", "speaker")
ryzen = core.new_cell(st, "Ryzen HD Audio Controller Analog Stereo", "speaker", speaker=True)
discord = core.new_cell(st, "Chromium input", "mic")
core.save_state(st)

layout = Layout()
layout.set_ui("auto_refresh", False)
c = Controller(layout)
pw, ids = standard_graph()
c.set_snapshot(core.Snapshot(pw.dump()))
w = ConduitWindow(c); w.resize(1500, 950); w.show()
QTest.qWait(400)
g = w.graph

def view(box): return g.box_widgets[box].view
def item(box, cid): return view(box).cells[cid]
def port_global(box, cid, side, ch):
    it = item(box, cid); v = view(box)
    r = it.port_rect(side, ch); sp = it.mapToScene(r.center())
    return v.viewport().mapToGlobal(v.mapFromScene(sp))
def send(widget, etype, gpos, button, buttons, mods=Qt.NoModifier):
    local = widget.mapFromGlobal(gpos)
    ev = QMouseEvent(etype, QPointF(local), QPointF(gpos), button, buttons, mods)
    QApplication.sendEvent(widget, ev)
def drag(src_box, a_gpos, b_gpos, button=Qt.LeftButton, mods=Qt.NoModifier):
    vp = view(src_box).viewport()
    send(vp, QEvent.MouseButtonPress, a_gpos, button, button, mods)
    for t in (0.25, 0.5, 0.75, 1.0):
        p = a_gpos + (b_gpos - a_gpos) * t
        send(vp, QEvent.MouseMove, QPoint(int(p.x()), int(p.y())), Qt.NoButton, button, mods)
    send(vp, QEvent.MouseButtonRelease, b_gpos, button, Qt.NoButton, mods)
    QTest.qWait(50)
def click(box, gpos, button=Qt.LeftButton, mods=Qt.NoModifier):
    vp = view(box).viewport()
    send(vp, QEvent.MouseButtonPress, gpos, button, button, mods)
    send(vp, QEvent.MouseButtonRelease, gpos, button, Qt.NoButton, mods)
    QTest.qWait(30)

for box in ("speaker", "mic", "custom.1"):
    view(box).fit()
QTest.qWait(100)

# 1. Both -> Both drag: spotify -> virtual speaker makes L and R links
drag("speaker", port_global("speaker", spotify["id"], "out", "*"), port_global("speaker", core.HUB_SPEAKER, "in", "*"))
links = [l for l in c.state["links"] if l["src"] == spotify["id"] and l["dst"] == core.HUB_SPEAKER]
check(sorted((l["src_port"], l["dst_port"]) for l in links) == [("FL", "FL"), ("FR", "FR")], "Both drag makes L+R links")
check(json.loads(core.STATE_FILE.read_text())["links"], "auto-apply wrote state.json")
QTest.qWait(700)
check(RESTARTS, "auto-apply restarted the daemon (debounced)")

# 2. single port: spotify L -> ryzen R (bypass) -> anti-link created
drag("speaker", port_global("speaker", spotify["id"], "out", "FL"), port_global("speaker", ryzen["id"], "in", "FR"))
check(any(l["src"] == spotify["id"] and l["dst"] == ryzen["id"] and l["src_port"] == "FL" and l["dst_port"] == "FR"
          for l in c.state["links"]), "single-port link L -> R")
anti = [l for l in c.state["links"] if l.get("anti")]
check(len(anti) == 1 and anti[0]["dst"] == core.HUB_SPEAKER and anti[0]["blocked"], "bypass made a default anti-link")

# 3. cross-box drag: virtual mic out (mic box) -> custom conduit hub in (custom.1)
drag("mic", port_global("mic", core.HUB_MIC, "out", "*"), port_global("custom.1", core.hub_custom(1), "in", "*"))
x = [l for l in c.state["links"] if l["src"] == core.HUB_MIC and l["dst"] == core.hub_custom(1)]
check(len(x) == 2, "cross-box link by dragging between boxes")
QTest.qWait(50)
check(any(k for k, *_ in g._cross if k == x[0]["id"]), "cross-box link drawn by the overlay")

# 4. middle click a cell -> blocked; again -> unblocked
it = item("speaker", spotify["id"]); v = view("speaker")
center = v.viewport().mapToGlobal(v.mapFromScene(it.mapToScene(QPointF(cg.CELL_W / 2, 8))))
click("speaker", center, Qt.MiddleButton)
check(core.cell_by_id(c.state, spotify["id"])["blocked"], "middle click blocks a cell")
check(c.cell_status(spotify["id"]) == "blocked", "blocked state wins")
click("speaker", center, Qt.MiddleButton)
check(not core.cell_by_id(c.state, spotify["id"])["blocked"], "middle click again unblocks")

# 5. click a link to select, middle-click to block, Delete removes
lk = next(l for l in c.state["links"] if l["src"] == spotify["id"] and l["dst"] == core.HUB_SPEAKER)
li = view("speaker").links[lk["id"]]
mid = li.path.pointAtPercent(0.5)
gmid = v.viewport().mapToGlobal(v.mapFromScene(mid))
click("speaker", gmid)
check(g.selected == {lk["id"]}, "click selects a link")
click("speaker", gmid, Qt.MiddleButton)
check(core.link_by_id(c.state, lk["id"])["blocked"], "middle click blocks a link")
g.selected = {lk["id"]}
g.delete_selected()
check(core.link_by_id(c.state, lk["id"]) is None, "Delete removes the selected link")

# 6. rubber band selects links
r0 = v.viewport().mapToGlobal(QPoint(2, 2)); r1 = v.viewport().mapToGlobal(QPoint(v.viewport().width() - 3, v.viewport().height() - 3))
drag("speaker", r0, r1)
check(len(g.selected) >= 2, f"rectangle drag selects several links ({len(g.selected)})")

# 7. drag a cell into another box
it = item("speaker", spotify["id"])
start = v.viewport().mapToGlobal(v.mapFromScene(it.mapToScene(QPointF(cg.CELL_W / 2, 10))))
mv = view("mic"); target = mv.viewport().mapToGlobal(mv.viewport().rect().center())
drag("speaker", start, target)
QTest.qWait(50)
check(core.cell_by_id(c.state, spotify["id"])["box"] == "mic", "dragging a cell onto another box moves it there")
check(spotify["id"] in view("mic").cells, "...and it's drawn there")

# 8. positions persist in layout.json, not state.json
layout.save_now()
lay = json.loads(core.LAYOUT_FILE.read_text())
check(spotify["id"] in lay["positions"], "cell position saved in layout.json")
check("positions" not in json.loads(core.STATE_FILE.read_text()), "state.json has no positions")

# 9. zoom per box
before = view("speaker").zoom, view("mic").zoom
view("speaker").set_zoom(view("speaker").zoom * 1.5)
check(view("mic").zoom == before[1] and view("speaker").zoom != before[0], "zoom is per box")

# 10. fullscreen
g.toggle_fullscreen("custom.1"); QTest.qWait(50)
check(not g.box_widgets["speaker"].isVisible() and g.box_widgets["custom.1"].isVisible(), "fullscreen hides other boxes")
g.escape(); QTest.qWait(50)
check(g.box_widgets["speaker"].isVisible(), "Esc restores")

# 11. hub disable dims box
g._toggle_box_enabled("speaker"); QTest.qWait(50)
check(not c.state["speaker"]["enabled"], "hub right-click disable")
g._toggle_box_enabled("speaker"); QTest.qWait(50)

# 12. outcome mode
cid = discord["id"]
g.start_outcome(cid, [{"kind": "active", "outcomes": {}}], 0)
target_cell = spotify["id"]   # now in mic box
tit = item("mic", target_cell); mvv = view("mic")
pt = mvv.viewport().mapToGlobal(mvv.mapFromScene(tit.mapToScene(QPointF(cg.CELL_W / 2, 8))))
click("mic", pt, Qt.MiddleButton)
check(g.outcome["outcomes"].get(target_cell) == "block", "outcome: middle click once = block")
click("mic", pt, Qt.MiddleButton)
check(g.outcome["outcomes"].get(target_cell) == "unblock", "outcome: twice = unblock")
click("mic", pt, Qt.MiddleButton); click("mic", pt, Qt.MiddleButton)
import conduit_dialogs
orig = conduit_dialogs.EventDialog.exec
conduit_dialogs.EventDialog.exec = lambda self: 1
g.finish_outcome(True); QTest.qWait(80)
conduit_dialogs.EventDialog.exec = orig
ev = core.cell_by_id(c.state, cid)["events"]
check(ev and ev[0]["outcomes"] == {target_cell: "block"}, "outcome saved onto the event")

# 13. manual save mode
w.auto_apply.setChecked(False)
n_before = len(RESTARTS)
core.cell_by_id(c.state, cid)["volume"] = 2.0; c.commit()
check(c.dirty and w.save_btn.isVisible() and w.save_btn.isEnabled(), "auto-apply off: edit is pending, Save enabled")
check(json.loads(core.STATE_FILE.read_text()) != c.state, "...not written yet")
w.save_btn.click(); QTest.qWait(120)
check(not c.dirty and core.cell_by_id(core.load_state(), cid)["volume"] == 2.0, "Save writes it")
w.auto_apply.setChecked(True)

# 14. external change (daemon event) is picked up
raw = json.loads(core.STATE_FILE.read_text())
for cc in raw["cells"]:
    if cc["id"] == ryzen["id"]: cc["blocked"] = True
core.atomic_write_json(core.STATE_FILE, raw)
for _ in range(20):
    QTest.qWait(50)
    if core.cell_by_id(c.state, ryzen["id"])["blocked"]: break
check(core.cell_by_id(c.state, ryzen["id"])["blocked"], "daemon's event block shows up in the GUI")

# 15. list editor: add, toggle, remove; preserves graph-only data
w.mode_group.button(1).click(); QTest.qWait(300)
le = w.list
sec = le.panels["speaker"].inputs
idx = next(i for i in range(sec.combo.count()) if sec.combo.itemData(i) == "Chromium")
sec._add(idx); QTest.qWait(50)
chrome = next(cc for cc in c.state["cells"] if cc["label"] == "Chromium" and cc["box"] == "speaker")
cl = [l for l in c.state["links"] if l["src"] == chrome["id"] and l["dst"] == core.HUB_SPEAKER]
check(len(cl) == 2, "list Add makes per-port links to the hub")
row = sec.rows[chrome["id"]]
row.enabled.setChecked(False)
check(all(core.link_by_id(c.state, l["id"])["blocked"] for l in cl), "list checkbox blocks the links")
n_links = len(c.state["links"])
ev_cell = core.cell_by_id(c.state, cid)
row._remove(); QTest.qWait(50)
check(len(c.state["links"]) == n_links - 2 and core.cell_by_id(c.state, chrome["id"]) is None, "list ✕ removes its links (and the unused cell)")
check(core.cell_by_id(c.state, cid)["events"], "events untouched by list edits")
check(any(l.get("anti") for l in c.state["links"]), "bypass anti-link untouched by list edits")

# 16. unmanaged toggle produces ghosts
w.mode_group.button(0).click(); QTest.qWait(200)
w.unmanaged.setChecked(True); QTest.qWait(100)
check(len(g.ghost_links) > 0, f"unmanaged links shown ({len(g.ghost_links)})")
gk = next(iter(g.ghost_links)); gl = g.ghost_links[gk]
n = len(c.state["links"])
g._adopt_ghost_link(gl, blocked=False); QTest.qWait(50)
check(len(c.state["links"]) == n + 1, "unmanaged link adopted into Conduit")

# 17. no unscoped stylesheets (kit lesson 7.1)
bad = []
for wdg in w.findChildren(__import__("PySide6.QtWidgets", fromlist=["QWidget"]).QWidget):
    ss = wdg.styleSheet().strip()
    if ss and "{" not in ss:
        bad.append((type(wdg).__name__, ss[:40]))
check(not bad, f"no unscoped stylesheets {bad[:3]}")

print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"))
w.c._auto_timer.stop()
sys.exit(1 if FAILS else 0)
