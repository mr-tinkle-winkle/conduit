"""Every theme, the equaliser window and the Mixing Desk, driven offscreen.
   QT_QPA_PLATFORM=offscreen python3 tests/test_themes.py"""
from __future__ import annotations
import json, sys, tempfile, time, traceback
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE))
import conduit_core as core
from fake_pw import standard_graph, V1_README_STATE

FAILS, ERRORS = [], []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: FAILS.append(msg)
sys.excepthook = lambda t, v, tb: ERRORS.append("".join(traceback.format_exception(t, v, tb)))

d = Path(tempfile.mkdtemp())
core.CONFIG_DIR = d; core.STATE_FILE = d / "state.json"; core.LAYOUT_FILE = d / "layout.json"
core.LINK_CACHE_FILE = d / ".link_cache.json"; core.V1_BACKUP_FILE = d / "state.v1.backup.json"
core.notify_daemon = lambda: None
core.STATE_FILE.write_text(json.dumps(V1_README_STATE))

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QWheelEvent, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import qt_ui_kit as kit
app = QApplication(sys.argv); kit.install(app)
import conduit_skins as skins
from conduit_layout import Layout
from conduit_controller import Controller
import conduit_gui
from conduit_gui import ConduitWindow
import conduit_eqview, conduit_eq

layout = Layout(); layout.set_ui("auto_refresh", False)
c = Controller(layout)
pw, ids = standard_graph()
c.set_snapshot(core.Snapshot(pw.dump()))
skins.activate("studio", app)
fams = set(QFontDatabase.families())
need = ["Oswald", "Share Tech Mono", "VT323", "Overpass", "IBM Plex Mono", "JetBrains Mono", "Manrope", "Archivo",
        "Archivo Black", "Barlow Condensed", "Caveat", "Press Start 2P", "Barlow", "Space Mono", "Patrick Hand"]
check(all(f in fams for f in need), f"bundled fonts registered {[f for f in need if f not in fams]}")

w = ConduitWindow(c); w.resize(1400, 900); w.show(); QTest.qWait(200)
conduit_gui._windows[:] = [w]
for key in skins.ORDER[1:] + ["studio"]:
    before = json.dumps(c.state, sort_keys=True)
    w = w.set_theme(key); QTest.qWait(120)
    g = w.graph
    sp = next(x for x in c.state["cells"] if x["label"] == "spotify")
    # run every one-shot effect and a few seconds of animation frames
    now = time.monotonic()
    for l in c.state["links"]:
        g._fx[("link", l["id"])] = now - 0.3
    for cell in c.state["cells"]:
        g._fx[("block", cell["id"])] = now - 0.2
        g._fx[("closed", cell["id"])] = now - 0.2
    sp["blocked"] = True; c.commit()
    for i in range(12):
        g._tick(); QTest.qWait(15)
    img = w.grab()
    sp = next(x for x in c.state["cells"] if x["label"] == "spotify"); sp["blocked"] = False; c.commit()
    g.selected = {c.state["links"][0]["id"]}; g._repaint(); QTest.qWait(30)
    g.selected = set()
    ok = skins.current().key == key and not img.isNull() and layout.ui("theme") == key
    check(ok and json.dumps(c.state, sort_keys=True) == before, f"theme {key}: switches, animates and paints; routing untouched")
    if w.desk is not None:
        check(w.modes == ["desk", "graphical", "list"], "desk theme has Desk | Patch | List")
# dark versions of the light themes
for key in [k for k in skins.ORDER if skins.SKINS[k].light]:
    w = w.set_theme(key, dark=True); QTest.qWait(80)
    for i in range(6):
        w.graph._tick(); QTest.qWait(10)
    sk = skins.current()
    bg = w.palette().window().color().lightness()
    check(sk.key == key and sk.dark and layout.ui("dark_mode") and bg < 80 and "(dark)" in w.theme_btn.text(),
          f"theme {key}: dark version (window lightness {bg})")
    w = w.set_theme(key, dark=False); QTest.qWait(60)
    check(not skins.current().dark and w.palette().window().color().lightness() > 180, f"theme {key}: back to light")
w = w.set_theme("rack", dark=True); QTest.qWait(60)
check(skins.current().key == "rack" and not skins.current().dark, "dark mode is ignored by themes that are already dark")
w = w.set_theme("studio", dark=False); QTest.qWait(60)
from PySide6.QtWidgets import QGraphicsItem
# links that follow a dragged cell paint live, then go back to cached
from PySide6.QtCore import QPointF as _P
g = w.graph
linked = lambda x, l: getattr(l, "ends", None) is not None and (l.ends[0] is x or l.ends[2] is x)
sv, mover = next((bw.view, x) for bw in g.box_widgets.values() for x in bw.view.cells.values()
                 if any(linked(x, l) for l in bw.view.links.values()))
