"""
A QFrame that draws a soft rounded accent outline itself, in place of
QFrame.StyledPanel (which paints the native/KDE sunken panel). Used for
the clip-type rows in Settings > Clip Capture and the Auto Add Filter
rows.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame

from .style import current_style
from .rounded_rect import rounded_rect_path
from .theme import Theme


class ThemedFrame(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)

    def setFrameShape(self, shape) -> None:   # callers asking for a panel get this one
        super().setFrameShape(QFrame.NoFrame)

    def paintEvent(self, event) -> None:
        a = current_style()
        theme = Theme(a)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        radius = min(a.rounded_corner_radius, 12) if a.rounded_corners_enabled else 4
        path = rounded_rect_path(r, radius)
        fill = QColor(theme.card_background())
        fill.setAlphaF(0.35)
        p.fillPath(path, fill)
        line = QColor(theme.border())
        line.setAlphaF(0.55)
        p.setPen(QPen(line, 1.2))
        p.drawPath(path)
        p.end()
