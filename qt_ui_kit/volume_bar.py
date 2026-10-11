"""
Horizontal volume control: a speaker icon on the left, then a track that
fills the rest of the widget's width. The track shows the INACTIVE
gradient as a static full-width background, with the ACTIVE gradient drawn
on top of it resized to the current value's proportion (same "resize to
fit the selected span" approach as the trim timeline's connector gradient),
and the marker icon centered on the current value's position.

Deliberately mirrors trim_timeline.py's split between pure coordinate math
(_value_at_x) and Qt event plumbing, for the same reason: testable without
needing to construct real QMouseEvent objects.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QRect, QSize, QPointF
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget

from .resources import resource_qpixmap

BASE_HEIGHT = 16      # 40% of the original 40px
ICON_MARGIN = 6      # gap between the speaker icon and the track
BASE_TRACK_HEIGHT = 8      # even (not 9) so it centers exactly on an integer
                       # pixel -- see the marker-centering note below
BASE_MARKER_SIZE = QSize(15, 15)  # 1.25x the previous 12px
BASE_SPEAKER_ICON_SIZE = 24       # 1.5x the previous 16px (itself already
                              # 2x an even earlier version)


def _round_to_even(x: float) -> int:
    return max(2 * round(x / 2), 2)


class VolumeBar(QWidget):
    value_changed = Signal(int)  # 0-100

    def __init__(self, parent=None):
        super().__init__(parent)
        self._track_height = BASE_TRACK_HEIGHT
        self._marker_size = BASE_MARKER_SIZE
        self._speaker_icon_size = BASE_SPEAKER_ICON_SIZE
        self.setFixedHeight(_round_to_even(BASE_HEIGHT))
        self._value = 100

        self._speaker_pixmap = resource_qpixmap("volume_speaker_icon.png")
        self._active_pixmap = resource_qpixmap("active_volume_gradient.png")
        self._inactive_pixmap = resource_qpixmap("inactive_volume_gradient.png")
        self._marker_pixmap = resource_qpixmap("bar_marker.png")

    # ------------------------------------------------------------ public API

    def set_value(self, value: int) -> None:
        self._value = max(0, min(100, value))
        self.update()

    @property
    def value(self) -> int:
        return self._value

    def set_scale(self, factor: float) -> None:
        # The WIDGET'S OWN height must also stay even, not just
        # self._track_height -- confirmed this matters: round(BASE_HEIGHT
        # * factor) lands on an odd number at several perfectly plausible
        # scale factors (e.g. 1.05 -> 17, 1.3 -> 21), and an odd WIDGET
        # height reintroduces the exact same top//2-truncation asymmetry
        # the even-track_height fix was for, just via a different
        # variable -- this was the actual remaining cause of the marker
        # still looking "slightly lower than it needs to be" after that
        # first fix.
        self.setFixedHeight(_round_to_even(max(BASE_HEIGHT * factor, 8)))
        # Stays even at every scale, not just at 1.0x -- an odd value
        # here is exactly what caused the marker-vs-track vertical
        # misalignment bug fixed earlier (see the centering note below).
        self._track_height = _round_to_even(BASE_TRACK_HEIGHT * factor)
        self._marker_size = QSize(
            max(round(BASE_MARKER_SIZE.width() * factor), 6),
            max(round(BASE_MARKER_SIZE.height() * factor), 6),
        )
        self._speaker_icon_size = max(round(BASE_SPEAKER_ICON_SIZE * factor), 8)
        self.update()

    # ------------------------------------------------------------ pure coordinate math

    def _icon_size(self) -> int:
        return self._speaker_icon_size

    def _track_rect(self) -> QRect:
        icon_size = self._icon_size()
        half_marker = self._marker_size.width() / 2
        # The track's usable extent is inset by half the marker's own
        # width on BOTH ends -- reserving room for the marker to overhang
        # past the track's colored fill while its CENTER still sits
        # exactly at the track's true endpoints (0%/100%) rather than the
        # marker's edge landing there instead. Without this, centering
        # the marker exactly at value=0/100 would put roughly half of it
        # past the widget's actual edge, which the earlier version
        # avoided by insetting the marker's TRAVEL range instead -- that
        # kept it fully on-widget, but then the marker never quite
        # reached the true 0%/100% endpoints, sitting visibly short of
        # them at each end.
        left = icon_size + ICON_MARGIN + half_marker
        width = max(self.width() - left - half_marker, 1)
        # self._track_height is kept even specifically so this divides
        # with no remainder -- see the marker vertical-centering note in
        # paintEvent below for why an odd track height was the actual
        # cause of the marker looking off-center.
        top = (self.height() - self._track_height) // 2
        return QRect(round(left), top, round(width), self._track_height)

    def _value_at_x(self, x: float) -> int:
        track = self._track_rect()
        if track.width() <= 0:
            return self._value
        fraction = (x - track.left()) / track.width()
        return max(0, min(100, round(fraction * 100)))

    def _x_at_value(self, value: int) -> float:
        # Plain proportional mapping across the track's own endpoints --
        # the half-marker inset now lives in _track_rect() itself (see
        # its docstring above), so the marker's center correctly lands
        # exactly AT track.left() (0%) and track.left()+track.width()
        # (100%), with the marker allowed to overhang past the track's
        # colored fill into the margin reserved for exactly that.
        track = self._track_rect()
        return track.left() + (value / 100) * track.width()

    # ------------------------------------------------------------ Qt event wrappers

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._set_value_from_x(event.position().x())

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.LeftButton:
            self._set_value_from_x(event.position().x())

    def _set_value_from_x(self, x: float) -> None:
        self.set_value(self._value_at_x(x))
        self.value_changed.emit(self._value)

    # ------------------------------------------------------------ rendering

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        icon_size = self._icon_size()
        # No shrinking margin here (unlike before) -- self._speaker_icon_size
        # already IS the target drawn size, doubled from the old
        # icon_size-minus-4px-margin-on-each-side result.
        icon_rect = QRect(0, (self.height() - icon_size) // 2, icon_size, icon_size)
        painter.drawPixmap(icon_rect, self._speaker_pixmap)

        track = self._track_rect()

        # Active fill (0 to the current value) and the inactive remainder
        # (value to 100) each get the FULL gradient image resized to fit
        # their own span -- not the whole gradient stretched across the
        # entire track with one part painted over. The earlier version
        # did the latter for the inactive side, which meant whatever was
        # visible beyond the active fill was only a cropped SLICE of the
        # inactive gradient (e.g. just its rightmost 30% of colors at
        # value=70), rather than the inactive gradient's full color range
        # always being visible within however much space it actually has.
        fill_width = int(track.width() * (self._value / 100))
        if fill_width > 0:
            fill_rect = QRect(track.left(), track.top(), fill_width, track.height())
            painter.drawPixmap(fill_rect, self._active_pixmap)
        if fill_width < track.width():
            remainder_rect = QRect(
                track.left() + fill_width, track.top(),
                track.width() - fill_width, track.height(),
            )
            painter.drawPixmap(remainder_rect, self._inactive_pixmap)

        # Marker at the current value's position. Vertically centered on
        # self.height()/2 exactly -- the marker looking "slightly below"
        # center was actually track_height being odd (9): (16-9)//2 == 3
        # (integer division truncates down), so the track's own visual
        # center sat at 3+9/2=7.5 while the marker centered on the
        # widget's true center of 8.0 -- correct on its own, but visibly
        # lower than the track it sits on. track_height is kept even at
        # every scale, so its center always lands on the same point too.
        marker_x = self._x_at_value(self._value)
        marker_rect = QRect(0, 0, self._marker_size.width(), self._marker_size.height())
        marker_rect.moveCenter(QPointF(marker_x, self.height() / 2).toPoint())
        # Unconditional final clamp, on top of the inset-based travel-
        # range math in _x_at_value above -- reported as still clipping
        # a little on the bottom and at the ends even after that fix,
        # which points to residual 1px overflow from moveCenter's own
        # integer rounding (QPointF.toPoint() rounds the float center to
        # the nearest int before moveCenter positions the rect from it) --
        # rather than trying to chase that precisely, this guarantees the
        # drawn rect never exceeds the widget's bounds regardless of any
        # upstream rounding.
        if marker_rect.right() >= self.width():
            marker_rect.moveRight(self.width() - 1)
        if marker_rect.left() < 0:
            marker_rect.moveLeft(0)
        if marker_rect.bottom() >= self.height():
            marker_rect.moveBottom(self.height() - 1)
        if marker_rect.top() < 0:
            marker_rect.moveTop(0)
        painter.drawPixmap(marker_rect, self._marker_pixmap)

        painter.end()
