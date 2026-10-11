"""
A rounded, theme-colored button replacing native/KDE-styled QToolButtons
-- the "Custom Buttons" setting's actual implementation. Used for the
Library's Search/Refresh/Filters/Sort By/Info row for now; the Local/
Uploaded page switcher is a separate, bigger piece of work still using
the native QTabBar (see HANDOFF.md).

Subclasses QToolButton (not QPushButton) specifically so existing
QToolButton-only features -- setPopupMode(InstantPopup) + setMenu(),
already used by Filters/Sort By/Info -- keep working completely
unchanged; only the PAINTING is replaced, not the click/popup behavior.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, QEvent
from PySide6.QtGui import QPainter, QColor
from PySide6.QtWidgets import QToolButton

from .style import current_style
from .press_pulse import PressPulse, scaled_cached
from .rounded_rect import rounded_rect_path
from .theme import Theme, contrast_text


class CustomButton(QToolButton):
    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self.setText(text)
        self.setCursor(Qt.PointingHandCursor)
        appearance = current_style()
        self._appearance = appearance
        self._theme = Theme(appearance)
        self._icon_pixmap = None  # QPixmap | None -- drawn instead of text when set
        self._circular = False
        self._fill_override: "QColor | None" = None
        self._outline_override: "QColor | None" = None
        self._outline_width = 0.0
        # Hover-shrink / press-pulse -- shared with every other custom
        # button in the app, see press_pulse.py.
        self._pulse = PressPulse(self)

    def set_fill_color(self, color) -> None:
        """Override this button's fill instead of the theme's own
        button_color() -- used for the video-card action buttons
        (Edit/Copy/Filters/Delete), which take on the card TEXT color
        rather than the standard accent fill, per the direct
        request. Pass None to go back to the theme default."""
        self._fill_override = QColor(color) if color is not None else None
        self.update()

    def set_outline(self, color, width: float) -> None:
        """An optional stroked outline around the button, in addition
        to its fill -- again for the video-card action buttons, which
        get an outline "similar to the text" (i.e. using the same
        color/idea as OutlinedLabel's own outline). width <= 0 means
        no outline (the default for every other CustomButton)."""
        self._outline_override = QColor(color) if color is not None else None
        self._outline_width = width
        self.update()

    def set_icon_pixmap(self, pixmap) -> None:
        """Draw `pixmap` (scaled, centered, with a small margin) instead
        of the button's text label. Pass None to go back to text.
        Distinct from QToolButton's own setIcon()/setIconSize() -- this
        button fully replaces native painting (see paintEvent's own
        comment), so a plain setIcon() call would have nothing left to
        actually render it."""
        self._icon_pixmap = pixmap
        self.update()

    def set_circular(self, diameter: int) -> None:
        """Force this button into a perfect circle of the given
        diameter, regardless of the Afterglow Theme rounded-corner
        radius setting -- used for the Library header's Search/
        Refresh/Sort buttons specifically, per the direct request
        for those three (not a general CustomButton shape option)."""
        self._circular = True
        self.setFixedSize(diameter, diameter)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        rect = QRectF(self.rect())

        # Pulse scale around the center, before anything is drawn, so
        # fill/outline/icon/text all shrink and grow together.
        self._pulse.apply(painter)
        # Disabled buttons used to paint exactly like enabled ones, so e.g.
        # the previewer's arrows at the end of a list, or Undo Edits on an
        # unedited clip, looked clickable. Dim the whole button instead.
        if not self.isEnabled():
            painter.setOpacity(0.4)

        if self._circular:
            # A perfect circle regardless of the theme's own corner-
            # radius setting -- width == height by construction
            # (set_circular() uses setFixedSize with equal sides), so
            # half of either dimension is the correct circle radius.
            radius = rect.height() / 2
        else:
            radius = self._appearance.rounded_corner_radius if self._appearance.rounded_corners_enabled else 0
            # Clamp to half the button's own (usually short) height -- a
            # small button fully rounded into a pill shape at the default
            # 24px radius is fine, but rounded_rect_path's own half-of-
            # smaller-dimension clamp already handles this; being explicit
            # here just keeps a very short button from ever wanting a
            # radius bigger than its own height in the first place.
            radius = min(radius, rect.height() / 2) if radius else 0

        bg = self._fill_override if self._fill_override is not None else self._theme.button_color()
        if self.isDown():
            bg = bg.darker(125)
        elif self.underMouse():
            bg = bg.lighter(115)

        if radius:
            painter.setClipPath(rounded_rect_path(rect, radius))
        painter.fillRect(self.rect(), bg)
        painter.setClipping(False)

        if self._outline_override is not None and self._outline_width > 0:
            # Traced on the EXACT SAME rect + radius as the fill above --
            # not an inset copy with a separately-reduced radius, which
            # is what this used to do and was the actual cause of the
            # outline looking "inconsistent around itself": insetting
            # the rect AND shrinking the radius by the same amount
            # doesn't scale a rounded rectangle's corner arc the same
            # way it scales a straight edge, so the two rounded shapes
            # (fill's and outline's) weren't concentric -- the gap
            # between them visibly widened or narrowed right at each
            # corner. Qt strokes a path CENTERED on its own geometry by
            # default, so tracing the identical path the fill already
            # uses gives a uniform-width border everywhere, corners
            # included; the pen's outer half simply has no widget area
            # left to draw into and is invisible, which is the desired
            # look anyway (the border reads as fully inside the
            # button), not a distortion of its shape.
            pen = painter.pen()
            pen.setColor(self._outline_override)
            pen.setWidthF(self._outline_width)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            if radius:
                painter.drawPath(rounded_rect_path(rect, radius))
            else:
                painter.drawRect(rect)

        if self._icon_pixmap is not None and not self._icon_pixmap.isNull():
            margin = max(3, round(min(self.width(), self.height()) * 0.14))
            target = self.rect().adjusted(margin, margin, -margin, -margin)
            scaled = scaled_cached(self._icon_pixmap, target.width(), target.height())
            x = target.x() + (target.width() - scaled.width()) // 2
            y = target.y() + (target.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        else:
            # Contrast against the ACTUAL fill in use (which may be the
            # override above, not always button_color()) -- otherwise a
            # fill override like the action buttons' card-text-color
            # could land on text that reads fine against the theme's
            # normal accent but not against this button's own fill.
            painter.setPen(contrast_text(bg))
            painter.drawText(self.rect(), Qt.AlignCenter, self.text())
        painter.end()
        # Deliberately NOT calling super().paintEvent() -- this fully
        # replaces QToolButton's native/KDE-styled rendering rather than
        # layering on top of it (the whole point of "Custom Buttons").

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.EnabledChange:
            self.update()

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        self.update()
