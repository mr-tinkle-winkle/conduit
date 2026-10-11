"""
A QListWidget that paints its own rows instead of leaving them to the
native/KDE item view look: a rounded accent highlight for the
selection, a softer one for hover, an optional small glyph on the left
and an optional dim secondary text on the right. Used by the custom
combo box popup and the file picker, so both read as part of the app
rather than as Breeze list views.

Rows can carry:
  ROLE_GLYPH      -- a glyph name drawn by draw_glyph() ("folder", "video", ...)
  ROLE_SECONDARY  -- right-aligned dim text (file size, date, ...)
  Qt.FontRole     -- per-row font (the font picker shows each font in itself)
A row whose AccessibleDescriptionRole is "separator" (what
QComboBox.insertSeparator() makes) is drawn as a thin accent line.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, QSize, QPointF
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QFontMetrics, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView, QListWidget, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
)

from .style import current_style
from .custom_scrollbar import CustomScrollBar
from .rounded_rect import rounded_rect_path
from .theme import Theme, contrast_text

ROLE_GLYPH = Qt.UserRole + 101
ROLE_SECONDARY = Qt.UserRole + 102


def is_separator(index) -> bool:
    return index.data(Qt.AccessibleDescriptionRole) == "separator"


def draw_glyph(p: QPainter, name: str, rect: QRectF, color: QColor) -> None:
    """Small line-art icons drawn in the theme colour, so the file picker
    doesn't pull Breeze icons from the system icon theme."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(color, max(1.4, rect.height() / 11))
    pen.setJoinStyle(Qt.RoundJoin)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    s = min(rect.width(), rect.height())
    r = QRectF(rect.center().x() - s / 2, rect.center().y() - s / 2, s, s).adjusted(s * .08, s * .12, -s * .08, -s * .08)
    x, y, w, h = r.x(), r.y(), r.width(), r.height()
    if name in ("folder", "folder-up", "home", "drive"):
        if name == "home":
            path = QPainterPath(QPointF(x, y + h * .45))
            path.lineTo(x + w / 2, y)
            path.lineTo(x + w, y + h * .45)
            p.drawPath(path)
            p.drawRect(QRectF(x + w * .15, y + h * .4, w * .7, h * .6))
            p.drawRect(QRectF(x + w * .42, y + h * .65, w * .16, h * .35))
        elif name == "drive":
            p.drawRoundedRect(QRectF(x, y + h * .3, w, h * .55), 3, 3)
            p.drawPoint(QPointF(x + w * .8, y + h * .58))
        else:
            fill = QColor(color)
            fill.setAlphaF(0.22)
            path = QPainterPath(QPointF(x, y + h))
            path.lineTo(x, y + h * .12)
            path.lineTo(x + w * .38, y + h * .12)
            path.lineTo(x + w * .48, y + h * .28)
            path.lineTo(x + w, y + h * .28)
            path.lineTo(x + w, y + h)
            path.closeSubpath()
            p.setBrush(fill)
            p.drawPath(path)
            if name == "folder-up":
                p.drawLine(QPointF(x + w / 2, y + h * .9), QPointF(x + w / 2, y + h * .45))
                p.drawLine(QPointF(x + w * .35, y + h * .6), QPointF(x + w / 2, y + h * .45))
                p.drawLine(QPointF(x + w * .65, y + h * .6), QPointF(x + w / 2, y + h * .45))
    else:
        # A page with a folded corner, with a mark for the kind of file.
        fold = w * .3
        path = QPainterPath(QPointF(x + w * .12, y))
        path.lineTo(x + w * .88 - fold, y)
        path.lineTo(x + w * .88, y + fold)
        path.lineTo(x + w * .88, y + h)
        path.lineTo(x + w * .12, y + h)
        path.closeSubpath()
        p.drawPath(path)
        cx, cy = x + w / 2, y + h * .62
        m = w * .2
        if name == "video":
            tri = QPainterPath(QPointF(cx - m * .7, cy - m))
            tri.lineTo(cx + m, cy)
            tri.lineTo(cx - m * .7, cy + m)
            tri.closeSubpath()
            p.setBrush(color)
            p.drawPath(tri)
        elif name == "audio":
            p.drawLine(QPointF(cx + m * .6, cy - m * 1.2), QPointF(cx + m * .6, cy + m * .5))
            p.setBrush(color)
            p.drawEllipse(QPointF(cx, cy + m * .55), m * .6, m * .45)
        elif name == "image":
            p.drawEllipse(QPointF(cx - m * .5, cy - m * .6), m * .3, m * .3)
            mt = QPainterPath(QPointF(cx - m * 1.2, cy + m))
            mt.lineTo(cx - m * .2, cy - m * .1)
            mt.lineTo(cx + m * .4, cy + m * .5)
            mt.lineTo(cx + m * .8, cy + m * .1)
            mt.lineTo(cx + m * 1.2, cy + m)
            p.drawPath(mt)
        elif name == "text":
            for i in range(3):
                yy = cy - m + i * m * .9
                p.drawLine(QPointF(cx - m * 1.1, yy), QPointF(cx + m * (1.1 if i < 2 else .3), yy))
    p.restore()


