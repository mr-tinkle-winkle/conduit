"""
Replaces the native KDE-styled vertical scroll bar in the Library grid.
Per the direct request: twice the default width, the handle in the
info/button blue (Theme.accent()), and the track (the space it scrolls
in) in the video-card-background color (Theme.card_background()).

Uses QStyleOptionSlider + the current style's own subControlRect() to
find where the handle actually is (accounting for scroll position and
handle size) rather than computing that geometry by hand -- the exact
standard Qt technique for a custom-painted scroll bar that still
behaves like a real one (draggable, clickable track, wheel-scrollable)
underneath.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QScrollBar, QStyleOptionSlider, QStyle, QApplication

from .style import current_style
from .rounded_rect import rounded_rect_path
from .theme import Theme


class CustomScrollBar(QScrollBar):
    def __init__(self, orientation=Qt.Vertical, parent=None, extent: "int | None" = None):
        super().__init__(orientation, parent)
        appearance = current_style()
        self._appearance = appearance
        self._theme = Theme(appearance)
        # `extent` gives a narrower bar for popups and small lists (the
        # combo dropdown, the file picker); the default stays twice the
        # native width, as asked for the Library grid.
        if extent is None:
            extent = QApplication.style().pixelMetric(QStyle.PM_ScrollBarExtent) * 2
        if orientation == Qt.Vertical:
            self.setFixedWidth(extent)
        else:
            self.setFixedHeight(extent)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        track_rect = QRectF(self.rect())
        radius = self._appearance.rounded_corner_radius if self._appearance.rounded_corners_enabled else 0
        radius = min(radius, track_rect.width() / 2, track_rect.height() / 2) if radius else 0
        if radius:
            painter.fillPath(rounded_rect_path(track_rect, radius), self._theme.card_background())
        else:
            painter.fillRect(track_rect, self._theme.card_background())

        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        handle_rect = self.style().subControlRect(QStyle.CC_ScrollBar, opt, QStyle.SC_ScrollBarSlider, self)
        if handle_rect.isValid() and handle_rect.width() > 0 and handle_rect.height() > 0:
            handle_qrectf = QRectF(handle_rect)
            handle_radius = min(radius, handle_qrectf.width() / 2, handle_qrectf.height() / 2) if radius else 0
            bg = self._theme.accent()
            if self.isSliderDown():
                bg = bg.darker(125)
            if handle_radius:
                painter.fillPath(rounded_rect_path(handle_qrectf, handle_radius), bg)
            else:
                painter.fillRect(handle_rect, bg)
        painter.end()
        # Deliberately NOT calling super().paintEvent() -- this fully
        # replaces the native scroll bar's painting; QScrollBar's own
        # mouse handling (drag, page-click, wheel) is untouched since
        # none of that lives in paintEvent.
