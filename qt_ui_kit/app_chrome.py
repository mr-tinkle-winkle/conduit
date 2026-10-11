"""
App-wide catch-alls for the bits of native/KDE chrome that no single
widget owns, installed once from MainWindow:

- Tooltips: every tooltip (a widget's setToolTip(), or an item's
  ToolTipRole in a list) is shown in an app-styled bubble instead of
  the Breeze tooltip. The native tooltip is also given theme colours as
  a fallback for anything this doesn't catch.
- Menus: any QMenu that shows up without a stylesheet of its own -- the
  Cut/Copy/Paste menu Qt builds for every text field and spin box, a
  submenu someone forgot to style -- gets the same menu styling the
  right-click menus use.
- Scroll bars: any scroll area still using the native scroll bar (a
  text box, a list) gets a slim CustomScrollBar.
"""
from __future__ import annotations

import atexit

from PySide6.QtCore import QObject, QEvent, Qt, QRectF, QPoint, QTimer
from PySide6.QtGui import QPainter, QPen, QPalette, QColor, QCursor, QGuiApplication
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QAbstractScrollArea, QLabel, QMenu, QToolTip, QWidget,
    QVBoxLayout,
)

from .style import current_style
from .custom_scrollbar import CustomScrollBar
from .rounded_rect import rounded_rect_path
from .theme import Theme, contrast_text


class ToolTipBubble(QWidget):
    def __init__(self):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        self.label = QLabel()
        self.label.setWordWrap(False)
        self.label.setTextFormat(Qt.AutoText)
        lay.addWidget(self.label)
        self.source: "QWidget | None" = None
        self.text = ""
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)
        self._restyle()

    def _restyle(self) -> None:
        a = current_style()
        self._appearance = a
        self._theme = Theme(a)
        color = contrast_text(self._theme.card_background()).name()
        self.label.setStyleSheet(f"QLabel {{ color: {color}; background: transparent; }}")

    def show_text(self, global_pos: QPoint, text: str, source: QWidget) -> None:
        if not text:
            self.hide()
            return
        if text != self.text or not self.isVisible():
            self._restyle()
            self.text = text
            self.label.setText(text)
            # Long plain-text tooltips wrap instead of running off the screen.
            self.label.setWordWrap(len(text) > 70 and "\n" not in text)
            if self.label.wordWrap():
                self.label.setFixedWidth(360)
            else:
                self.label.setMinimumWidth(0)
                self.label.setMaximumWidth(16777215)
            self.adjustSize()
        self.source = source
        screen = QGuiApplication.screenAt(global_pos) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        x = global_pos.x() + 14
        y = global_pos.y() + 20
        if x + self.width() > avail.right():
            x = avail.right() - self.width()
        if y + self.height() > avail.bottom():
            y = global_pos.y() - self.height() - 8
        self.move(max(avail.left(), x), max(avail.top(), y))
        self.show()
        self.raise_()
        self._hide_timer.start(max(4000, min(15000, len(text) * 90)))

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        radius = min(8, r.height() / 2) if self._appearance.rounded_corners_enabled else 3
        path = rounded_rect_path(r, radius)
        bg = QColor(self._theme.card_background())
        bg.setAlphaF(0.97)
        p.fillPath(path, bg)
        p.setPen(QPen(self._theme.outline(), 1.2))
        p.drawPath(path)
        p.end()


_MENU_TYPES = (QEvent.Polish,)
_HIDE_TYPES = {QEvent.MouseButtonPress, QEvent.MouseButtonDblClick, QEvent.Wheel, QEvent.KeyPress,
               QEvent.WindowDeactivate}


class _ChromeFilter(QObject):
    def __init__(self, app):
        super().__init__(app)
        self.bubble: "ToolTipBubble | None" = None

    def _tip_for(self, obj, event) -> "tuple[str, QWidget] | None":
        parent = obj.parentWidget() if isinstance(obj, QWidget) else None
        if isinstance(parent, QAbstractItemView) and obj is parent.viewport():
            idx = parent.indexAt(event.pos())
            if idx.isValid():
                t = idx.data(Qt.ToolTipRole)
                if t:
                    return str(t), obj
            return ("", obj) if not parent.toolTip() else (parent.toolTip(), obj)
        if isinstance(obj, QMenu):
            return None   # QMenu's own action tooltips stay with QMenu
        w = obj
        while isinstance(w, QWidget):
            if w.toolTip():
                return w.toolTip(), w
            if w.isWindow():
                break
            w = w.parentWidget()
        return ("", obj)

    def eventFilter(self, obj, event) -> bool:
        t = event.type()
        if t == QEvent.ToolTip and isinstance(obj, QWidget):
            found = self._tip_for(obj, event)
            if found is None:
                return False
            text, src = found
            if self.bubble is None:
                self.bubble = ToolTipBubble()
            if text:
                self.bubble.show_text(event.globalPos(), text, src)
            else:
                self.bubble.hide()
            return True
        if t == QEvent.Polish:
            if isinstance(obj, QMenu) and not obj.styleSheet():
                from .menus import menu_stylesheet as _menu_stylesheet
                obj.setStyleSheet(_menu_stylesheet(current_style()))
            elif isinstance(obj, QAbstractScrollArea) and not obj.property("_kit_native_bars"):
                if not isinstance(obj.verticalScrollBar(), CustomScrollBar):
                    obj.setVerticalScrollBar(CustomScrollBar(Qt.Vertical, extent=10))
                if not isinstance(obj.horizontalScrollBar(), CustomScrollBar):
                    obj.setHorizontalScrollBar(CustomScrollBar(Qt.Horizontal, extent=10))
            return False
        b = self.bubble
        if b is not None and b.isVisible():
            if t in _HIDE_TYPES or (t == QEvent.Leave and obj is b.source):
                b.hide()
        return False


_filter = None


def uninstall() -> None:
    global _filter
    app = QApplication.instance()
    if _filter is None:
        return
    if app is not None:
        app.removeEventFilter(_filter)
    if _filter.bubble is not None:
        _filter.bubble.hide()
    _filter = None


def install() -> None:
    global _filter
    app = QApplication.instance()
    if app is None or _filter is not None:
        return
    _filter = _ChromeFilter(app)
    app.installEventFilter(_filter)
    # Take the Python filter out before the interpreter starts tearing
    # down: Qt keeps delivering events while QApplication is destroyed,
    # and a Python filter called at that point crashes the process.
    app.aboutToQuit.connect(uninstall)
    atexit.register(uninstall)
    # Fallback colours for any native tooltip that still slips through.
    theme = Theme(current_style())
    pal = QToolTip.palette()
    pal.setColor(QPalette.ToolTipBase, theme.card_background())
    pal.setColor(QPalette.ToolTipText, contrast_text(theme.card_background()))
    QToolTip.setPalette(pal)
