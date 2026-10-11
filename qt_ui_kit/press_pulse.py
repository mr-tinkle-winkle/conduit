"""
Shared hover/press "pulse" animation for every custom-painted button in
the app (CustomButton, the sidebar/Local/Uploaded LibraryTabButtons,
custom checkboxes/radio buttons, spinbox arrows, the previewer's
play/volume/fullscreen buttons, ...).

Behavior (all transitions eased, never a hard jump):
- hover: shrink slightly
- mouse held down: shrink further ("pulse down")
- release: bounce up just past normal, then settle ("pulse up, then
  smoothly back to normal"). Settles at the hover size if the cursor is
  still over the widget, or full size if it has left.

Usage -- one line in __init__, one line in paintEvent:

    self._pulse = PressPulse(self)
    ...
    painter = QPainter(self)
    self._pulse.apply(painter)            # scales around the widget center
    # or: self._pulse.apply(painter, some_rect.center())  # around a point

Implemented as an event filter + QVariantAnimation rather than a Qt
Property on each class, so it attaches to any widget class without
touching that class's own event handlers or needing a Qt meta-property.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QEvent, QVariantAnimation, QEasingCurve, Qt, QPointF, QRectF
from PySide6.QtGui import QPainter

HOVER_SCALE = 0.96
PRESS_SCALE = 0.90
# Release-bounce peak. 1.0 = exactly the widget's own edge. It was 1.04,
# but a widget can't paint outside its rect, so anything above 1.0 clips
# the rounded corners off at the peak (a tall sidebar button's 24px curve
# measured ~4px, i.e. square). apply() reserves headroom automatically if
# this is raised above 1.0 again, at the cost of every button resting
# 1/overshoot smaller -- ~7px per end on a 340px sidebar button at 1.04.
# At 1.0 the release still visibly bounces: pressed 0.90 -> 1.0 -> settles
# at the hover size 0.96 (the cursor is almost always still over the
# button on release).
RELEASE_OVERSHOOT_SCALE = 1.0
HOVER_MS = 150
PRESS_MS = 110
RELEASE_UP_MS = 90
RELEASE_SETTLE_MS = 180


class PressPulse(QObject):
    def __init__(self, widget, hover_scale: float = HOVER_SCALE, press_scale: float = PRESS_SCALE,
                 overshoot_scale: float = RELEASE_OVERSHOOT_SCALE):
        super().__init__(widget)
        self._widget = widget
        self._hover_scale = hover_scale
        self._press_scale = press_scale
        self._overshoot_scale = overshoot_scale
        self.scale = 1.0
        self._pressed = False
        self._settle_target: float | None = None
        self._anim = QVariantAnimation(self)
        self._anim.valueChanged.connect(self._on_value)
        self._anim.finished.connect(self._on_finished)
        widget.installEventFilter(self)

    # ------------------------------------------------------------ painting

    def apply(self, painter: QPainter, center: "QPointF | None" = None) -> None:
        """Apply the current pulse scale to `painter`, around `center`
        (default: the widget's own center). Call before drawing anything
        that should pulse.

        HEADROOM: the drawn scale is `self.scale / overshoot`, not
        `self.scale`. A widget can't paint outside its own rect, so the
        old version's release bounce (1.04x) pushed the shape's rounded
        corners out past the edges, where Qt clipped them: a tall sidebar
        button's 24px corner curve dropped to ~4px (read as square) at
        the peak of every bounce. Now the PEAK of the bounce exactly fills
        the rect and rest is drawn at 1/overshoot (~96%), so the full
        rounded shape is always visible. Hover/press keep the same
        proportions relative to rest. Edges that must stay flush against
        a neighbor use touching_extension() -- see its docstring."""
        s = self.scale / self._overshoot_scale
        if s == 1.0:
            return
        c = QPointF(center) if center is not None else QRectF(self._widget.rect()).center()
        painter.translate(c)
        painter.scale(s, s)
        painter.translate(-c)

    def touching_extension(self, rect: QRectF) -> "tuple[float, float]":
        """(dx, dy): how far to extend a shape's rect on any side that
        TOUCHES a neighbor (a square, un-rounded side, e.g. between two
        joined tab buttons), so that side still sits exactly flush with
        the widget edge at rest despite the headroom apply() adds. Drawn
        at rest scale 1/overshoot around the center, an extension of
        size * (overshoot - 1) / 2 lands exactly on the edge. At the
        peak of the bounce that side overshoots and gets clipped, which
        is harmless because it's square anyway."""
        k = (self._overshoot_scale - 1.0) / 2.0
        return rect.width() * k, rect.height() * k

    # ------------------------------------------------------------ animation

    def _on_value(self, value) -> None:
        self.scale = float(value)
        self._widget.update()

    def _on_finished(self) -> None:
        # Second leg of the release pulse: overshoot -> resting size.
        if self._settle_target is not None:
            target = self._settle_target
            self._settle_target = None
            self._animate(target, RELEASE_SETTLE_MS, QEasingCurve.InOutCubic)

    def _animate(self, target: float, duration: int, curve=QEasingCurve.OutCubic) -> None:
        self._anim.stop()
        self._anim.setStartValue(self.scale)
        self._anim.setEndValue(float(target))
        self._anim.setDuration(duration)
        self._anim.setEasingCurve(curve)
        self._anim.start()

    def _resting(self) -> float:
        return self._hover_scale if self._widget.underMouse() else 1.0

    def reset(self) -> None:
        self._anim.stop()
        self._settle_target = None
        self._pressed = False
        self.scale = 1.0
        self._widget.update()

    # ------------------------------------------------------------ events

    def eventFilter(self, obj, event) -> bool:
        if obj is not self._widget:
            return False
        et = event.type()
        if et == QEvent.Enter:
            if not self._pressed and self._widget.isEnabled():
                self._settle_target = None
                self._animate(self._hover_scale, HOVER_MS)
        elif et == QEvent.Leave:
            if not self._pressed:
                self._settle_target = None
                self._animate(1.0, HOVER_MS)
        elif et in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick):
            if event.button() == Qt.LeftButton and self._widget.isEnabled():
                self._pressed = True
                self._settle_target = None
                self._animate(self._press_scale, PRESS_MS)
        elif et == QEvent.MouseButtonRelease:
            if event.button() == Qt.LeftButton and self._pressed:
                self._pressed = False
                resting = self._resting()
                self._settle_target = resting
                self._animate(max(resting, self._overshoot_scale), RELEASE_UP_MS)
        elif et in (QEvent.Hide, QEvent.EnabledChange):
            if et == QEvent.Hide or not self._widget.isEnabled():
                self.reset()
        return False


# ---------------------------------------------------------------- icon scaling cache
#
# Several button classes smooth-scale their icon pixmap inside
# paintEvent. That used to run only on hover/click repaints, but the
# pulse animation repaints every frame (~60/s per animating button), so
# the same scale is now cached. Keyed on QPixmap.cacheKey(), which
# changes whenever the pixmap's contents change.
from collections import OrderedDict as _OrderedDict

_SCALED_CACHE: "_OrderedDict" = _OrderedDict()
_SCALED_CACHE_MAX = 512


def scaled_cached(pixmap, width: int, height: int, aspect=Qt.KeepAspectRatio):
    key = (pixmap.cacheKey(), int(width), int(height), int(aspect.value if hasattr(aspect, "value") else aspect))
    hit = _SCALED_CACHE.get(key)
    if hit is not None:
        _SCALED_CACHE.move_to_end(key)
        return hit
    result = pixmap.scaled(int(width), int(height), aspect, Qt.SmoothTransformation)
    _SCALED_CACHE[key] = result
    while len(_SCALED_CACHE) > _SCALED_CACHE_MAX:
        _SCALED_CACHE.popitem(last=False)
    return result
