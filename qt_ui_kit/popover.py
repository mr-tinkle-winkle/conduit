"""
Replaces the old combined Filters/Sort By/Info QMenu (three labeled
sections stacked vertically in one dropdown) with an actual custom
popup panel: three horizontally-tiled tabs, each switching a real page
of widgets below rather than scrolling through one long vertical list.

Corner rounding, per the instruction: the two OUTER tabs round only
their own outer top corner (left tab: top-left; right tab: top-right);
the MIDDLE tab has no rounding at all, and no tab rounds a corner that
touches a neighboring tab or the content area below it -- same "don't
round a touching seam" convention already used for the Local/Uploaded
tab icons (see library_page.py's _composite_tab_icon). The content
area below rounds only its own bottom two corners, for the same
reason (its top edge is flush against the tab strip).

Colors come from Theme, same as the rest of the UI Update: the active
tab and the content panel's border use accent(); inactive tabs and the
content panel's fill use card_background() -- "the color scheme as
mentioned" per how this was actually asked for, i.e. the same
Afterglow Theme palette already used for buttons/cards elsewhere,
not a new one-off color choice.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, QPoint, QRect
from PySide6.QtGui import QPainter, QColor
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QStackedWidget, QAbstractButton, QLayout

from .press_pulse import PressPulse
from .style import current_style
from .rounded_rect import rounded_rect_path
from .theme import Theme, contrast_text
from .scale_reveal import animate_popup_from_point


class _PopoverTabButton(QAbstractButton):
    def __init__(self, text: str, position: str, theme: Theme, radius: float, parent=None):
        """position: 'left' | 'middle' | 'right' | 'only' -- decides which
        corner(s) this tab is allowed to round ('only': a single tab, so
        both top corners)."""
        super().__init__(parent)
        self._pulse = PressPulse(self)  # shared hover/press pulse, see press_pulse.py
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self._position = position
        self._theme = theme
        self._radius = radius
        self.setMinimumHeight(32)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        self._pulse.apply(painter)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())

        top_left = self._position in ("left", "only")
        top_right = self._position in ("right", "only")
        # Neighbor tabs touch side-to-side and every tab touches the page
        # box below it: extend those sides so they stay flush at rest
        # despite the pulse headroom (PressPulse.touching_extension).
        ext_x, ext_y = self._pulse.touching_extension(rect)
        rect = rect.adjusted(
            0 if self._position in ("left", "only") else -ext_x, 0,
            0 if self._position in ("right", "only") else ext_x, ext_y,
        )
        radius = min(self._radius, rect.height()) if self._radius else 0

        bg = self._theme.accent() if self.isChecked() else self._theme.card_background()
        if self.underMouse() and not self.isChecked():
            bg = bg.lighter(115)

        if radius:
            path = rounded_rect_path(
                rect, radius,
                top_left=top_left, top_right=top_right,
                bottom_left=False, bottom_right=False,
            )
            painter.fillPath(path, bg)
        else:
            painter.fillRect(rect, bg)

        painter.setPen(contrast_text(bg))
        painter.drawText(self.rect(), Qt.AlignCenter, self.text())
        painter.end()

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)


class _RoundedContentArea(QStackedWidget):
    """The page content sits inside this -- painted with a rounded-
    bottom-corners card (accent-colored outline, card_background fill)
    behind whatever page widget is showing, matching the visual weight
    of a VideoCard's own info box."""

    def __init__(self, theme: Theme, radius: float, parent=None):
        super().__init__(parent)
        self._theme = theme
        self._radius = radius
        self.setAttribute(Qt.WA_StyledBackground, False)

    def sizeHint(self):
        # QStackedWidget's OWN default sizeHint()/minimumSizeHint() is
        # the LARGEST across ALL of its pages, not just the one
        # actually showing -- each page here is already wrapped in its
        # own independently-sized QScrollArea (_wrap_scrollable, in
        # library_page.py), so a short page (few tags in Filters) was
        # still forcing the whole popover to whatever height the
        # TALLEST of the three pages needed, leaving real, visible
        # unfilled space below the shorter page's own content --
        # reported directly ("unnecessary padding... isn't even filled
        # by the elements"). Returning the CURRENT page's own sizeHint
        # instead makes the popover's height track whichever page is
        # actually showing.
        current = self.currentWidget()
        return current.sizeHint() if current is not None else super().sizeHint()

    def minimumSizeHint(self):
        current = self.currentWidget()
        return current.minimumSizeHint() if current is not None else super().minimumSizeHint()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 0, -1, -1)
        radius = min(self._radius, rect.height() / 2, rect.width() / 2) if self._radius else 0
        path = rounded_rect_path(rect, radius, top_left=False, top_right=False)
        painter.fillPath(path, self._theme.card_background())
        pen = painter.pen()
        pen.setColor(self._theme.outline())
        pen.setWidthF(2)
        painter.setPen(pen)
        painter.drawPath(path)
        painter.end()
        super().paintEvent(event)


