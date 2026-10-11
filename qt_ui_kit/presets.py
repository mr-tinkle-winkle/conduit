"""Ready-made styles.  ``kit.set_style(kit.presets.light())`` etc."""
from __future__ import annotations

from .style import Style


def dark() -> Style:
    """The default: black and white (black surfaces, white text, grays between)."""
    return Style()


def light() -> Style:
    """White surfaces, black text."""
    return Style(window="#ffffff", base="#f5f5f5", surface="#e9e9e9", accent="#d4d4d4", secondary="#b8b8b8",
                 border="#9a9a9a", outline="#111111", text="#000000", text_outline="#ffffff")


def afterglow() -> Style:
    """afterglow's own colours (the app this kit was extracted from): deep blues, light-blue text."""
    return Style(window="#0d1621", base="#050f18", surface="#091e37", accent="#152c4f", secondary="#0c8ea0",
                 border="#152c4f", outline="#152c4f", text="#9bcbff", text_outline="#24466d", text_outline_width=0.0,
                 corner_radius=24, padding=14)


def system() -> Style:
    """Custom-painted widgets, but coloured from the live desktop (Qt / KDE) palette."""
    return Style(fixed_colors=False)
