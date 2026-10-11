"""
A plain 3px, 15%-darker-than-itself outline around each top-level page
(Local, Uploaded, the overall Library page, Editor, Settings) -- per
the direct request. Each page uses ITS OWN background color as the
basis (darkened 15%), not one shared border color, since these pages
don't all share the same background. `skip_*` lets a page omit
whichever edge touches something else directly (no gap) -- Local/
Uploaded's own content area skips its top edge, which is flush against
the Library header (search/refresh/sort + the page-switch buttons)
right above it, so the outline doesn't visibly double up or bleed into
that seam.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, QPointF
from PySide6.QtGui import QPainter, QColor

BORDER_WIDTH = 3.0
DARKEN_FACTOR = 115  # Qt's own "darker(factor)" convention -- 115 = 15% darker


def paint_page_outline(widget, base_color: QColor, skip_top: bool = False, skip_bottom: bool = False,
                        skip_left: bool = False, skip_right: bool = False) -> None:
    painter = QPainter(widget)
    painter.setRenderHint(QPainter.Antialiasing)
    pen = painter.pen()
    pen.setColor(base_color.darker(DARKEN_FACTOR))
    pen.setWidthF(BORDER_WIDTH)
    painter.setPen(pen)
    inset = BORDER_WIDTH / 2
    rect = QRectF(widget.rect()).adjusted(inset, inset, -inset, -inset)
    if not skip_top:
        painter.drawLine(QPointF(rect.left(), rect.top()), QPointF(rect.right(), rect.top()))
    if not skip_bottom:
        painter.drawLine(QPointF(rect.left(), rect.bottom()), QPointF(rect.right(), rect.bottom()))
    if not skip_left:
        painter.drawLine(QPointF(rect.left(), rect.top()), QPointF(rect.left(), rect.bottom()))
    if not skip_right:
        painter.drawLine(QPointF(rect.right(), rect.top()), QPointF(rect.right(), rect.bottom()))
    painter.end()
