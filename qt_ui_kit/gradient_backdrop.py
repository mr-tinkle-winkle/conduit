"""
Wraps a page widget with a gradient image behind it, inset by a small
margin so a border of the gradient shows around the page's edges --
Library and Editor otherwise sat directly against the stack's background
with nothing visually separating them, which read as "a little empty in
between."

The wrapped widget's own colors/content are unaffected -- it just gets
placed on top of the gradient with a margin around it, rather than filling
the whole container.
"""
from __future__ import annotations

from PySide6.QtCore import QRect
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget, QVBoxLayout

from PySide6.QtGui import QPixmap

from .icons import texture

BORDER_MARGIN = 10


class GradientBackdrop(QWidget):
    def __init__(self, content: QWidget, image: "str | QPixmap | None" = None, parent=None, margin: int = BORDER_MARGIN):
        """image: an image path / QPixmap, or None for the theme gradient (icons.texture("backdrop"))."""
        super().__init__(parent)
        if isinstance(image, QPixmap):
            self._gradient_pixmap = image
        elif image:
            self._gradient_pixmap = QPixmap(image)
        else:
            self._gradient_pixmap = texture("backdrop")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(margin, margin, margin, margin)
        layout.addWidget(content)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        # Scaled to fit this widget's current full size (not the inset
        # content area) -- it's the border peeking out around the
        # content that's the point.
        painter.drawPixmap(QRect(0, 0, self.width(), self.height()), self._gradient_pixmap)
        painter.end()
        super().paintEvent(event)