class TabbedPopover(QWidget):
    """A popup panel that grows out of the button that opened it, with a strip of tabs on top
    (one tab = no visible choice, just the header) and a rounded page below each.  Stays open
    while its contents are used; closes on a click outside (Qt.Popup).

        pop = TabbedPopover(tab_labels=("Filter", "Sort", "View"))
        pop.set_page_widget(0, some_widget) ...
        pop.show_below(button)
    """
    TAB_LABELS = ("Options",)

    def __init__(self, parent=None, tab_labels: "tuple[str, ...] | None" = None, width: int = 320):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self._width = width
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        appearance = current_style()
        theme = Theme(appearance)
        radius = appearance.rounded_corner_radius if appearance.rounded_corners_enabled else 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        # Same fix as SearchBubble's own layout -- see its comment.
        # Without this, Qt clamps any setGeometry() call smaller than
        # the layout's own computed minimum (driven by the tab buttons
        # + stack's own size hints) straight back up to it, defeating
        # animate_popup_from_point()'s small-starting-rect.
        outer.setSizeConstraint(QLayout.SetNoConstraint)

        tab_row = QHBoxLayout()
        tab_row.setContentsMargins(0, 0, 0, 0)
        tab_row.setSpacing(0)
        labels = tuple(tab_labels or self.TAB_LABELS)
        positions = ["only"] if len(labels) == 1 else ["left"] + ["middle"] * (len(labels) - 2) + ["right"]
        self._tab_buttons: list[_PopoverTabButton] = []
        for label, position in zip(labels, positions):
            btn = _PopoverTabButton(label, position, theme, radius)
            btn.clicked.connect(lambda _checked, i=len(self._tab_buttons): self.set_current_index(i))
            tab_row.addWidget(btn, stretch=1)
            self._tab_buttons.append(btn)
        outer.addLayout(tab_row)

        self._stack = _RoundedContentArea(theme, radius)
        outer.addWidget(self._stack)

        # resize(), NOT setFixedWidth() -- a hard fixed width clamps
        # any later setGeometry() call's width straight back to it
        # immediately, which would defeat animate_popup_from_point()'s
        # small-starting-rect the same way it did for SearchBubble
        # (see that class's own comment on this exact bug). show_below()
        # re-asserts this same 320px width after adjustSize() every
        # time it's shown, so the popover's width is still effectively
        # constant -- just not via a hard constraint that would also
        # apply mid-animation.
        self.resize(self._width, self.height())
        self._tab_buttons[0].setChecked(True)

    def set_page_widget(self, index: int, widget: QWidget) -> None:
        """Swap in a freshly-built page widget at `index`, preserving
        whichever page is currently showing -- called on every refresh()
        since the Filters page's content depends on which tags
        currently exist (same reason the old QMenu was rebuilt on every
        refresh too)."""
        current = self._stack.currentIndex()
        old = self._stack.widget(index) if index < self._stack.count() else None
        if old is not None:
            self._stack.removeWidget(old)
            old.deleteLater()
        self._stack.insertWidget(index, widget)
        if 0 <= current < self._stack.count():
            self._stack.setCurrentIndex(current)

    def set_current_index(self, index: int) -> None:
        self._stack.setCurrentIndex(index)
        for i, btn in enumerate(self._tab_buttons):
            btn.setChecked(i == index)
        # Re-sizes the whole popover to the NEWLY-current page's own
        # height immediately -- without this, switching tabs while the
        # popover is already open would leave it at whatever size the
        # PREVIOUSLY-selected page needed until the next time it's
        # reopened, since _RoundedContentArea's sizeHint only gets
        # re-queried when something actually asks for a size update.
        self.adjustSize()
        self.resize(self._width, self.height())

    def show_below(self, anchor: QWidget) -> None:
        """Grows out of `anchor`'s own position rather than just
        appearing -- per the direct request for the Sort popover and
        Settings tab pages both to "come out of their buttons by
        scaling" instead of an instant appearance. adjustSize() first
        (unchanged) to get this popover's real final height from its
        current content, then re-assert the 320px width (adjustSize()
        can change it based on content otherwise -- see __init__'s own
        comment on why width is no longer a hard setFixedWidth
        constraint) before animate_popup_from_point computes the small
        starting rect relative to this final size."""
        self.adjustSize()
        self.resize(self._width, self.height())
        pos = anchor.mapToGlobal(QPoint(0, anchor.height()))
        final_geometry = QRect(pos, self.size())
        origin = anchor.mapToGlobal(anchor.rect().center())
        animate_popup_from_point(self, origin, final_geometry)


# afterglow-era name
SortPopover = TabbedPopover
