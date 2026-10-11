"""
Rounded-corner path building for the UI update's "Rounded Corners"
setting -- one shared utility so every rounded element (cards, nested
card boxes, sidebar borders, tab icons, text boxes, the video player)
gets the same corner shape and the same "don't round a corner that's
touching another element" behavior, rather than each place growing its
own slightly-different implementation.

"Apple style" here, per how it was actually asked for, just means
smooth and not immediately going into a circular arc -- NOT a literal
superellipse/squircle solve (there's no need for the full math). Each
corner is a single cubic Bezier whose control points sit further out
along the two edges than a true quarter-circle's would, which is what
produces that flatter, "eases into the curve" look instead of an
abrupt straight-to-circular transition. SMOOTH_KAPPA below is the
tuning knob -- 0.5523 is the constant that makes a Bezier approximate
an actual quarter-circle; a larger value pulls the curve flatter/
smoother. This can't be visually verified in this sandbox (no real
display), so treat the exact constant as a starting point to nudge
once it's actually on screen.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF
from PySide6.QtGui import QPainterPath, QPixmap, QPainter
from PySide6.QtCore import Qt

# 0.5523 = the standard constant for a Bezier-approximated quarter
# circle. Above that flattens the curve (smoother, less immediately
# circular); this is intentionally above the circular value.
SMOOTH_KAPPA = 0.68


def rounded_rect_path(
    rect: QRectF,
    radius: float,
    top_left: bool = True,
    top_right: bool = True,
    bottom_left: bool = True,
    bottom_right: bool = True,
) -> QPainterPath:
    """A closed QPainterPath for `rect` with smooth rounded corners at
    `radius`, individually skippable per corner (pass False for a
    corner that's touching another rounded element -- e.g. two card
    boxes/sidebar buttons sharing an edge -- so the shared seam stays a
    sharp, flush line instead of each side showing a rounded notch).
    radius is clamped to at most half of whichever of rect's width/
    height is smaller, so an oversized radius on a small/thin rect
    can't produce an invalid or self-intersecting path.

    A skipped corner is a plain sharp right angle, not radius=0's own
    (still-a-tiny-curve) case -- the two are handled identically here
    since a 0-radius "curve" and a true sharp corner are visually and
    geometrically the same thing, but the boolean flags are the
    intended way to skip a corner rather than passing radius=0 for a
    whole rect just to flatten one of its four.
    """
    r = max(0.0, min(radius, rect.width() / 2, rect.height() / 2))
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    k = r * SMOOTH_KAPPA

    path = QPainterPath()
    # Start at the top edge, just past the top-left corner, and go
    # clockwise: top edge -> top-right corner -> right edge -> ...
    path.moveTo(x + (r if top_left else 0), y)
    path.lineTo(x + w - (r if top_right else 0), y)
    if top_right:
        path.cubicTo(x + w - r + k, y, x + w, y + r - k, x + w, y + r)
    else:
        path.lineTo(x + w, y)
    path.lineTo(x + w, y + h - (r if bottom_right else 0))
    if bottom_right:
        path.cubicTo(x + w, y + h - r + k, x + w - r + k, y + h, x + w - r, y + h)
    else:
        path.lineTo(x + w, y + h)
    path.lineTo(x + (r if bottom_left else 0), y + h)
    if bottom_left:
        path.cubicTo(x + r - k, y + h, x, y + h - r + k, x, y + h - r)
    else:
        path.lineTo(x, y + h)
    path.lineTo(x, y + (r if top_left else 0))
    if top_left:
        path.cubicTo(x, y + r - k, x + r - k, y, x + r, y)
    else:
        path.lineTo(x, y)
    path.closeSubpath()
    return path


def round_pixmap_corners(pixmap: QPixmap, radius: float) -> QPixmap:
    """Bake rounded corners directly into a copy of `pixmap` (a
    transparent-cornered clip, not just a widget-level effect) -- used
    for the video thumbnail, since a QLabel showing a QPixmap doesn't
    clip the image to anything but its own rectangular bounds on its
    own. radius <= 0 returns the input unchanged, no copy."""
    if radius <= 0:
        return pixmap
    result = QPixmap(pixmap.size())
    result.fill(Qt.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setClipPath(rounded_rect_path(QRectF(pixmap.rect()), radius))
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    return result
