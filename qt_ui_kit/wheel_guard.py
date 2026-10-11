"""
App-wide: the mouse wheel never changes a selection box (QComboBox,
including QFontComboBox) or a number box (any QAbstractSpinBox) -- it's too easy to change a setting by accident
while scrolling a page. The wheel event is handed to the box's parent
instead, so the surrounding page still scrolls as if the box weren't
there. Installed once on the QApplication (see install()).
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QAbstractScrollArea, QAbstractSpinBox, QApplication, QComboBox


class _ComboWheelGuard(QObject):
    def eventFilter(self, obj, event) -> bool:
        if event.type() != QEvent.Wheel or not hasattr(obj, "parentWidget"):
            return False
        box = obj if isinstance(obj, (QComboBox, QAbstractSpinBox)) else None
        if box is None:
            parent = obj.parentWidget() if obj.isWidgetType() else None
            if isinstance(parent, (QComboBox, QAbstractSpinBox)):
                box = parent          # the line edit inside a spin/combo box
        if box is not None:
            obj = box
            # Hand it to the nearest scrolling ancestor (a forwarded, non-
            # spontaneous wheel event isn't propagated up by Qt on its own).
            w = obj.parentWidget()
            while w is not None and not isinstance(w, QAbstractScrollArea):
                w = w.parentWidget()
            if w is not None:
                vp = w.viewport()
                pos = vp.mapFromGlobal(event.globalPosition().toPoint())
                fwd = QWheelEvent(pos, event.globalPosition(), event.pixelDelta(), event.angleDelta(),
                                  event.buttons(), event.modifiers(), event.phase(), event.inverted(),
                                  event.source())
                QApplication.sendEvent(vp, fwd)
            return True
        return False


_guard = None


def install() -> None:
    global _guard
    app = QApplication.instance()
    if app is None or _guard is not None:
        return
    _guard = _ComboWheelGuard(app)
    app.installEventFilter(_guard)
