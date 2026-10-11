"""
Replaces the native QToolButton arrow-type indicator used for collapsing
a Clip Options row. A circular button showing a ">" character that
smoothly rotates 90 degrees (pointing right when collapsed, down when
expanded) rather than snapping between two different arrow glyphs.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QAbstractButton

from .press_pulse import PressPulse
from .style import current_style
from .theme import Theme, contrast_text

_SIZE = 24
_ANIM_DURATION_MS = 180


class CollapseToggleButton(QAbstractButton):
    def __init__(self, expanded: bool = True, parent=None):
        super().__init__(parent)
        self._pulse = PressPulse(self)  # shared hover/press pulse, see press_pulse.py
        self.setCheckable(True)
        self.setChecked(expanded)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(_SIZE, _SIZE)

        appearance = current_style()
        self._theme = Theme(appearance)
        # 0 degrees = pointing right (collapsed), 90 = pointing down (expanded)
        self._rotation = 90.0 if expanded else 0.0

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(_ANIM_DURATION_MS)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim_value)
        self.toggled.connect(self._animate_to_state)

    def _animate_to_state(self, checked: bool) -> None:
        target = 90.0 if checked else 0.0
        self._anim.stop()
        self._anim.setStartValue(self._rotation)
        self._anim.setEndValue(target)
        self._anim.start()

    def _on_anim_value(self, value) -> None:
        self._rotation = float(value)
        self.update()

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
        painter.setPen(Qt.NoPen)
        painter.setBrush(bg)
        painter.drawEllipse(rect)

        painter.save()
        painter.translate(rect.center())
        painter.rotate(self._rotation)
        painter.setPen(contrast_text(bg))
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        text_rect = QRectF(-rect.width() / 2, -rect.height() / 2, rect.width(), rect.height())
        from .icons import glyph
        painter.drawText(text_rect, Qt.AlignCenter, glyph("expand", painter.font()))
        painter.restore()
        painter.end()

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)
