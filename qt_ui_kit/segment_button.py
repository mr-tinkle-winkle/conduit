"""
SegmentButton: the checkable, icon-or-text, highlight-filled button used
for navigation. In afterglow this is `LibraryTabButton`, used for:
- the left sidebar's page buttons (Library / Editor / Settings),
  each position="full", separated by ui_padding;
- a pair of touching page-switch tabs (Local | Uploaded):
  position="left" + position="right", zero spacing between them.

Put a group of these in a QButtonGroup(exclusive=True) and connect
idClicked to the QStackedWidget.setCurrentIndex.

Corner rule (used across the whole kit): never round a corner that
touches another element. `position` picks which corners round:
  "full"   all four      (a button with padding around it)
  "left"   left two      (first of a horizontal touching row)
  "right"  right two     (last of a horizontal touching row)
  "middle" none          (inside a touching row)
  "top"    top two       (first of a vertical touching stack)
  "bottom" bottom two    (last of a vertical touching stack)

States, all derived from Theme.highlight():
  unchecked  darker(140)
  hovered    lighter(112)
  pressed    darker(125)
  loading    darker(150) on top of the above (set_loading(True), e.g.
             while the page this button opens is still loading)
Plus the shared hover/press pulse (press_pulse.py).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, QSize
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QAbstractButton

from .press_pulse import PressPulse, scaled_cached
from .rounded_rect import rounded_rect_path
from .theme import Theme, contrast_text

_CORNERS = {
    # position: (top_left, top_right, bottom_left, bottom_right)
    "full": (True, True, True, True),
    "left": (True, False, True, False),
    "right": (False, True, False, True),
    "middle": (False, False, False, False),
    "top": (True, True, False, False),
    "bottom": (False, False, True, True),
}


class SegmentButton(QAbstractButton):
    def __init__(self, icon_pixmap: "QPixmap | str | None" = None, position: str = "full",
                 text: str = "", parent=None):
        """icon_pixmap: a QPixmap, or an icon NAME from icons.py (drawn as text by default)."""
        super().__init__(parent)
        if isinstance(icon_pixmap, str):
            from .icons import icon_pixmap as _named
            icon_pixmap = _named(icon_pixmap, 128)
        if position not in _CORNERS:
            raise ValueError(f"position must be one of {sorted(_CORNERS)}")
        self._pulse = PressPulse(self)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setText(text)
        self._icon_pixmap = icon_pixmap
        self._position = position
        self._icon_target_size = 32
        self._loading = False
        self._theme = Theme()

    def set_loading(self, loading: bool) -> None:
        if self._loading != loading:
            self._loading = loading
            self.update()

    def set_icon_target_size(self, size: int) -> None:
        """Icon edge length in px. Size this from the container (e.g.
        sidebar width minus a little padding) in the parent's
        resizeEvent, so icons scale with the window."""
        self._icon_target_size = max(1, int(size))
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        pad = 16
        if self._icon_pixmap is None and self.text():
            fm = self.fontMetrics()
            return QSize(fm.horizontalAdvance(self.text()) + 2 * pad, fm.height() + pad)
        return QSize(self._icon_target_size + pad, self._icon_target_size + pad)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        self._pulse.apply(painter)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        bg = self._theme.turquoise()
        if not self.isChecked():
            bg = bg.darker(140)
        if self.isDown():
            bg = bg.darker(125)
        elif self.underMouse():
            bg = bg.lighter(112)
        if self._loading:
            bg = bg.darker(150)

        rect = QRectF(self.rect())
        tl, tr, bl, br = _CORNERS[self._position]
        # A side with no rounded corner touches a neighbor: extend it so
        # it stays flush at rest even if the pulse reserves headroom
        # (PressPulse.touching_extension; 0 while overshoot is 1.0).
        ex, ey = self._pulse.touching_extension(rect)
        rect = rect.adjusted(0 if (tl or bl) else -ex, 0 if (tl or tr) else -ey,
                             0 if (tr or br) else ex, 0 if (bl or br) else ey)
        radius = self._theme.corner_radius if self._theme.rounded_corners_enabled else 0
        if radius:
            painter.fillPath(
                rounded_rect_path(rect, radius, top_left=tl, top_right=tr, bottom_left=bl, bottom_right=br),
                bg,
            )
        else:
            painter.fillRect(rect, bg)

        if self._icon_pixmap is not None and not self._icon_pixmap.isNull():
            scaled = scaled_cached(self._icon_pixmap, self._icon_target_size, self._icon_target_size)
            painter.drawPixmap((self.width() - scaled.width()) // 2,
                               (self.height() - scaled.height()) // 2, scaled)
        elif self.text():
            painter.setPen(contrast_text(bg))
            painter.drawText(self.rect(), Qt.AlignCenter, self.text())
        painter.end()

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)
