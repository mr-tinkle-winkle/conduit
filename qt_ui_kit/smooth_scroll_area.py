"""
QScrollArea's default wheel handling moves the scrollbar in one abrupt
jump per wheel "tick" (Qt applies the whole step, no easing) -- fine for
short lists, but reported as feeling "snappy" on the Library grid, which
can be many rows tall. SmoothScrollArea overrides wheel handling to
animate to the target position instead of jumping there, using an
eased QPropertyAnimation on the vertical scrollbar's own `value`
property so everything else about QScrollArea (resizing, the existing
scrollbar widget, keyboard/drag scrolling) is untouched.

Multiple quick wheel ticks (a fast scroll) accumulate onto the SAME
in-flight animation's end value rather than starting a new animation
each time -- restarting from the current (mid-animation) position would
feel like it's fighting itself; extending the existing target instead
gives one continuous, smoothly-accelerating scroll for a fast swipe.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QEasingCurve, QPropertyAnimation
from PySide6.QtWidgets import QScrollArea

from .custom_scrollbar import CustomScrollBar


class SmoothScrollArea(QScrollArea):
    # How much farther the animation still has to travel, per wheel
    # "tick" of 120 units (Qt's standard notch delta) -- larger feels
    # more responsive to a fast flick, smaller feels gentler.
    _STEP_PER_TICK = 90
    _DURATION_MS = 220

    def __init__(self, parent=None):
        super().__init__(parent)
        self._anim: QPropertyAnimation | None = None
        self._anim_target: int = 0
        # Custom-painted (2x width, accent handle, card_background
        # track) rather than the native KDE-styled bar, per Max's
        # direct request. setVerticalScrollBar takes ownership of the
        # old one and installs this one in its place -- QScrollArea's
        # own scrolling logic (value range, page steps, this class's
        # own wheelEvent animation above) all keep working unchanged
        # since only the BAR WIDGET's painting changed, not the
        # QAbstractSlider machinery underneath it.
        self.setVerticalScrollBar(CustomScrollBar(Qt.Vertical))

    def wheelEvent(self, event) -> None:
        bar = self.verticalScrollBar()
        if bar is None or not bar.isVisible():
            super().wheelEvent(event)
            return

        delta = event.angleDelta().y()
        if delta == 0:
            # Some trackpads report horizontal-only or sub-notch deltas
            # via pixelDelta() instead -- fall back to default handling
            # rather than silently doing nothing.
            super().wheelEvent(event)
            return

        ticks = delta / 120.0
        step = -round(ticks * self._STEP_PER_TICK)

        # Start from wherever the animation is currently headed (if one
        # is already running) rather than the scrollbar's current,
        # not-yet-reached value -- see module docstring.
        base = self._anim_target if self._anim is not None and self._anim.state() == QPropertyAnimation.Running else bar.value()
        target = max(bar.minimum(), min(bar.maximum(), base + step))
        self._animate_to(target)
        event.accept()

    def _animate_to(self, target: int) -> None:
        bar = self.verticalScrollBar()
        if self._anim is not None:
            self._anim.stop()
        self._anim_target = target
        anim = QPropertyAnimation(bar, b"value", self)
        anim.setDuration(self._DURATION_MS)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.setStartValue(bar.value())
        anim.setEndValue(target)
        anim.start()
        self._anim = anim
