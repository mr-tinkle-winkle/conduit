#!/usr/bin/env python3
"""
conduit_gui.py -- Conduit's window (PySide6 + qt_ui_kit) and command-line flags.

Two editors over the same ~/.config/conduit/state.json, switched at the very top:
  Graphical  a qpwgraph-style node canvas (conduit_graph.py)
  List       the classic Speaker / Microphone / Custom Conduit lists (conduit_list.py)

Auto-apply (default on): every edit writes state.json and restarts the daemon.
Off: edits stay pending (dirty marker) until Save.  Cell positions, zoom and the
like live in layout.json, which the daemon never reads -- moving things never
restarts anything.

Flags (apply and exit, no window):
  conduit --micVolume=0.5 | --speakerVolume=2.0
  conduit --mute=TARGET | --unmute=TARGET | --togglemute=TARGET
      TARGET: speaker, mic, or a Custom Conduit's name
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import conduit_core as core  # noqa: E402

LOGO = HERE / "assets" / "conduit_logo.png"


# ---------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------

def _valid_targets(state):
    return ["speaker", "mic"] + [c.get("name") or f"Custom Conduit {c['id']}" for c in state["custom"]["conduits"]]


def _resolve_target(state, target):
    t = target.strip()
    tl = t.lower()
    if tl == "speaker":
        return state["speaker"]
    if tl in ("mic", "microphone"):
        return state["mic"]
    for c in state["custom"]["conduits"]:
        name = (c.get("name") or f"Custom Conduit {c['id']}").strip().lower()
        if tl == name or t in (f"custom.{c['id']}", f"custom:{c['id']}", str(c["id"])):
            return c
    return None


def _build_cli_parser():
    p = argparse.ArgumentParser(prog="conduit", description="Conduit -- run with no flags to open the window.")
    p.add_argument("--micVolume", type=float, metavar="MULTIPLIER", help="Microphone master volume (no limit)")
    p.add_argument("--speakerVolume", type=float, metavar="MULTIPLIER", help="Speaker master volume (no limit)")
    p.add_argument("--mute", nargs="?", const="", metavar="TARGET", help="speaker, mic, or a Custom Conduit's name")
    p.add_argument("--unmute", nargs="?", const="", metavar="TARGET")
    p.add_argument("--togglemute", nargs="?", const="", metavar="TARGET")
    return p


def handle_cli_args(argv) -> bool:
    args, _unknown = _build_cli_parser().parse_known_args(argv)
    if all(v is None for v in (args.micVolume, args.speakerVolume, args.mute, args.unmute, args.togglemute)):
        return False
    core.ensure_config_exists()
    state = core.load_state()
    changed = False
    if args.micVolume is not None:
        state["mic"]["volume"] = args.micVolume
        print(f"conduit: mic volume set to {args.micVolume}")
        changed = True
    if args.speakerVolume is not None:
        state["speaker"]["volume"] = args.speakerVolume
        print(f"conduit: speaker volume set to {args.speakerVolume}")
        changed = True
    for flag, value, mode in (("--mute", args.mute, "mute"), ("--unmute", args.unmute, "unmute"),
                              ("--togglemute", args.togglemute, "toggle")):
        if value is None:
            continue
        if not value:
            print(f"conduit: {flag} needs a target, e.g. {flag}=mic. "
                  f"Available targets: {', '.join(_valid_targets(state))}", file=sys.stderr)
            sys.exit(1)
        entry = _resolve_target(state, value)
        if entry is None:
            print(f"conduit: no target named {value!r}. Available targets: {', '.join(_valid_targets(state))}",
                  file=sys.stderr)
            sys.exit(1)
        entry["muted"] = True if mode == "mute" else False if mode == "unmute" else not entry.get("muted", False)
        print(f"conduit: {value} {'muted' if entry['muted'] else 'unmuted'}")
        changed = True
    if changed:
        core.save_state(state)
        core.restart_daemon()
    return True


# ---------------------------------------------------------------------------
# window
# ---------------------------------------------------------------------------

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import (QApplication, QButtonGroup, QHBoxLayout, QLabel, QMainWindow,  # noqa: E402
                               QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget)

import qt_ui_kit as kit  # noqa: E402

from conduit_controller import Controller  # noqa: E402
from conduit_layout import Layout  # noqa: E402


def app_icon() -> QIcon:
    return QIcon.fromTheme("conduit", QIcon(str(LOGO)))


class ConduitWindow(QMainWindow):
    def __init__(self, controller: Controller | None = None):
        super().__init__()
        self.setWindowTitle("Conduit")
        self.setWindowIcon(app_icon())
        self.layout_ = controller.layout if controller else Layout()
        self.c = controller or Controller(self.layout_)
        self._tray = None

        central = QWidget()
        central.setAutoFillBackground(True)
        self.setCentralWidget(central)
        v = QVBoxLayout(central)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)

        # -- very top: Graphical | List, and the save state
        top = QHBoxLayout()
        top.setSpacing(0)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.btn_graph = kit.SegmentButton(None, "left", "  Graphical  ")
        self.btn_list = kit.SegmentButton(None, "right", "  List  ")
        for i, b in enumerate((self.btn_graph, self.btn_list)):
            b.setCheckable(True)
            b.setMinimumHeight(32)
            self.mode_group.addButton(b, i)
            top.addWidget(b)
        self.mode_group.idClicked.connect(self._set_mode)
        top.addSpacing(16)
        self.dirty_label = QLabel("")
        top.addWidget(self.dirty_label)
        top.addStretch(1)
        self.revert_btn = kit.CustomButton("Discard")
        self.revert_btn.setToolTip("Throw away the pending edits and reload the saved config")
        self.revert_btn.clicked.connect(self.c.revert)
        self.save_btn = kit.CustomButton("Save")
        self.save_btn.setToolTip("Write the config and restart the daemon")
        self.save_btn.clicked.connect(self.c.save)
        top.addWidget(self.revert_btn)
        top.addSpacing(6)
        top.addWidget(self.save_btn)
        v.addLayout(top)

        # -- toolbar
        bar = QHBoxLayout()
        bar.setSpacing(10)
        refresh = kit.CustomButton("Refresh devices")
        refresh.clicked.connect(self.c.refresh)
        bar.addWidget(refresh)
        self.auto_refresh = kit.CustomCheckBox("Auto Refresh Devices")
        self.auto_refresh.setToolTip("Re-check PipeWire every few seconds (keeps the graph's link colours live)")
        self.auto_refresh.setChecked(bool(self.layout_.ui("auto_refresh")))
        self.auto_refresh.toggled.connect(self.c.set_auto_refresh)
        bar.addWidget(self.auto_refresh)
        self.unmanaged = kit.CustomCheckBox("Show Unmanaged Links")
        self.unmanaged.setToolTip("Also draw (dashed) links Conduit didn't make -- e.g. WirePlumber's own -- "
                                  "touching anything on the canvas. Right-click one to add or block it.")
        self.unmanaged.setChecked(bool(self.layout_.ui("show_unmanaged")))
        bar.addWidget(self.unmanaged)
        bar.addStretch(1)
        self.auto_apply = kit.CustomCheckBox("Auto-apply")
        self.auto_apply.setToolTip("On: every edit saves and restarts the daemon. Off: edits wait for Save.")
        self.auto_apply.setChecked(self.c.auto_apply)
        self.auto_apply.toggled.connect(self._auto_apply_toggled)
        bar.addWidget(self.auto_apply)
        tray = kit.CustomButton("Close to Tray")
        tray.clicked.connect(self.close_to_tray)
        bar.addWidget(tray)
        v.addLayout(bar)

        # -- editors
        from conduit_graph import GraphEditor
        from conduit_list import ListEditor
        self.stack = QStackedWidget()
        self.graph = GraphEditor(self.c)
        self.list = ListEditor(self.c)
        self.stack.addWidget(self.graph)
        self.stack.addWidget(self.list)
        v.addWidget(self.stack, 1)
        self.unmanaged.toggled.connect(self.graph.set_show_unmanaged)
        self.graph.show_unmanaged_changed.connect(self._sync_unmanaged_box)

        self.c.dirty_changed.connect(self._dirty)
        self._dirty(self.c.dirty)
        mode = 1 if self.layout_.ui("mode") == "list" else 0
        self.mode_group.button(mode).setChecked(True)
        self._set_mode(mode, animate=False)

        geo = self.layout_.data["window"].get("size")
        self.resize(*(geo if isinstance(geo, list) and len(geo) == 2 else (1400, 900)))

    # -- top bar
    def _set_mode(self, idx, animate=True):
        self.layout_.set_ui("mode", "list" if idx == 1 else "graphical")
        self.unmanaged.setVisible(idx == 0)
        if animate and self.stack.currentIndex() != idx:
            kit.crossfade_to_index(self.stack, idx)
        else:
            self.stack.setCurrentIndex(idx)

    def _sync_unmanaged_box(self, on):
        self.unmanaged.blockSignals(True)
        self.unmanaged.setChecked(on)
        self.unmanaged.blockSignals(False)

    def _auto_apply_toggled(self, on):
        self.c.set_auto_apply(on)
        self._dirty(self.c.dirty)

    def _dirty(self, dirty):
        manual = not self.c.auto_apply
        self.save_btn.setVisible(manual)
        self.revert_btn.setVisible(manual)
        self.save_btn.setEnabled(dirty)
        self.revert_btn.setEnabled(dirty)
        if manual:
            self.dirty_label.setText("●  Unsaved changes" if dirty else "Saved")
        else:
            self.dirty_label.setText("Changes apply automatically")
        self.setWindowTitle("Conduit" + (" *" if dirty else ""))

    # -- tray
    def close_to_tray(self):
        if self._tray is None:
            self._tray = QSystemTrayIcon(app_icon(), self)
            self._tray.setToolTip("Conduit")
            menu = kit.themed_menu("", self)
            menu.addAction("Open Conduit", self._restore)
            menu.addAction("Quit", self._quit)
            self._tray.setContextMenu(menu)
            self._tray.activated.connect(
                lambda r: self._restore() if r == QSystemTrayIcon.ActivationReason.Trigger else None)
        self._tray.show()
        self.hide()

    def _restore(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def _quit(self):
        self._tray_quit = True
        self.close()

    def closeEvent(self, e):
        if self.c.dirty:
            if kit.ask_confirm(self, "Unsaved changes", "Save your pending edits before closing?", "Save"):
                self.c.save()
        self.layout_.data["window"]["size"] = [self.width(), self.height()]
        self.layout_.save_now()
        super().closeEvent(e)
        QApplication.instance().quit()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Conduit")
    app.setDesktopFileName("conduit")
    kit.install(app)
    try:
        from qt_ui_kit import themed_dialogs
        themed_dialogs.RECENT_COLORS_FILE = core.CONFIG_DIR / ".recent_colors.json"
    except Exception:  # noqa: BLE001
        pass
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(False)
    w = ConduitWindow()
    w.show()
    w.c.start()
    sys.exit(app.exec())


if __name__ == "__main__":
    if handle_cli_args(sys.argv[1:]):
        sys.exit(0)
    main()
