"""Frame cost while dragging a cell around, per theme (offscreen raster, so relative numbers).
   QT_QPA_PLATFORM=offscreen python3 tests/bench_drag.py [theme ...]"""
from __future__ import annotations
import json, math, sys, tempfile, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE))
import conduit_core as core
from fake_pw import standard_graph, V1_README_STATE

d = Path(tempfile.mkdtemp())
core.CONFIG_DIR = d; core.STATE_FILE = d / "state.json"; core.LAYOUT_FILE = d / "layout.json"
core.LINK_CACHE_FILE = d / ".link_cache.json"; core.V1_BACKUP_FILE = d / "state.v1.backup.json"
core.notify_daemon = lambda: None
core.STATE_FILE.write_text(json.dumps(V1_README_STATE))

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication
import qt_ui_kit as kit
app = QApplication(sys.argv); kit.install(app)
import conduit_skins as skins
from conduit_layout import Layout
from conduit_controller import Controller
import conduit_gui
from conduit_gui import ConduitWindow

layout = Layout(); layout.set_ui("auto_refresh", False)
c = Controller(layout)
pw, ids = standard_graph()
c.set_snapshot(core.Snapshot(pw.dump()))
skins.activate("studio", app)
w = ConduitWindow(c); w.resize(1500, 950); w.show()
for _ in range(10): app.processEvents()
conduit_gui._windows[:] = [w]
N = 80
for key in sys.argv[1:] or skins.ORDER:
    w = w.set_theme(key) or w
    for _ in range(10): app.processEvents()
    g = w.graph
    view = g.box_widgets["speaker"].view
    it = next(x for x in view.cells.values() if not x.hub and not x.ghost)
    base = QPointF(it.pos())
    g._anim.stop()
    g.interacting = True
    for _ in range(5): app.processEvents()
    t = time.perf_counter()
    for i in range(N):
        it.setPos(base + QPointF(60 * math.sin(i / 7), 40 * math.cos(i / 9)))
        app.processEvents()                           # paint what the move invalidated, like a real drag
    dt = (time.perf_counter() - t) / N
    g.interacting = False
    getattr(g, "end_cell_drag", lambda: None)()
    it.setPos(base)
    print(f"{key:10s} {dt * 1000:6.1f} ms/frame  ({1 / dt:5.0f} fps)")
