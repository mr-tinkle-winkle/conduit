"""
Replaces native/KDE QGroupBox chrome (used for every "section" header
in Settings, plus tag-category grouping in the Sort popover's Filters
page) with a custom-painted rounded box and title, matching the rest
of the Afterglow custom-widget system.

Deliberately a drop-in for the exact construction pattern already used
everywhere: `group = CustomGroupBox("Title")` then
`layout = QVBoxLayout(group)` (or QFormLayout, etc.) works completely
unchanged at every call site -- this box applies its own reserved
margins (room at the top for the painted title) to whatever layout
gets attached, both via a setLayout() override AND, as a second,
independent guarantee, again in showEvent() right before it's ever
actually shown (see showEvent's own comment for why the setLayout()
path alone isn't fully reliable). `widget.setContentsMargins()` called
BEFORE a layout exists does NOT carry over to a layout attached
later -- confirmed directly (a widget's contentsMargins set pre-layout
has no effect; a newly-attached layout falls back to the current
style's own default margins instead). That wrong assumption in an
earlier version of this class is exactly what caused a real reported
bug: every CustomGroupBox was actually laying out its content with
the STYLE's default margins the whole time, not the title-reserving
ones this class was written to use -- meaning the reserved space above
the title never actually existed, misaligning what this box's OWN
paintEvent assumed versus what its content actually needed, and
throwing off any code (like the Sort popover's own page-sizing) that
relied on this box's reported sizeHint being consistent with its real
rendered content.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QColor
from PySide6.QtWidgets import QWidget, QVBoxLayout

from .style import current_style
from .rounded_rect import rounded_rect_path
from .theme import Theme

_TITLE_HEIGHT = 26


class CustomGroupBox(QWidget):
    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self._title = title
        appearance = current_style()
        self._appearance = appearance
        self._theme = Theme(appearance)

    def make_layout(self, layout_cls=QVBoxLayout):
        """Constructs a layout of the given class, attaches it to this
        box, and immediately applies the correct title-reserving
        margins -- use THIS instead of calling `layout_cls(self)`
        directly. PySide6 does not reliably invoke a Python-side
        setLayout() override from that constructor's own internal
        attachment call (confirmed directly -- a subclass's setLayout()
        override, with a print statement added specifically to check,
        never actually printed when a layout was attached this way),
        which is what an earlier version of this class relied on to
        apply these margins automatically. showEvent() below is a
        second, independent attempt at the same fix for anything that
        still constructs a layout the old way, but it only helps once
        this box is actually SHOWN -- code that queries this box's
        sizeHint() before ever showing it (the Sort popover's own page-
        sizing, which computes a page's needed height immediately after
        building it, well before any show() call) would still see the
        wrong, un-reserved size at exactly the moment that matters most.
        This factory method is the actually-reliable fix; showEvent()
        stays as a defensive fallback, not the primary mechanism."""
        layout = layout_cls(self)
        self._apply_layout_margins()
        return layout

    def setLayout(self, layout) -> None:
        super().setLayout(layout)
        self._apply_layout_margins()

    def showEvent(self, event) -> None:
        # BELT AND SUSPENDERS: setLayout() above is the "should" fix,
        # but calling QVBoxLayout(self)/QFormLayout(self) etc. --
        # every call site's own construction pattern -- turned out NOT
        # to reliably invoke a Python-subclass override of setLayout()
        # at all (confirmed directly: the margins it tried to apply
        # simply weren't there afterward), almost certainly because
        # that constructor attaches the layout via an internal C++-side
        # call that PySide's virtual-method dispatch doesn't route
        # through the Python override for. Re-applying here as well,
        # right before this box is ever actually shown, is a second,
        # independent guarantee that doesn't depend on that dispatch
        # working -- cheap and fully idempotent either way.
        super().showEvent(event)
        self._apply_layout_margins()

    def _apply_layout_margins(self) -> None:
        lay = self.layout()
        if lay is not None:
            lay.setContentsMargins(12, _TITLE_HEIGHT + 6, 12, 12)

    def setTitle(self, title: str) -> None:
        self._title = title
        self.update()

    def title(self) -> str:
        return self._title

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = QRectF(self.rect()).adjusted(1, _TITLE_HEIGHT / 2, -1, -1)
        radius = self._appearance.rounded_corner_radius if self._appearance.rounded_corners_enabled else 8
        radius = min(radius, rect.height() / 2, rect.width() / 2) if radius else 0
        path = rounded_rect_path(rect, radius) if radius else None

        pen = painter.pen()
        pen.setColor(self._theme.border())
        pen.setWidthF(2)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        if path is not None:
            painter.drawPath(path)
        else:
            painter.drawRect(rect)

        if self._title:
            # A small punched-out gap behind the title text, same idea
            # as a native QGroupBox's title notch in its own top border
            # -- filled with the WINDOW/page background (approximated
            # here as the parent widget's own palette window color,
            # since these boxes sit directly on a plain settings page)
            # so the border line doesn't visibly run behind the text.
            font = painter.font()
            font.setBold(True)
            painter.setFont(font)
            from PySide6.QtGui import QFontMetrics
            fm = QFontMetrics(font)
            text_width = fm.horizontalAdvance(self._title)
            gap_rect = QRectF(rect.x() + 8, 0, text_width + 8, _TITLE_HEIGHT)
            bg = self.palette().window().color()
            painter.fillRect(gap_rect, bg)
            painter.setPen(QColor(self._appearance.card_text_color))
            painter.drawText(gap_rect.adjusted(4, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, self._title)
        painter.end()
