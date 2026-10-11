"""
Replaces native QRadioButton rendering (used for the Sort By tab's
exclusive sort-order choice) with the same card-background-box +
checkmark-icon look CustomCheckBox already gives checkboxes, just with
a circular indicator instead of a rounded-rect one, matching the
conventional round vs. square distinction between radio buttons and
checkboxes. Exclusivity itself is unchanged -- still enforced by
whatever QButtonGroup the caller adds these to, same as the native
QRadioButtons before.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, QSize
from PySide6.QtGui import QPainter, QColor, QPen
from PySide6.QtWidgets import QAbstractButton

from .press_pulse import PressPulse, scaled_cached
from .style import current_style
from .resources import resource_qpixmap
from .theme import Theme

_BOX_SIZE = 20
_TEXT_GAP = 8


class CustomRadioButton(QAbstractButton):
    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._pulse = PressPulse(self)  # indicator-only pulse, see paintEvent
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        appearance = current_style()
        self._appearance = appearance
        self._theme = Theme(appearance)
        self._checkmark = resource_qpixmap("checkmark_icon.png")

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        text_w = fm.horizontalAdvance(self.text()) if self.text() else 0
        width = _BOX_SIZE + (_TEXT_GAP + text_w if self.text() else 0)
        height = max(_BOX_SIZE, fm.height()) + 4
        return QSize(width, height)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        box_rect = QRectF(0, (self.height() - _BOX_SIZE) / 2, _BOX_SIZE, _BOX_SIZE)
        # Pulse only the indicator box (restored before the label is
        # drawn) so the text doesn't slide around as the box scales.
        painter.save()
        self._pulse.apply(painter, box_rect.center())
        bg = self._theme.card_background()
        if self.underMouse():
            bg = bg.lighter(115)
        painter.setPen(Qt.NoPen)
        painter.setBrush(bg)
        painter.drawEllipse(box_rect)

        # A FRESH QPen here, not painter.pen() -- Qt.NoPen just set
        # above is a PEN STYLE, not merely "no color set yet", and it
        # survives a later setColor()/setWidthF() call on that same
        # pen object; only the style itself changes that. Calling
        # painter.pen() right after setPen(Qt.NoPen) silently returns
        # a pen that still won't draw ANY stroke no matter what color
        # or width gets set on it afterward -- which is exactly why
        # this outline wasn't rendering at all. A brand-new QPen
        # defaults to Qt.SolidLine, avoiding the trap entirely.
        pen = QPen(self._theme.border())
        pen.setWidthF(1.5)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(box_rect)

        if self.isChecked() and not self._checkmark.isNull():
            margin = 3
            target = box_rect.adjusted(margin, margin, -margin, -margin)
            scaled = scaled_cached(self._checkmark, round(target.width()), round(target.height()))
            x = target.x() + (target.width() - scaled.width()) / 2
            y = target.y() + (target.height() - scaled.height()) / 2
            painter.drawPixmap(round(x), round(y), scaled)

        painter.restore()

        if self.text():
            painter.setPen(QColor(self._appearance.card_text_color))
            text_rect = self.rect().adjusted(_BOX_SIZE + _TEXT_GAP, 0, 0, 0)
            painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, self.text())
        painter.end()

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)
