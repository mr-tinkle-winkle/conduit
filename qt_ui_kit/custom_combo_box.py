"""
A QComboBox that draws itself and opens its own dropdown instead of
the native/KDE one. It keeps the whole QComboBox API (addItem,
currentIndex, currentData, findData, insertSeparator, setItemData with
Qt.FontRole, the activated/currentIndexChanged signals, editable mode)
so call sites only swap the class name; only the painting and the popup
are replaced.

The box is the same rounded, accent-outlined card-coloured field as
CustomLineEdit, with a drawn chevron. The dropdown is a frameless
rounded popup holding a ThemedListWidget (accent highlight, hover,
per-row fonts, separators), placed under the box -- or above it when
there's no room below -- and scrolling past maxVisibleItems().
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, QPoint, QPointF, QTimer, QEvent, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QFontMetrics, QGuiApplication, QIcon
from PySide6.QtWidgets import QAbstractItemView, QComboBox, QFrame, QVBoxLayout, QListWidgetItem

from .style import current_style
from .rounded_rect import rounded_rect_path
from .theme import Theme, contrast_text
from .themed_list import ThemedListWidget, is_separator


class _ComboPopup(QFrame):
    def __init__(self, combo: "CustomComboBox"):
        super().__init__(combo, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self._combo = combo
        a = current_style()
        self._appearance = a
        self._theme = Theme(a)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.list = ThemedListWidget(self, row_height=28, background=self._theme.card_background())
        lay.addWidget(self.list)
        model = combo.model()
        for i in range(combo.count()):
            idx = model.index(i, combo.modelColumn(), combo.rootModelIndex())
            it = QListWidgetItem(str(idx.data(Qt.DisplayRole) or ""))
            for role in (Qt.FontRole, Qt.DecorationRole, Qt.AccessibleDescriptionRole, Qt.ToolTipRole):
                v = idx.data(role)
                if v is not None:
                    it.setData(role, v)
            flags = model.flags(idx)
            if is_separator(idx) or not (flags & Qt.ItemIsEnabled):
                it.setFlags(it.flags() & ~(Qt.ItemIsEnabled | Qt.ItemIsSelectable))
            it.setData(Qt.UserRole + 200, i)
            self.list.addItem(it)
        cur = combo.currentIndex()
        if 0 <= cur < self.list.count():
            self.list.setCurrentRow(cur)
        self.list.itemClicked.connect(self._choose)
        self.list.itemActivated.connect(self._choose)
        self.list.itemEntered.connect(self._hovered)

    def _hovered(self, item) -> None:
        if item.flags() & Qt.ItemIsEnabled:
            self._combo.itemHovered.emit(item.data(Qt.UserRole + 200))

    def _choose(self, item) -> None:
        if not (item.flags() & Qt.ItemIsEnabled):
            return
        i = item.data(Qt.UserRole + 200)
        self.close()
        self._combo._pick(i)

    def closeEvent(self, event) -> None:
        self._combo._popup_closed()
        super().closeEvent(event)

    def mousePressEvent(self, event) -> None:
        # A click on the combo box itself while open just closes the
        # popup; without NoMouseReplay Qt would hand that click on to the
        # box, which would open it straight back up.
        gp = event.globalPosition().toPoint()
        if not self.rect().contains(event.position().toPoint()):
            box = self._combo
            if box.rect().contains(box.mapFromGlobal(gp)):
                self.setAttribute(Qt.WA_NoMouseReplay, True)
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        radius = min(self._appearance.rounded_corner_radius, 12) if self._appearance.rounded_corners_enabled else 4
        path = rounded_rect_path(r, radius)
        p.fillPath(path, self._theme.card_background())
        p.setPen(QPen(self._theme.outline(), 1.5))
        p.drawPath(path)
        p.end()

    def place(self) -> None:
        combo = self._combo
        rows = 0
        height = 8
        visible = max(1, combo.maxVisibleItems())
        for i in range(self.list.count()):
            if rows >= visible:
                break
            height += self.list.sizeHintForRow(i)
            rows += 1
        height += 8
        width = max(combo.width(), min(520, self.list.sizeHintForColumn(0) + 30))
        screen = combo.screen() or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        below = combo.mapToGlobal(QPoint(0, combo.height() + 2))
        above_y = combo.mapToGlobal(QPoint(0, 0)).y() - height - 2
        y = below.y()
        if y + height > avail.bottom() and above_y >= avail.top():
            y = above_y
        height = min(height, avail.height() - 20)
        x = min(max(below.x(), avail.left()), avail.right() - width)
        self.setGeometry(x, y, width, height)
        cur = self.list.currentRow()
        if cur >= 0:
            self.list.scrollToItem(self.list.item(cur), QAbstractItemView.PositionAtCenter)


class CustomComboBox(QComboBox):
    # the row under the mouse while the dropdown is open (a live preview can follow it), and
    # the dropdown closing (the preview goes back to the committed value)
    itemHovered = Signal(int)
    popupHidden = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        a = current_style()
        self._appearance = a
        self._theme = Theme(a)
        self._popup: "_ComboPopup | None" = None
        self._hover = False
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)

    def setEditable(self, editable: bool) -> None:
        super().setEditable(editable)
        le = self.lineEdit()
        if le is not None:
            text = contrast_text(self._theme.card_background()).name()
            le.setStyleSheet(f"QLineEdit {{ background: transparent; border: none; color: {text}; "
                             f"selection-background-color: {self._theme.accent().name()}; }}")
            le.setCursor(Qt.IBeamCursor)

    # --- popup ---------------------------------------------------------
    def showPopup(self) -> None:
        if self._popup is not None or self.count() == 0:
            return
        self._popup = _ComboPopup(self)
        self._popup.place()
        self._popup.show()
        self._popup.list.setFocus()
        self.update()

    def hidePopup(self) -> None:
        if self._popup is not None:
            self._popup.close()

    def _popup_closed(self) -> None:
        self._popup = None
        self.update()
        self.popupHidden.emit()

    def popup_widget(self) -> "_ComboPopup | None":
        return self._popup

    def _pick(self, i: int) -> None:
        # setCurrentIndex() gives currentIndexChanged/currentTextChanged;
        # activated/textActivated are the "user picked it" signals.
        self.setCurrentIndex(i)
        self.activated.emit(i)
        self.textActivated.emit(self.itemText(i))

    # --- painting -------------------------------------------------------
    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        icon = QIcon()
        font = None
        if not self.isEditable() and self.currentIndex() >= 0:
            icon = self.itemIcon(self.currentIndex())
            font = self.itemData(self.currentIndex(), Qt.FontRole)
        paint_dropdown_field(p, self, None if self.isEditable() else self.currentText(),
                             open_=self._popup is not None, hover=self._hover,
                             focused=self.hasFocus(), icon=icon, font_family=font.family() if font else None,
                             icon_size=self.iconSize().height())
        p.end()

    def sizeHint(self):
        s = super().sizeHint()
        s.setHeight(max(s.height(), 30))
        return s

    def minimumSizeHint(self):
        s = super().minimumSizeHint()
        s.setHeight(max(s.height(), 30))
        return s


def paint_dropdown_field(p: QPainter, w, text: "str | None", open_: bool = False, hover: bool = False,
                         focused: bool = False, icon: "QIcon | None" = None,
                         font_family: "str | None" = None, icon_size: int = 16) -> None:
    """The closed look shared by CustomComboBox and the other dropdown
    buttons (Settings > Filters' multi-filter picker): a rounded
    card-coloured field with an accent outline, the current text, and a
    chevron that flips while the dropdown is open. text=None skips the
    text (an editable combo draws its own line edit there)."""
    a = current_style()
    theme = Theme(a)
    p.setRenderHint(QPainter.Antialiasing)
    r = QRectF(w.rect()).adjusted(1, 1, -1, -1)
    radius = a.rounded_corner_radius if a.rounded_corners_enabled else 6
    radius = min(radius, r.height() / 2)
    path = rounded_rect_path(r, radius)
    bg = theme.card_background()
    if hover and w.isEnabled():
        bg = bg.lighter(112) if bg.lightness() < 128 else bg.darker(106)
    p.fillPath(path, bg)
    accent = QColor(theme.accent())
    if not w.isEnabled():
        accent.setAlphaF(0.4)
    p.setPen(QPen(accent, 2 if (focused or open_) else 1.5))
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)
    text_color = contrast_text(theme.card_background())
    if not w.isEnabled():
        text_color.setAlphaF(0.45)
    cw = 10.0
    cx = r.right() - 10 - cw / 2
    cy = r.center().y()
    chev = QPainterPath()
    d = -1 if open_ else 1
    chev.moveTo(cx - cw / 2, cy - d * cw / 4)
    chev.lineTo(cx, cy + d * cw / 4)
    chev.lineTo(cx + cw / 2, cy - d * cw / 4)
    pen = QPen(text_color, 1.8)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.drawPath(chev)
    if text is None:
        return
    x = r.left() + 10
    if icon is not None and not icon.isNull():
        sz = int(min(icon_size, r.height() - 6))
        p.drawPixmap(int(x), int(cy - sz / 2), icon.pixmap(sz, sz))
        x += sz + 6
    f = w.font()
    if font_family:
        f.setFamily(font_family)
    p.setFont(f)
    p.setPen(text_color)
    avail = cx - cw - 4 - x
    shown = QFontMetrics(f).elidedText(text, Qt.ElideRight, int(max(10, avail)))
    p.drawText(QRectF(x, r.top(), avail, r.height()), Qt.AlignVCenter | Qt.AlignLeft, shown)
