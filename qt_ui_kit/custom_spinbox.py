"""
Replaces native/KDE QSpinBox/QDoubleSpinBox chrome with the same
rounded, theme-colored box CustomLineEdit already gives plain text
fields -- plus two small custom-painted increment/decrement arrow
buttons (no icon asset needed; a triangle each is simple enough to
draw directly), replacing the native up/down spin arrows.

QAbstractSpinBox already does essentially everything else needed
(keyboard up/down arrows, wheel-to-step, typing a value directly,
range clamping, step size) -- only the box's own background/border
painting and the increment/decrement buttons' appearance are replaced.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QPainterPath
from PySide6.QtWidgets import QAbstractSpinBox, QDoubleSpinBox, QSpinBox, QAbstractButton

from .press_pulse import PressPulse
from .style import current_style
from .rounded_rect import rounded_rect_path
from .theme import Theme, contrast_text

_ARROW_WIDTH = 20


class _SpinArrowButton(QAbstractButton):
    def __init__(self, up: bool, parent=None):
        super().__init__(parent)
        self._pulse = PressPulse(self)  # shared hover/press pulse, see press_pulse.py
        self._up = up
        self.setCursor(Qt.PointingHandCursor)
        self.setAutoRepeat(True)
        self.setAutoRepeatDelay(400)
        self.setAutoRepeatInterval(80)
        appearance = current_style()
        self._theme = Theme(appearance)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        self._pulse.apply(painter)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        bg = self._theme.accent()
        if self.isDown():
            bg = bg.darker(125)
        elif self.underMouse():
            bg = bg.lighter(115)
        radius = min(6.0, rect.height() / 2, rect.width() / 2)
        painter.fillPath(rounded_rect_path(rect, radius), bg)

        fg = contrast_text(bg)
        painter.setBrush(fg)
        painter.setPen(Qt.NoPen)
        cx, cy = rect.center().x(), rect.center().y()
        w, h = rect.width() * 0.3, rect.height() * 0.22
        path = QPainterPath()
        if self._up:
            path.moveTo(cx - w, cy + h * 0.5)
            path.lineTo(cx + w, cy + h * 0.5)
            path.lineTo(cx, cy - h * 0.5)
        else:
            path.moveTo(cx - w, cy - h * 0.5)
            path.lineTo(cx + w, cy - h * 0.5)
            path.lineTo(cx, cy + h * 0.5)
        path.closeSubpath()
        painter.drawPath(path)
        painter.end()

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)


class _CustomSpinBoxMixin:
    """Shared painting + increment-button wiring for both the int and
    float variants below -- QAbstractSpinBox's own stepBy(steps) is
    what both QSpinBox and QDoubleSpinBox already implement to apply
    one step in either direction respecting range/wrapping, so the
    arrow buttons just call that directly rather than needing separate
    logic per variant."""

    def _setup_custom_chrome(self) -> None:
        appearance = current_style()
        self._appearance = appearance
        self._theme = Theme(appearance)
        self.setButtonSymbols(QAbstractSpinBox.NoButtons)  # hide the native up/down arrows entirely
        text_color = contrast_text(self._theme.card_background()).name()
        self.setStyleSheet(
            f"QAbstractSpinBox {{ background: transparent; border: none; color: {text_color}; "
            f"padding: 2px 26px 2px 8px; }}"
        )

        self._up_btn = _SpinArrowButton(up=True, parent=self)
        self._down_btn = _SpinArrowButton(up=False, parent=self)
        self._up_btn.clicked.connect(lambda: self.stepBy(1))
        self._down_btn.clicked.connect(lambda: self.stepBy(-1))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Each arrow gets HALF the spinbox's own height, computed
        # dynamically -- NOT a fixed 22px each. A fixed 22px assumed a
        # tall spinbox (needing ~50px+ total to stack two 22px arrows
        # without overlapping), but a real spinbox is typically only
        # ~28-32px tall -- reported directly: "the bottom increment
        # button [was] the only visible one, the top increment button
        # only filling about 20% of the box," exactly what two
        # oversized, overlapping buttons anchored from opposite edges
        # would look like (the later-painted one visually winning the
        # shared space). Sizing each to exactly half the box's own
        # height guarantees they always tile perfectly with zero
        # overlap and zero gap, regardless of how tall or short this
        # particular spinbox actually is.
        margin = 2
        arrow_w = _ARROW_WIDTH
        x = self.width() - arrow_w - margin
        usable_h = self.height() - 2 * margin
        arrow_h = usable_h // 2
        self._up_btn.setGeometry(x, margin, arrow_w, arrow_h)
        self._down_btn.setGeometry(x, margin + arrow_h, arrow_w, usable_h - arrow_h)
        # Place the text field ourselves. Newer Qt (seen on PySide6 6.12) lays the inner line
        # edit out 1px wide when the spin box has stylesheet padding and NoButtons, so the value
        # was invisible -- the gallery showed empty spin boxes.
        le = self.lineEdit()
        if le is not None:
            le.setGeometry(8, margin, max(10, x - 8 - 2), self.height() - 2 * margin)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        radius = self._appearance.rounded_corner_radius if self._appearance.rounded_corners_enabled else 8
        radius = min(radius, rect.height() / 2) if radius else 0
        if radius:
            path = rounded_rect_path(rect, radius)
            painter.fillPath(path, self._theme.card_background())
            pen = painter.pen()
            pen.setColor(self._theme.border())
            pen.setWidthF(2)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)
        else:
            painter.fillRect(self.rect(), self._theme.card_background())
        painter.end()
        # Deliberately calling QAbstractSpinBox's OWN paintEvent (not
        # skipping it) so the embedded line-edit text/cursor still
        # renders normally on top of the background just painted above
        # -- same layering trick CustomLineEdit uses, just via an
        # explicit base-class call here since QSpinBox/QDoubleSpinBox
        # would otherwise repaint native chrome UNDER our own text if
        # we called their paintEvent instead of the shared base's.
        QAbstractSpinBox.paintEvent(self, event)


class CustomSpinBox(_CustomSpinBoxMixin, QSpinBox):
    def __init__(self, parent=None):
        QSpinBox.__init__(self, parent)
        self._setup_custom_chrome()


class CustomDoubleSpinBox(_CustomSpinBoxMixin, QDoubleSpinBox):
    def __init__(self, parent=None):
        QDoubleSpinBox.__init__(self, parent)
        self._setup_custom_chrome()