g.interacting = True
mover.setPos(mover.pos() + _P(5, 5)); QTest.qWait(20)
follow = [l for l in sv.links.values() if linked(mover, l)]
check(follow and all(l.cacheMode() == QGraphicsItem.NoCache for l in follow), "links following a dragged cell paint live")
g.interacting = False; g.end_cell_drag()
mover.setPos(mover.pos() - _P(5, 5)); QTest.qWait(20)
check(all(l.cacheMode() == QGraphicsItem.DeviceCoordinateCache or l.boundingRect().width() * l.boundingRect().height() > l.CACHE_MAX_AREA
          for l in follow), "and are cached again after the drag")

# every bundled weight is a real face (variable fonts only exposed one weight to Qt)
from PySide6.QtGui import QFontInfo
from conduit_skin_base import F
for fam, wts in (("Manrope", (400, 600, 800)), ("Archivo", (400, 700, 800)), ("Overpass", (400, 800, 900)),
                 ("Oswald", (400, 600)), ("Caveat", (400, 700)), ("Barlow", (400, 600))):
    got = [QFontInfo(F(fam, 12, weight=x)).weight() for x in wts]
    check(got == list(wts), f"{fam} has real weights {got}")
check(not ERRORS, f"no exceptions while painting themes {ERRORS[:1]}")

# ---- equaliser window
dlg = conduit_eqview.EqDialog(c, "speaker", w, live=False); dlg.show(); QTest.qWait(100)
gr = dlg.graph
def mouse(widget, etype, pos, button=Qt.LeftButton, buttons=None):
    ev = QMouseEvent(etype, QPointF(pos), QPointF(widget.mapToGlobal(pos)), button, button if buttons is None else buttons, Qt.NoModifier)
    QApplication.sendEvent(widget, ev)
QTest.qWait(120)
eq = core.eq_config(c.state, "speaker")
check([b.get("label") for b in eq["bands"]] == [b["label"] for b in conduit_eq.AUDIO_POINTS] and eq["seeded"]
      and not eq["enabled"] and all(b["gain"] == 0 for b in eq["bands"]),
      "a speaker's first EQ opens with flat audio tuning points")
check(json.loads(core.STATE_FILE.read_text())["speaker"]["eq"]["bands"][4].get("label") == "Upper mids", "point labels are saved")
pt = QPoint(int(gr.fx(700)), int(gr.gy(6)))
mouse(gr, QEvent.MouseButtonDblClick, pt); QTest.qWait(120)
eq = core.eq_config(c.state, "speaker")
check(len(eq["bands"]) == 8 and eq["enabled"] and abs(eq["bands"][-1]["freq"] - 700) < 30 and abs(eq["bands"][-1]["gain"] - 6) < 0.6,
      f"double-click adds a band where you clicked {eq['bands'][-1]}")
# Lock X: dragging only moves the gain
dlg.lock_x.setChecked(True)
b5 = dlg.eq()["bands"][5]
h = gr.handle_pos(b5).toPoint()
mouse(gr, QEvent.MouseButtonPress, h)
target = QPoint(int(gr.fx(9000)), int(gr.gy(4)))
mouse(gr, QEvent.MouseMove, target, Qt.NoButton, Qt.LeftButton)
mouse(gr, QEvent.MouseButtonRelease, target, Qt.LeftButton, Qt.NoButton); QTest.qWait(120)
b5 = core.eq_config(c.state, "speaker")["bands"][5]
check(b5["freq"] == 5000.0 and abs(b5["gain"] - 4) < 0.6 and layout.ui("eq_lock_x"), f"Lock X keeps the frequency {b5}")
dlg.lock_x.setChecked(False)
# a microphone gets voice points
mdlg = conduit_eqview.EqDialog(c, "mic", w, live=False); QTest.qWait(80)
check([b.get("label") for b in core.eq_config(c.state, "mic")["bands"]] == [b["label"] for b in conduit_eq.VOICE_POINTS],
      "a microphone's first EQ opens with voice tuning points")
