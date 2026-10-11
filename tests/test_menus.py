"""Open every context menu and trigger every action (dialogs auto-cancelled); fail on any exception.
   QT_QPA_PLATFORM=offscreen python3 tests/test_menus.py"""
from __future__ import annotations
import copy, json, sys, tempfile, traceback
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE))
import conduit_core as core
from fake_pw import standard_graph, V1_README_STATE

d = Path(tempfile.mkdtemp())
core.CONFIG_DIR = d; core.STATE_FILE = d / "state.json"; core.LAYOUT_FILE = d / "layout.json"
core.LINK_CACHE_FILE = d / ".link_cache.json"; core.V1_BACKUP_FILE = d / "state.v1.backup.json"
core.restart_daemon = lambda: None
core.STATE_FILE.write_text(json.dumps(V1_README_STATE))

ERRORS = []
def hook(t, v, tb):
    ERRORS.append("".join(traceback.format_exception(t, v, tb)))
sys.excepthook = hook

from PySide6.QtCore import QPoint, QPointF
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu, QDialog
import qt_ui_kit as kit
app = QApplication(sys.argv); kit.install(app)
import conduit_dialogs as dlg, conduit_graph as cg
# answer every dialog: values for volume/colour, accept pickers with nothing chosen
kit.ask_text = lambda *a, **k: "1.5"
kit.get_color = lambda *a, **k: __import__("PySide6.QtGui", fromlist=["QColor"]).QColor("#ff00ff")
kit.ask_confirm = lambda *a, **k: True
QDialog.exec = lambda self: 0
seen = []
def fake_exec(self, *a, **k):
    acts = []
    def walk(m):
        for act in m.actions():
            if act.menu():
                walk(act.menu())
            elif act.isEnabled() and not act.isSeparator():
                acts.append(act)
    walk(self)
    seen.append([a.text() for a in acts])
    # trigger on copies of the state so actions don't consume each other: snapshot / restore
    for act in acts:
        before = copy.deepcopy(c.state)
        act.trigger()
        QTest.qWait(5)
        c.state = before
    return None
class _TestMenu(QMenu):
    def exec(self, *a, **k):
        return fake_exec(self)
def _themed_menu(title="", parent=None, stay_open=False):
    m = _TestMenu(title, parent); m.setStyleSheet(kit.menu_stylesheet()); return m
kit.themed_menu = _themed_menu

from conduit_layout import Layout
from conduit_controller import Controller
from conduit_gui import ConduitWindow
layout = Layout(); layout.set_ui("auto_refresh", False); layout.set_ui("show_unmanaged", True)
c = Controller(layout)
pw, ids = standard_graph()
c.set_snapshot(core.Snapshot(pw.dump()))
w = ConduitWindow(c); w.resize(1400, 900); w.show(); QTest.qWait(300)
g = w.graph
gpos = QPoint(100, 100)
for box, bw in list(g.box_widgets.items()):
    for cid, it in list(bw.view.cells.items()):
        g.cell_menu(it, gpos); QTest.qWait(20)
    for key in list(bw.view.links):
        if not key.startswith("int:"):
            g.link_menu(key, gpos); QTest.qWait(20)
    g.background_menu(bw.view, QPointF(0, 0), gpos); QTest.qWait(20)
for key, *_ in list(g._cross):
    g.link_menu(key, gpos)
# header NS menus
for bw in g.box_widgets.values():
    if bw.header.ns.isEnabled() and bw.header.ns.isVisible():
        bw.header._ns_menu()
QTest.qWait(200)
# events dialog / new cell / new speaker paths
cid = c.state["cells"][0]["id"]
g.open_events(cid); g.new_cell("speaker", QPointF(0, 0)); g.new_speaker(QPointF(0, 0))
QTest.qWait(100)
print(f"menus opened: {len(seen)}, actions fired: {sum(len(s) for s in seen)}")
if ERRORS:
    print("\n".join(ERRORS[:5])); print(f"{len(ERRORS)} ERRORS"); sys.exit(1)
print("ALL PASS")
