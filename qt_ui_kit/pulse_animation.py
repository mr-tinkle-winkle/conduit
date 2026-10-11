"""
Shared press/hover icon-size animation: ease into a smaller icon size
on mouse-down (and back on mouse-up), plus a smaller, separate ease
for a hover-in/hover-out "slight downsize" effect. Used by the sidebar
nav buttons (QToolButton, sizes itself via setIconSize) and the
Library's Local/Uploaded tab icons (QTabBar, sizes itself via
setTabIcon of a rescaled pixmap) -- two different widget types with no
shared base class, but the same press/hover feel wanted for both, so
the animation logic itself lives here instead of being duplicated.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QVariantAnimation, QEasingCurve

PULSE_PRESS_FRACTION = 0.82  # icon shrinks to 82% of full size while held down
PULSE_HOVER_FRACTION = 0.93  # icon shrinks to 93% of full size while merely hovered
PULSE_FULL_FRACTION = 1.0
PULSE_DOWN_MS = 90
PULSE_UP_MS = 160


class PulseAnimator(QObject):
    def __init__(self, get_base_size, apply_size, parent=None):
        """
        get_base_size: () -> int -- the button/tab's current "full" icon
        size. A callback rather than a captured value so it keeps
        tracking window/tab resizes instead of going stale.

        apply_size: (int) -> None -- called on every animation frame
        with the icon size to render at right now.
        """
        super().__init__(parent)
        self._get_base_size = get_base_size
        self._apply_size = apply_size
        self._anim = QVariantAnimation(self)
        self._anim.valueChanged.connect(lambda v: self._apply_size(round(v)))
        # Tracks the fraction-of-base-size we're currently at/animating
        # towards, so a new animation started mid-flight (e.g. a press
        # arriving while a hover-in ease is still running) eases from
        # wherever the icon actually is right now rather than snapping.
        self._current_fraction = PULSE_FULL_FRACTION

    def _animate_to(self, target_fraction: float, duration_ms: int) -> None:
        base = self._get_base_size()
        start_value = base * self._current_fraction
        if self._anim.state() == QVariantAnimation.Running:
            value = self._anim.currentValue()
            if value is not None:
                start_value = value
        self._anim.stop()
        self._anim.setDuration(duration_ms)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.setStartValue(round(start_value))
        self._anim.setEndValue(round(base * target_fraction))
        self._current_fraction = target_fraction
        self._anim.start()

    def press(self) -> None:
        self._animate_to(PULSE_PRESS_FRACTION, PULSE_DOWN_MS)

    def release(self, is_hovered: bool = False) -> None:
        """is_hovered: whether the pointer is still over the widget when
        the button/click is released -- eases back to the hover-sized
        93% rather than all the way to 100% in that case, since the
        hover state is still in effect (hover_leave() will handle the
        rest whenever the pointer actually moves off)."""
        target = PULSE_HOVER_FRACTION if is_hovered else PULSE_FULL_FRACTION
        self._animate_to(target, PULSE_UP_MS)

    def hover_enter(self) -> None:
        self._animate_to(PULSE_HOVER_FRACTION, PULSE_UP_MS)

    def hover_leave(self) -> None:
        self._animate_to(PULSE_FULL_FRACTION, PULSE_UP_MS)
