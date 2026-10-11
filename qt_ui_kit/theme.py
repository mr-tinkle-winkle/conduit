"""
Colour lookup for painting code.  ``Theme()`` reads the current kit style (see style.py); every
custom-painted widget asks it for colours instead of hard-coding them, so a style change -- or a
later gradient / texture pass -- only touches the style, never the widgets.

With ``style.fixed_colors`` off, the roles come from the live QApplication palette instead
(window -> Window, base -> Base, surface -> AlternateBase, accent -> Highlight, secondary -> Mid,
text -> WindowText).
"""
from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from .style import Style, current_style


def contrast_text(bg: QColor) -> QColor:
    """Near-black or near-white, whichever reads better on ``bg`` (perceptual luminance)."""
    luminance = 0.299 * bg.red() + 0.587 * bg.green() + 0.114 * bg.blue()
    return QColor(20, 20, 20) if luminance > 140 else QColor(240, 240, 240)


class Theme:
    """Cheap to construct: build one where you paint (``Theme()``), never cache it across a style change."""

    def __init__(self, style: "Style | None" = None):
        self._style = style or current_style()

    @property
    def style(self) -> Style:
        return self._style

    @property
    def custom_buttons_enabled(self) -> bool:
        return self._style.custom_paint

    @property
    def afterglow_enabled(self) -> bool:          # (afterglow-era name) fixed colours vs live palette
        return self._style.fixed_colors

    @property
    def rounded_corners_enabled(self) -> bool:
        return self._style.rounded_corners

    @property
    def corner_radius(self) -> int:
        return self._style.corner_radius if self._style.rounded_corners else 0

    def _pick(self, hex_value: str, role) -> QColor:
        if self._style.fixed_colors:
            return QColor(hex_value)
        return QApplication.palette().color(role)

    # ---- roles
    def window(self) -> QColor:
        return self._pick(self._style.window, QPalette.Window)

    def base(self) -> QColor:
        return self._pick(self._style.base, QPalette.Base)

    def surface(self) -> QColor:
        return self._pick(self._style.surface, QPalette.AlternateBase)

    def accent(self) -> QColor:
        return self._pick(self._style.accent, QPalette.Highlight)

    def secondary(self) -> QColor:
        return self._pick(self._style.secondary, QPalette.Mid)

    def border(self) -> QColor:
        return self._pick(self._style.border, QPalette.Mid)

    def outline(self) -> QColor:
        return self._pick(self._style.outline, QPalette.WindowText)

    def text(self) -> QColor:
        return self._pick(self._style.text, QPalette.WindowText)

    def danger(self) -> QColor:
        return QColor(self._style.danger)

    def highlight(self) -> QColor:
        """Navigation buttons' fill (SegmentButton) -- the secondary role."""
        return self.secondary()

    def button_color(self) -> QColor:
        return self.accent()

    def button_text_color(self) -> QColor:
        return contrast_text(self.button_color())

    # ---- afterglow-era names
    def card_background(self) -> QColor:
        return self.surface()

    def app_background(self) -> QColor:
        return self.window()

    def library_background(self) -> QColor:
        return self.base()

    def turquoise(self) -> QColor:
        return self.secondary()
