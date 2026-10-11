"""
The trim timeline: a horizontal bar spanning the full video duration, with
two draggable handles marking the selected start/end range, plus a
playhead marker showing current playback position.

Split deliberately into pure, testable pieces vs. Qt event plumbing:
- _time_to_x / _x_to_time: pure coordinate math, no widget state needed
  beyond width/duration.
- _drag_to: the actual state update for a drag, independent of how the
  drag was initiated (real mouse event or a test calling it directly).
mousePressEvent/mouseMoveEvent/mouseReleaseEvent are thin wrappers around
these, so the logic itself is testable without needing to fight
constructing real QMouseEvent objects or simulate an actual display.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QPointF, QRect, QSize
from PySide6.QtGui import QPainter, QColor, QPen
from PySide6.QtWidgets import QWidget

from .resources import resource_qpixmap

# Widened from the original 8px -- handle_texture.png is ~91% transparent
# (a thin grip-line pattern on a mostly-clear background), and an 8px-wide
# handle left almost nothing of it visible. Widening alone wasn't the
# actual fix though (see the handle-drawing code below) -- it's paired
# with a solid base fill so there's still a clearly visible handle body
# even where the texture itself is transparent.
BASE_HANDLE_WIDTH = 16
MIN_GAP_SEC = 0.05  # smallest allowed distance between start and end handles

# The playhead/volume marker asset (bar_marker.png) is a roughly square
# badge/ring graphic, not an elongated bar -- so unlike the handle
# texture, it's drawn at a fixed icon size centered on its position
# rather than stretched to fill a tall thin rect (which would smear a
# round badge into an unrecognizable vertical streak).
BASE_MARKER_SIZE = QSize(20, 20)
BASE_MIN_HEIGHT = 48


class TrimTimeline(QWidget):
    range_changed = Signal(float, float)   # start, end -- emitted live while dragging a handle
    seek_requested = Signal(float)         # emitted for both live handle-drag preview AND a plain left-click/drag seek
    drag_started = Signal()                # emitted on a handle grab (right-click), before any movement -- callers can pause playback here
    drag_finished = Signal(float, float)   # start, end -- emitted when a handle drag ends, once the range is committed

    def __init__(self, parent=None):
        super().__init__(parent)
        self._handle_width = BASE_HANDLE_WIDTH
        self._marker_size = BASE_MARKER_SIZE
        self.setMinimumHeight(BASE_MIN_HEIGHT)
        # as tall as it needs, as wide as it gets (a free height let it stretch to fill a whole box)
        from PySide6.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._duration = 0.0
        self._start = 0.0
        self._end = 0.0
        self._playhead = 0.0
        self._dragging: str | None = None  # None | "start" | "end" -- a right-click handle grab
        self._left_seeking = False         # a left-click/drag plain seek, independent of handle dragging

        # Loaded once, then drawn at paint time. The connector gradient is
        # resized to whatever width the current start/end gap is on each
        # repaint; the handle texture is stretched to the handle's own
        # rect (not tiled -- it's a scroll handle, not a repeating pattern).
        self._handle_pixmap = resource_qpixmap("handle_texture.png")
        self._gradient_pixmap = resource_qpixmap("handle_connector_gradient.png")
        self._marker_pixmap = resource_qpixmap("bar_marker.png")

    # ------------------------------------------------------------ public API

    def set_duration(self, duration: float) -> None:
        self._duration = max(duration, 0.001)
        self._start = 0.0
        self._end = self._duration
        self.update()

    def set_range(self, start: float, end: float) -> None:
        self._start = max(0.0, min(start, self._duration))
        self._end = max(0.0, min(end, self._duration))
        self.update()

    def set_playhead(self, position: float) -> None:
        self._playhead = position
        self.update()

    def set_scale(self, factor: float) -> None:
        self._handle_width = max(round(BASE_HANDLE_WIDTH * factor), 4)
        self._marker_size = QSize(
            max(round(BASE_MARKER_SIZE.width() * factor), 8),
            max(round(BASE_MARKER_SIZE.height() * factor), 8),
        )
        self.setMinimumHeight(max(round(BASE_MIN_HEIGHT * factor), 24))
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(240, self.minimumHeight())

    @property
    def start(self) -> float:
        return self._start

    @property
    def end(self) -> float:
        return self._end

    @property
    def dragging_handle(self) -> str | None:
        """None | "start" | "end" -- which handle a right-click drag is
        currently moving, if any. Lets callers (e.g. the Editor page)
        live-seek the preview to whichever edge is actually being
        dragged rather than guessing from range_changed's two values
        alone, since only one of them is actually moving on any given
        drag."""
        return self._dragging

    # ------------------------------------------------------------ pure coordinate math

    def _usable_width(self) -> float:
        return max(self.width() - 2 * self._handle_width, 1)

    def _time_to_x(self, t: float) -> float:
        if self._duration <= 0:
            return float(self._handle_width)
        return self._handle_width + (t / self._duration) * self._usable_width()

    def _x_to_time(self, x: float) -> float:
        if self._duration <= 0:
            return 0.0
        t = (x - self._handle_width) / self._usable_width() * self._duration
        return min(max(t, 0.0), self._duration)

    # ------------------------------------------------------------ drag state (testable directly)

    def _press_at(self, x: float) -> None:
        """A right-click (or a direct call, for tests): grabs the nearer
        handle if the click is close to one. Otherwise, a click outside
        the current [start, end] selection unambiguously means "move the
        boundary it's outside of" (there's no sensible reading of
        clicking left of start as wanting to move end instead), and a
        click genuinely inside the selection falls back to "which side
        of the playhead" to decide which edge to pull in. Left-click is
        handled separately by _seek_at below -- it never touches the
        trim range at all.

        The boundary-first check matters specifically when the playhead
        itself sits outside [start, end] (e.g. after scrubbing past an
        edge): using playhead-side alone in that case could assign a
        click that's clearly on the start side (but happens to be on the
        far side of an out-of-range playhead) to the END handle instead,
        which then immediately clamps end down to just above start --
        collapsing the whole selection to ~0 length. Confirmed this
        happens with the pure playhead-side version before this fix.
        """
        start_x = self._time_to_x(self._start)
        end_x = self._time_to_x(self._end)
        if abs(x - start_x) <= self._handle_width * 1.5:
            self._dragging = "start"
            self.drag_started.emit()
        elif abs(x - end_x) <= self._handle_width * 1.5:
            self._dragging = "end"
            self.drag_started.emit()
        elif x < start_x:
            self._dragging = "start"
            self.drag_started.emit()
            self._drag_to(self._x_to_time(x))
        elif x > end_x:
            self._dragging = "end"
            self.drag_started.emit()
            self._drag_to(self._x_to_time(x))
        else:
            # Genuinely inside the current selection -- fall back to
            # which side of the playback marker the click is on, as
            # before.
            playhead_x = self._time_to_x(self._playhead)
            self._dragging = "start" if x < playhead_x else "end"
            self.drag_started.emit()
            self._drag_to(self._x_to_time(x))

    def _drag_to(self, t: float) -> None:
        if self._dragging is None:
            return
        if self._dragging == "start":
            self._start = max(0.0, min(t, self._end - MIN_GAP_SEC))
        elif self._dragging == "end":
            self._end = min(self._duration, max(t, self._start + MIN_GAP_SEC))
        self.update()
        self.range_changed.emit(self._start, self._end)
        # No seek_requested here (unlike _seek_at/left-click) -- dragging
        # a handle with the right mouse button only adjusts the trim
        # boundary now, without also scrubbing live playback. Right-click
        # (handles) and left-click (seek) are fully independent actions.

    def _release(self) -> None:
        was_dragging = self._dragging is not None
        self._dragging = None
        self._left_seeking = False
        if was_dragging:
            self.drag_finished.emit(self._start, self._end)

    def _seek_at(self, x: float) -> None:
        """A left-click (or drag): just moves playback to that point on
        the timeline. Never grabs or moves a handle, regardless of how
        close to one the click lands."""
        self._left_seeking = True
        self.seek_requested.emit(self._x_to_time(x))

    # ------------------------------------------------------------ Qt event wrappers

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.RightButton:
            self._press_at(event.position().x())
        elif event.button() == Qt.LeftButton:
            self._seek_at(event.position().x())

    def mouseMoveEvent(self, event) -> None:
        if self._dragging is not None:
            self._drag_to(self._x_to_time(event.position().x()))
        elif self._left_seeking:
            self.seek_requested.emit(self._x_to_time(event.position().x()))

    def mouseReleaseEvent(self, event) -> None:
        self._release()

    # ------------------------------------------------------------ rendering

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        # Without this, QPainter scales pixmaps with fast/nearest-neighbor
        # sampling -- for the handle texture specifically (~91%
        # transparent, thin detail lines) that was landing almost
        # entirely on transparent source pixels and contributed to it
        # reading as invisible. Smooth sampling alone wasn't the whole
        # fix (see the solid base fill below), but it does make the
        # texture's own detail actually survive being scaled.
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        bar_y = self.height() // 2 - 4
        bar_height = 8
        bar_rect_left = self._handle_width
        bar_rect_width = self._usable_width()

        # Full-duration background track
        painter.fillRect(bar_rect_left, bar_y, int(bar_rect_width), bar_height, QColor("#3a3a3a"))

        # Selected range highlight -- the connector gradient image,
        # stretched to whatever width the current start/end gap is.
        start_x = self._time_to_x(self._start)
        end_x = self._time_to_x(self._end)
        selection_rect = QRect(int(start_x), bar_y, max(int(end_x - start_x), 0), bar_height)
        if selection_rect.width() > 0:
            painter.drawPixmap(selection_rect, self._gradient_pixmap)

        # Playhead -- a fixed-size marker icon centered on the position,
        # rather than a full-height line (see MARKER_SIZE's comment).
        playhead_x = self._time_to_x(self._playhead)
        marker_rect = QRect(0, 0, self._marker_size.width(), self._marker_size.height())
        marker_rect.moveCenter(QPointF(playhead_x, self.height() / 2).toPoint())
        # Same defensive clamp as volume_bar.py's marker, applied
        # proactively here too even though this one wasn't specifically
        # reported as clipping -- same underlying mechanism (moveCenter's
        # integer rounding can place the rect a pixel outside the widget
        # at the very ends of its range), so worth guarding against
        # unconditionally rather than waiting for it to actually surface.
        if marker_rect.right() >= self.width():
            marker_rect.moveRight(self.width() - 1)
        if marker_rect.left() < 0:
            marker_rect.moveLeft(0)
        if marker_rect.bottom() >= self.height():
            marker_rect.moveBottom(self.height() - 1)
        if marker_rect.top() < 0:
            marker_rect.moveTop(0)
        painter.drawPixmap(marker_rect, self._marker_pixmap)

        # Handles -- a solid base (this IS the visible handle shape/body)
        # with the texture image stretched on top of it for decoration.
        # The texture alone is almost entirely transparent, so without
        # this base fill there was no visible handle at all -- just a
        # faint scatter of texture detail with nothing solid behind it.
        for x in (start_x, end_x):
            handle_rect = QRect(int(x - self._handle_width / 2), 2, self._handle_width, self.height() - 4)
            painter.setPen(QPen(QColor("#000000"), 1))
            painter.setBrush(QColor("#e0e0e0"))
            painter.drawRect(handle_rect)
            painter.drawPixmap(handle_rect, self._handle_pixmap)

        painter.end()
