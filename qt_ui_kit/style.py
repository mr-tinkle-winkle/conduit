"""
The kit's one settings object: colours, corner rounding, padding, text outline.  Every widget
reads the CURRENT style when it is built / painted (``current_style()``), so an app sets it once at
start-up -- before creating widgets -- and that's the whole theming step:

    import qt_ui_kit as kit
    kit.set_style(kit.Style())                       # the default: dark, black and white
    kit.set_style(kit.Style(accent="#2161bb"))       # or any field overridden
    kit.set_style(kit.presets.afterglow())           # or a named preset

Colour roles (all hex strings):
  window      the app window's background
  base        pages, lists, dialogs, popups
  surface     cards, group boxes, text fields, list rows
  accent      buttons, the selected tab / item, checked boxes
  secondary   a second, quieter accent (tab icon wells, selection highlights)
  border      borders of fields, check boxes, group boxes, frames
  outline     frames of dialogs / popups / tooltips, and focus rings
  text        text and text icons (button text is picked per fill for contrast instead)
  text_outline / text_outline_width   OutlinedLabel's outline (width 0 = none)
  danger      destructive buttons (Delete)

``fixed_colors=False`` samples the live Qt / desktop palette instead of these hex values;
``custom_paint=False`` leaves buttons native (Qt-styled) instead of custom-painted.

The attribute names afterglow used (``afterglow_color_accent``, ``card_text_color``,
``rounded_corner_radius``...) are kept as read-only aliases, so widget code ported from afterglow
works unchanged -- new code should use the plain names.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace


@dataclass
class Style:
    window: str = "#000000"
    base: str = "#0a0a0a"
    surface: str = "#161616"
    accent: str = "#2c2c2c"
    secondary: str = "#484848"
    border: str = "#5c5c5c"
    outline: str = "#f0f0f0"
    text: str = "#ffffff"
    text_outline: str = "#000000"
    text_outline_width: float = 0.0
    danger: str = "#e5484d"
    rounded_corners: bool = True
    corner_radius: int = 12
    padding: int = 10
    custom_paint: bool = True
    fixed_colors: bool = True
    # free-form extras an app wants every widget to be able to read (kept, never interpreted)
    extra: dict = field(default_factory=dict)

    def with_(self, **changes) -> "Style":
        """A copy with some fields changed: ``kit.current_style().with_(accent="#335")``."""
        return replace(self, **changes)

    # ---- afterglow-era names (read-only aliases for ported code) ------------------------------
    @property
    def rounded_corners_enabled(self) -> bool: return self.rounded_corners
    @property
    def rounded_corner_radius(self) -> int: return self.corner_radius
    @property
    def ui_padding(self) -> int: return self.padding
    @property
    def card_text_color(self) -> str: return self.text
    @property
    def card_text_outline_color(self) -> str: return self.text_outline
    @property
    def card_text_outline_width(self) -> float: return self.text_outline_width
    @property
    def custom_buttons_enabled(self) -> bool: return self.custom_paint
    @property
    def afterglow_theme_enabled(self) -> bool: return self.fixed_colors
    @property
    def afterglow_color_accent(self) -> str: return self.accent
    @property
    def afterglow_color_card_background(self) -> str: return self.surface
    @property
    def afterglow_color_app_background(self) -> str: return self.window
    @property
    def afterglow_color_library(self) -> str: return self.base
    @property
    def afterglow_color_turquoise(self) -> str: return self.secondary


_current = Style()
_listeners: list = []


def current_style() -> Style:
    """The style widgets read.  Don't mutate it -- use set_style() / update_style()."""
    return _current


def set_style(style: Style) -> None:
    """Replace the style.  Widgets built after this use it; to restyle widgets that already exist,
    rebuild them (or repaint: most read the style at paint time)."""
    global _current
    _current = copy.deepcopy(style)
    for fn in list(_listeners):
        try:
            fn(_current)
        except Exception:  # noqa: BLE001
            pass


def update_style(**changes) -> Style:
    set_style(_current.with_(**changes))
    return _current


def on_style_changed(fn) -> None:
    """fn(style) after every set_style()."""
    _listeners.append(fn)