class ThemedItemDelegate(QStyledItemDelegate):
    def __init__(self, view, row_height: int = 30):
        super().__init__(view)
        self._view = view
        self.row_height = row_height
        a = current_style()
        self._appearance = a
        self._theme = Theme(a)

    def sizeHint(self, option, index) -> QSize:
        if is_separator(index):
            return QSize(10, 9)
        font = index.data(Qt.FontRole)
        h = self.row_height
        if font is not None:
            h = max(h, QFontMetrics(font).height() + 10)
        fm = QFontMetrics(font if font is not None else option.font)
        w = fm.horizontalAdvance(str(index.data(Qt.DisplayRole) or "")) + 40
        sec = index.data(ROLE_SECONDARY)
        if sec:
            w += QFontMetrics(option.font).horizontalAdvance(str(sec)) + 24
        return QSize(w, h)

    def paint(self, p: QPainter, option: QStyleOptionViewItem, index) -> None:
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(option.rect).adjusted(2, 1, -2, -1)
        accent = self._theme.accent()
        base_text = QColor(self._appearance.card_text_color)
        if is_separator(index):
            c = QColor(accent)
            c.setAlphaF(0.55)
            p.setPen(QPen(c, 1))
            y = rect.center().y()
            p.drawLine(QPointF(rect.left() + 8, y), QPointF(rect.right() - 8, y))
            p.restore()
            return
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        enabled = bool(index.flags() & Qt.ItemIsEnabled)
        radius = min(8.0, rect.height() / 2) if self._appearance.rounded_corners_enabled else 2.0
        text_color = base_text
        if selected:
            p.fillPath(rounded_rect_path(rect, radius), accent)
            text_color = contrast_text(accent)
        elif hovered and enabled:
            h = QColor(accent)
            h.setAlphaF(0.28)
            p.fillPath(rounded_rect_path(rect, radius), h)
        if not enabled:
            text_color = QColor(text_color)
            text_color.setAlphaF(0.45)
        x = rect.left() + 10
        glyph = index.data(ROLE_GLYPH)
        icon = index.data(Qt.DecorationRole)
        if glyph:
            g = QRectF(x, rect.top() + 4, rect.height() - 8, rect.height() - 8)
            draw_glyph(p, str(glyph), g, text_color)
            x = g.right() + 8
        elif isinstance(icon, QIcon) and not icon.isNull():
            s = int(rect.height() - 8)
            p.drawPixmap(int(x), int(rect.top() + 4), icon.pixmap(s, s))
            x += s + 8
        sec = index.data(ROLE_SECONDARY)
        right = rect.right() - 10
        if sec:
            dim = QColor(text_color)
            dim.setAlphaF(dim.alphaF() * 0.65)
            p.setPen(dim)
            p.setFont(option.font)
            sw = QFontMetrics(option.font).horizontalAdvance(str(sec))
            p.drawText(QRectF(right - sw, rect.top(), sw, rect.height()), Qt.AlignVCenter | Qt.AlignRight, str(sec))
            right -= sw + 16
        font = index.data(Qt.FontRole)
        p.setFont(font if font is not None else option.font)
        p.setPen(text_color)
        text = str(index.data(Qt.DisplayRole) or "")
        elided = QFontMetrics(p.font()).elidedText(text, Qt.ElideMiddle, int(max(10, right - x)))
        p.drawText(QRectF(x, rect.top(), right - x, rect.height()), Qt.AlignVCenter | Qt.AlignLeft, elided)
        p.restore()


class ThemedListWidget(QListWidget):
    def __init__(self, parent=None, row_height: int = 30, background: "QColor | None" = None):
        super().__init__(parent)
        a = current_style()
        self._theme = Theme(a)
        bg = background if background is not None else self._theme.card_background()
        self.setItemDelegate(ThemedItemDelegate(self, row_height))
        self.setVerticalScrollBar(CustomScrollBar(Qt.Vertical, extent=10))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setMouseTracking(True)
        self.setUniformItemSizes(False)
        self.setFrameShape(QListWidget.NoFrame)
        self.setStyleSheet(
            f"QListWidget {{ background: {bg.name()}; border: none; outline: none; padding: 4px; }}")
