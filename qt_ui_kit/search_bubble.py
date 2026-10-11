"""
The search field, redesigned per the direct correction: not a text
box that opens to the SIDE of the Search button (the old
_toggle_search_visibility placeholder), but a small popup that appears
UNDERNEATH the button and visually attaches to it with a little tail,
like a comic speech bubble. Outline color is the button/accent color;
fill color is the video card background color.

Revised again this round, per three more direct corrections:
1. The outline used to visibly "continue through" the tail as if the
   tail and body had two separate outlines, rather than reading as one
   continuous bubble outline -- caused by the tail's flat base sitting
   exactly COINCIDENT with the body's top edge (touching, not
   overlapping). QPainterPath.united()'s boolean-op result can leave a
   stray seam exactly where two shapes only touch at a shared edge
   rather than genuinely overlapping, since that edge doesn't fully
   resolve into either shape's interior. Fixed by extending the tail's
   base _TAIL_OVERLAP px past the body's top edge, into the body's own
   interior -- the seam then sits fully inside the united region and
   never becomes part of the outline Qt actually strokes.
2. The tail is now a curved, rounded shape (built from two cubic
   Beziers rather than three straight polygon edges) and wider at its
   base (_TAIL_WIDTH), so it reads as flowing into the bubble rather
   than a sharp triangular point stuck on top of it.
3. The bubble now centers itself directly under the anchor button
   (not left-aligned to it, which put a wide bubble mostly to one
   side) -- the tail sits at the bubble's horizontal center, which is
   therefore always aligned under the button too.

Qt.Popup gives the auto-close-on-outside-click behavior for free (a
mouse press anywhere outside the popup's geometry closes it) -- no
separate "click elsewhere" handling needed.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, QPoint, QRect, Signal
from PySide6.QtGui import QPainter, QPainterPath
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QLayout

from .style import current_style
from .rounded_rect import rounded_rect_path
from .theme import Theme, contrast_text
from .custom_checkbox import CustomCheckBox
from .scale_reveal import animate_popup_from_point

_TAIL_HEIGHT = 20   # visible height of the tail above the body's top edge
_TAIL_OVERLAP = 8   # how far the tail's base extends PAST that edge, into the body -- see module docstring, point 1
_TAIL_WIDTH = 44
_TAIL_GAP = 6        # blank space between the anchor button and the tail's own tip -- "float just a bit off" the button
_WIDTH = 260
_BODY_HEIGHT = 44


class SearchBubble(QWidget):
    # Emitted when the person actually wants to run the search --
    # pressing Enter in the field, or clicking the confirm checkbox
    # next to it -- NOT on every keystroke. Typing alone only updates
    # what's visibly in the box; LibraryPage reads line_edit.text()
    # itself when this fires, rather than this signal carrying the text.
    search_confirmed = Signal()

    def __init__(self, parent=None, placeholder: str = "Search\u2026"):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        appearance = current_style()
        self._theme = Theme(appearance)
        self._radius = appearance.rounded_corner_radius if appearance.rounded_corners_enabled else 12
        self._tail_x = _WIDTH // 2  # bubble is always centered under its anchor -- see show_below()

        # resize(), NOT setFixedSize() -- a hard fixed size clamps any
        # later setGeometry() call straight back to it immediately,
        # which silently defeated the whole point of
        # animate_popup_from_point()'s small-starting-rect: the widget
        # was never actually able to render smaller than its final
        # size, so the "grow" animation only ever showed the (barely
        # visible, offscreen-QPA-dependent) opacity fade, not real
        # growth. Nothing else lays this widget out -- it's a
        # standalone top-level popup -- so there's nothing that
        # actually needs a hard size floor/ceiling here.
        self.resize(_WIDTH, _TAIL_HEIGHT + _BODY_HEIGHT)

        row = QHBoxLayout(self)
        row.setContentsMargins(14, _TAIL_HEIGHT + 8, 14, 8)
        row.setSpacing(8)
        # Without this, Qt auto-computes a minimumSize for the whole
        # widget from the layout's own children (the line edit +
        # checkbox both have real minimum-size hints), and clamps any
        # setGeometry() call smaller than that straight back up to it
        # -- the SAME bug setFixedSize() caused above (see this
        # widget's resize() comment), just coming from the layout's
        # own automatic constraint instead of an explicit one. This is
        # safe here specifically because show_below() always sets an
        # exact final geometry itself; nothing relies on the layout's
        # own size negotiation to pick this widget's size.
        row.setSizeConstraint(QLayout.SetNoConstraint)

        self.line_edit = QLineEdit(self)
        self.line_edit.setPlaceholderText(placeholder)
        text_color = contrast_text(self._theme.card_background()).name()
        # Scoped to QLineEdit specifically (not a bare "background-color:
        # ..." with no type selector) -- deliberately avoiding the exact
        # unscoped-stylesheet-cascade mistake documented in HANDOFF.md;
        # this selector only ever matches this one widget anyway.
        self.line_edit.setStyleSheet(
            f"QLineEdit {{ background: transparent; border: none; color: {text_color}; }}"
        )
        row.addWidget(self.line_edit, stretch=1)

        self.confirm_checkbox = CustomCheckBox()
        self.confirm_checkbox.setToolTip("Search")
        self.confirm_checkbox.toggled.connect(lambda _checked: self.search_confirmed.emit())
        row.addWidget(self.confirm_checkbox)

        # Search no longer runs live-as-you-type -- only on Enter or the
        # confirm checkbox above, per the direct instruction (typing
        # alone used to trigger _do_refresh on every keystroke via a
        # chain this widget doesn't own; that wiring now waits for one
        # of these two signals instead -- see LibraryPage). Enter now
        # TOGGLES the checkbox itself (rather than emitting
        # search_confirmed directly) so the checkbox's own visible
        # state always reflects the last thing that happened, whether
        # that was a click or Enter -- toggled() (not clicked()) is
        # what emits search_confirmed, since toggle() changes the
        # checked state programmatically rather than via a real click,
        # and only toggled() fires for both.
        self.line_edit.returnPressed.connect(self.confirm_checkbox.toggle)

    def _build_path(self) -> QPainterPath:
        body_rect = QRectF(0, _TAIL_HEIGHT, _WIDTH, _BODY_HEIGHT)
        radius = min(self._radius, body_rect.height() / 2, body_rect.width() / 2)
        body_path = rounded_rect_path(body_rect, radius)

        base_y = _TAIL_HEIGHT + _TAIL_OVERLAP  # inside the body -- see module docstring, point 1
        peak_y = 0.0
        base_left = self._tail_x - _TAIL_WIDTH / 2
        base_right = self._tail_x + _TAIL_WIDTH / 2
        # A genuine POINT at the top now (not a small rounded/flat tip
        # like before) -- per the direct correction, the tail should
        # "reach a point where it ends at the top."
        #
        # THE HORIZONTAL-TANGENT POINT MUST BE AT THE VISIBLE BOUNDARY
        # (y=_TAIL_HEIGHT), NOT DEEPER INSIDE THE BODY. An earlier
        # version put it at base_y (_TAIL_OVERLAP px below that, inside
        # the body, for the seamless-outline-union trick) -- which
        # meant the ACTUALLY VISIBLE part of the curve (everything
        # above y=_TAIL_HEIGHT; the overlap portion is hidden inside
        # the body's own fill) never reached a horizontal tangent at
        # all, since that only happened deeper in, off-screen. The
        # curve's real on-screen edge met the body's flat top edge at
        # whatever slope it happened to have at y=_TAIL_HEIGHT --
        # visibly a harsh, un-blended cutoff, reported directly.
        #
        # Fixed by splitting each side into two pieces: a cubic from
        # the peak down to (base_x, _TAIL_HEIGHT) -- the real visible
        # boundary -- whose control point AT that endpoint shares its
        # exact y, giving a true horizontal tangent exactly where the
        # curve actually meets the body's flat top edge; then a plain
        # straight lineTo extending _TAIL_OVERLAP px further down to
        # base_y, entirely hidden inside the body, purely to keep the
        # genuine-overlap fix for the outline seam (see point 1 above)
        # -- its shape doesn't matter since nothing of it is ever seen.
        flare = _TAIL_WIDTH * 0.35
        arc_reach = (_TAIL_HEIGHT - peak_y) * 0.35

        tail_path = QPainterPath()
        tail_path.moveTo(base_left, base_y)
        tail_path.lineTo(base_left, _TAIL_HEIGHT)  # hidden -- straight, inside the body
        tail_path.cubicTo(
            base_left + flare, _TAIL_HEIGHT,
            self._tail_x, peak_y + arc_reach,
            self._tail_x, peak_y,
        )
        tail_path.cubicTo(
            self._tail_x, peak_y + arc_reach,
            base_right - flare, _TAIL_HEIGHT,
            base_right, _TAIL_HEIGHT,
        )
        tail_path.lineTo(base_right, base_y)  # hidden -- straight, inside the body
        tail_path.lineTo(base_left, base_y)
        tail_path.closeSubpath()

        return body_path.united(tail_path)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        combined = self._build_path()
        painter.fillPath(combined, self._theme.card_background())
        pen = painter.pen()
        pen.setColor(self._theme.outline())
        pen.setWidthF(2)
        painter.setPen(pen)
        painter.drawPath(combined)
        painter.end()

    def show_below(self, anchor: QWidget) -> None:
        """Grows out of `anchor`'s own icon rather than just appearing
        -- per the direct request, same "resize out of the icon"
        treatment as SortPopover now has. Still centered under the
        button with the tail floating _TAIL_GAP px clear of it (both
        unchanged from before) -- animate_popup_from_point just
        computes a small starting rect near the anchor's own center
        and animates geometry + opacity from there up to this final
        position/size, rather than jumping straight to it."""
        anchor_global = anchor.mapToGlobal(QPoint(0, anchor.height()))
        center_x = anchor_global.x() + anchor.width() // 2
        final_geometry = QRect(center_x - _WIDTH // 2, anchor_global.y() + _TAIL_GAP, self.width(), self.height())
        origin = anchor.mapToGlobal(anchor.rect().center())
        animate_popup_from_point(self, origin, final_geometry)
        self.line_edit.setFocus()
        self.update()