mh = mdlg.graph.handle_pos(mdlg.eq()["bands"][4]).toPoint()
mouse(mdlg.graph, QEvent.MouseButtonPress, mh)
mouse(mdlg.graph, QEvent.MouseMove, mh - QPoint(0, 30), Qt.NoButton, Qt.LeftButton)
mouse(mdlg.graph, QEvent.MouseButtonRelease, mh - QPoint(0, 30), Qt.LeftButton, Qt.NoButton); QTest.qWait(120)
check(core.eq_config(c.state, "mic")["enabled"] and mdlg.enabled.isChecked(), "lifting a default point switches the EQ on")
mdlg.eq()["bands"] = []; mdlg._commit.start(); mdlg.close(); QTest.qWait(120)
mdlg = conduit_eqview.EqDialog(c, "mic", w, live=False); QTest.qWait(80); mdlg.close()
check(core.eq_config(c.state, "mic")["bands"] == [], "points are only seeded once (a cleared EQ stays clear)")
h = gr.handle_pos(dlg.eq()["bands"][0]).toPoint()
mouse(gr, QEvent.MouseButtonPress, h)
target = QPoint(int(gr.fx(200)), int(gr.gy(-9)))
mouse(gr, QEvent.MouseMove, target, Qt.NoButton, Qt.LeftButton)
mouse(gr, QEvent.MouseButtonRelease, target, Qt.LeftButton, Qt.NoButton); QTest.qWait(120)
b = core.eq_config(c.state, "speaker")["bands"][0]
check(abs(b["freq"] - 200) < 12 and abs(b["gain"] + 9) < 0.6, f"dragging a handle sets frequency and gain {b}")
q0 = b["q"]
wh = QWheelEvent(QPointF(target), QPointF(gr.mapToGlobal(target)), QPoint(0, 0), QPoint(0, 240), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
QApplication.sendEvent(gr, wh); QTest.qWait(120)
check(core.eq_config(c.state, "speaker")["bands"][0]["q"] > q0, "mouse wheel changes Q")
check(json.loads(core.STATE_FILE.read_text())["speaker"]["eq"]["bands"], "EQ edits are saved to state.json")
dlg._apply_preset(list(conduit_eq.PRESETS).index("Vocal clarity") + 1); QTest.qWait(120)
check(len(core.eq_config(c.state, "speaker")["bands"]) == 4, "preset applied")
dlg.gain.setValue(2.5)
gr.select(1); dlg.gain.setValue(-7.5); QTest.qWait(120)
check(core.eq_config(c.state, "speaker")["bands"][1]["gain"] == -7.5, "band row edits the selected band")
gr.remove(1); QTest.qWait(120)
check(len(core.eq_config(c.state, "speaker")["bands"]) == 3, "band removed")
import math
dlg.tap.feed([math.sin(2 * math.pi * 1000 * i / 48000) for i in range(conduit_eqview.FFT_N * 2)]); dlg.tap._analyse(); dlg._spectrum()
bins = dlg.tap.bins; peak = bins.index(max(bins)); f_peak = conduit_eq.log_freqs(conduit_eqview.N_BINS + 1)[peak]
check(700 < f_peak < 1200 and max(bins) > -12, f"spectrum analyser finds a 1 kHz tone ({f_peak:.0f} Hz, {max(bins):.1f} dB)")
dlg._frame(); dlg.grab()
check(gr.legend and gr.tint_time is not None and gr.tint_visual is not None, "smoothing tints and legend are drawn")
# smoothing maths
sm = conduit_eqview.SpectrumSmoother()
flat = [-80.0] * 40
sm.process(flat)
spike = list(flat); spike[20] = 0.0
shown, ti, vi = sm.process(spike)
check(shown[20] < -30 and ti[20] > 0.9 and max(vi) > 0.3, f"time smoothing stops a jump and says so ({shown[20]:.1f} dB)")
sm.visual_on = False
for _ in range(60):
    shown, ti, vi = sm.process(spike)
check(shown[20] > -25 and ti[20] < 0.4, f"... and settles ({shown[20]:.1f} dB)")
sm.time_on = sm.visual_on = False
shown, ti, vi = sm.process(spike)
check(shown == spike and not any(ti) and not any(vi), "both off = the raw frame")
sm.visual_on, sm.visual = True, 1.0
wide = sm.process(spike)[0]
sm.visual = 0.1
narrow = sm.process(spike)[0]
check(wide[20] < narrow[20] and wide[16] > narrow[16], "the smoothing amount sets how wide it spreads")
dlg.time_on.setChecked(False); dlg.smooth_amt.setValue(20)
check(not layout.ui("eq_time") and abs(layout.ui("eq_smooth_amount") - 0.2) < 1e-9 and not dlg.time_amt.isEnabled(),
      "smoothing options are saved")
dlg.time_on.setChecked(True); dlg.smooth_amt.setValue(50)
dlg.close()
check(not ERRORS, f"no exceptions in the equaliser {ERRORS[:1]}")

# ---- listen to this device
import conduit_listen
mgr = conduit_listen.manager(c)
spawned = []
class FakeProc:
    def __init__(self, cmd): self.cmd = cmd; self.killed = False
    def kill(self): self.killed = True
mgr.spawn = lambda cmd: (spawned.append(FakeProc(cmd)), spawned[-1])[1]
vm = core.hub_base_node(c.state, "mic", c.snap)
mgr.toggle(core.HUB_MIC, True)
check(mgr.is_on(core.HUB_MIC) and spawned and spawned[-1].cmd[0] == "pw-loopback", "Listen starts a loopback")
cmd = " ".join(spawned[-1].cmd) if spawned else ""
sink = core.listen_sink(c.state, c.snap)
check(sink is not None and core.is_hardware_sink(sink) and f'target.object = "{sink.name}"' in cmd
      and vm is not None and f'target.object = "{vm.name}"' in cmd, f"... from the mic to the real speakers ({sink and sink.name})")
check(not core.listen_supported(c.state, core.HUB_SPEAKER), "the Virtual Speaker can't be listened to (you hear it already)")
w.graph._sync_listening()
hub_item = next(bw.view.cells[core.HUB_MIC] for bw in w.graph.box_widgets.values() if core.HUB_MIC in bw.view.cells)
check(hub_item.listening, "the graph marks the listened device")
dlg2 = conduit_eqview.EqDialog(c, "mic", w, live=False); QTest.qWait(50)
check(dlg2.listen.isChecked(), "the EQ window's Listen box shows it")
dlg2.listen.setChecked(False); QTest.qWait(20)
check(not mgr.is_on(core.HUB_MIC) and spawned[-1].killed, "unticking it stops the loopback")
dlg2.close()
d2 = conduit_eqview.EqDialog(c, "speaker", w, live=False); QTest.qWait(20)
check(not d2.listen.isEnabled(), "Listen is disabled in the Virtual Speaker's EQ"); d2.close()
mgr.spawn = mgr._spawn

# ---- mixing desk
w = w.set_theme("desk"); QTest.qWait(200)
w.mode_group.button(0).click(); QTest.qWait(400)
canvas = w.desk.canvas; canvas.repaint()
def hit(kind, pred=lambda p: True):
    return next(r for r, k, p, _t in canvas.hits if k == kind and pred(p))
disc = core.new_cell(c.state, "Discord", "speaker"); c.commit(); QTest.qWait(50); canvas.repaint()
r = hit("route", lambda p: p == (disc["id"], core.HUB_SPEAKER))
mouse(canvas, QEvent.MouseButtonPress, r.center().toPoint()); QTest.qWait(80); canvas.repaint()
ls = [l for l in c.state["links"] if l["src"] == disc["id"] and l["dst"] == core.HUB_SPEAKER]
check(len(ls) == 2, "desk: IN button links the strip into the mix")
mouse(canvas, QEvent.MouseButtonPress, hit("route", lambda p: p == (disc["id"], core.HUB_SPEAKER)).center().toPoint()); QTest.qWait(80); canvas.repaint()
check(not [l for l in c.state["links"] if l["src"] == disc["id"]], "desk: pressing it again unlinks")
mouse(canvas, QEvent.MouseButtonPress, hit("mute", lambda p: p == disc["id"]).center().toPoint()); QTest.qWait(50); canvas.repaint()
check(core.cell_by_id(c.state, disc["id"])["blocked"], "desk: MUTE blocks the cell")
fr = next(r for r, k, i in canvas.faders if k == "cell" and i == disc["id"])
top = QPoint(int(fr.center().x()), int(fr.top() + 1))
mouse(canvas, QEvent.MouseButtonPress, QPoint(int(fr.center().x()), int(fr.center().y())))
mouse(canvas, QEvent.MouseMove, top, Qt.NoButton, Qt.LeftButton)
mouse(canvas, QEvent.MouseButtonRelease, top, Qt.LeftButton, Qt.NoButton); QTest.qWait(60)
check(core.cell_by_id(c.state, disc["id"])["volume"] >= 1.9, "desk: fader sets the cell volume")
mouse(canvas, QEvent.MouseButtonPress, hit("hubmute", lambda p: p == "mic").center().toPoint()); QTest.qWait(50)
check(c.state["mic"]["muted"], "desk: master MUTE mutes the device")

# ---- polling rate
c.state["daemon"].update(instant=False, poll_ms=250); c.commit(); QTest.qWait(50)
check(json.loads(core.STATE_FILE.read_text())["daemon"] == {"instant": False, "poll_ms": 250} and "250 ms" in w.poll_btn.text(),
      f"polling rate saved and shown ({w.poll_btn.text()})")
check(core.normalize({"version": 2, "daemon": {"poll_ms": 1}})["daemon"]["poll_ms"] == core.POLL_MIN_MS, "polling rate is clamped")
check(not ERRORS, f"no exceptions overall {ERRORS[:1]}")
print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"))
w.c._auto_timer.stop()
sys.exit(1 if FAILS else 0)
