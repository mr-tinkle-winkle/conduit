"""
A single scale factor derived from the window's current size, used to
scale UI elements (trim timeline, volume bar, sidebar icons, card fonts)
up or down together as the window is resized.

Reference size is a typical fullscreen resolution -- "the current scale
is great for fullscreen" was the starting point, so 1920x1080 is treated
as the 1.0x baseline that all the existing hardcoded pixel sizes were
already tuned against, and everything scales relative to that rather than
to some arbitrary smaller default window size.
"""
from __future__ import annotations

REFERENCE_WIDTH = 1920
REFERENCE_HEIGHT = 1080

MIN_SCALE = 0.5
MAX_SCALE = 2.0


def compute_scale(width: int, height: int) -> float:
    if width <= 0 or height <= 0:
        return 1.0
    factor = min(width / REFERENCE_WIDTH, height / REFERENCE_HEIGHT)
    return max(MIN_SCALE, min(MAX_SCALE, factor))
