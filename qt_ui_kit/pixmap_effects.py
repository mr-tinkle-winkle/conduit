"""
Shared pixmap helpers for border rendering (sidebar nav buttons AND the
unedited-video-clip highlight): loading a user-chosen custom image in
place of the built-in gradient, and applying a hue shift to whichever
pixmap ends up being used.

Both are meant to be called ONCE per widget construction / settings
reload, never per-frame or from inside a paintEvent -- hue_shift_pixmap
in particular loops pixel-by-pixel in pure Python (no numpy dependency
is currently declared for this project's flake, so this deliberately
doesn't reach for it), which is a fine one-time cost (roughly half a
second on a 512x512 image, measured directly) but would be a real
per-frame performance problem if it were ever called from paintEvent.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QImage, QColor, QPainter


def resolve_border_pixmap(custom_image_path: str, default_pixmap: QPixmap) -> QPixmap:
    """A user-chosen image file, if one is set and actually exists, in
    place of the built-in default -- silently falls back to the default
    if the path is empty, the file's gone missing (deleted/moved since
    it was set), or fails to load as an image, rather than erroring or
    showing nothing."""
    if custom_image_path:
        path = Path(custom_image_path)
        if path.exists():
            custom = QPixmap(str(path))
            if not custom.isNull():
                return custom
    return default_pixmap


def hue_shift_pixmap(pixmap: QPixmap, degrees: int) -> QPixmap:
    """Rotate the hue of every pixel by `degrees` (wrapping around 360),
    preserving each pixel's own saturation/value/alpha. Fully transparent
    pixels and fully achromatic ones (pure gray/white/black -- Qt reports
    no defined hue for these, getHsvF()'s hue component comes back -1)
    are left alone rather than getting an arbitrary hue assigned to them.
    degrees == 0 (mod 360) returns the input pixmap as-is with no copy
    at all -- the common case, since this is opt-in and defaults to 0."""
    if degrees % 360 == 0:
        return pixmap
    image = pixmap.toImage().convertToFormat(QImage.Format_ARGB32)
    shift = (degrees % 360) / 360.0
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.alpha() == 0:
                continue
            h, s, v, a = color.getHsvF()
            if h < 0:
                continue  # achromatic -- no hue to shift
            image.setPixelColor(x, y, QColor.fromHsvF((h + shift) % 1.0, s, v, a))
    return QPixmap.fromImage(image)


_hue_shift_cache: dict[tuple[str, int], QPixmap] = {}


def hue_shift_pixmap_cached(cache_key: str, pixmap: QPixmap, degrees: int) -> QPixmap:
    """Same as hue_shift_pixmap(), but memoized process-wide by
    (cache_key, degrees). Matters most for VideoCard's unedited-clip
    highlight, which is rebuilt fresh in every single card's __init__ --
    one per video shown in the grid -- so without this, a hue shift's
    one-time ~half-second cost (measured directly on a 512x512 image)
    would multiply by however many cards are in the library instead of
    genuinely happening once. cache_key should identify the SOURCE
    pixmap (e.g. the resource name or custom image path used), since
    the actual QPixmap object's identity isn't a reliable/stable cache
    key across separately-loaded copies of the same underlying image."""
    key = (cache_key, degrees % 360)
    if key not in _hue_shift_cache:
        _hue_shift_cache[key] = hue_shift_pixmap(pixmap, degrees)
    return _hue_shift_cache[key]


def silhouette_outline_pixmap(pixmap: QPixmap, color: QColor, width: int) -> QPixmap:
    """Outline `pixmap`'s own opaque SILHOUETTE (not a bounding
    rectangle) in `color`, `width` pixels thick -- the "Filter Outline"
    setting, which was specifically asked to match the icon's actual
    shape rather than draw a square around it. Also fills any fully
    ENCLOSED transparent regions (a hole inside the silhouette, like
    the counter of a letter "O" or "A") with the outline color too, per
    a direct follow-up request -- a hollow gap poking through the
    middle of an otherwise-solid outline reads as a rendering glitch,
    not a design choice.

    The canvas GROWS outward by `width` on each side rather than the
    icon being shrunk inward to leave room within a fixed-size canvas
    (an earlier version did that). Shrinking the icon visually
    detaches the outline from where the icon's TRUE edge actually is --
    reported directly as the outline appearing to "float 1 or 2 pixels
    off" the icon. Growing outward instead means the icon is placed at
    its own full, untouched size, and the outline hugs its real edge
    by construction; the result is simply LARGER than the input now,
    and it's the CALLER's job to scale the whole composite (icon +
    outline together, so their relative proportions never change) down
    to whatever final display size is needed -- `_FilterIconLabel`
    already does exactly that scaling step regardless of this
    pixmap's own size, so this needs no caller-side change beyond
    what's already there.

    Implemented as a plain morphological dilation of the alpha channel
    (a pixel becomes part of the outline if it's currently transparent
    but within `width` pixels of an opaque one), plus a flood fill from
    the canvas border to distinguish true exterior transparent pixels
    from enclosed ones -- pure Python, no numpy, so this is a real
    O(w*h*width^2) cost. Fine for typical small icon sizes as a cached
    one-time-per-(icon, color, width) operation (see the cache below),
    not something to call per-frame."""
    if width <= 0:
        return pixmap
    from PySide6.QtCore import QSize, Qt
    from PySide6.QtGui import QPainter
    from collections import deque

    size = pixmap.size()
    new_size = QSize(size.width() + 2 * width, size.height() + 2 * width)

    icon_layer = QImage(new_size, QImage.Format_ARGB32)
    icon_layer.fill(Qt.transparent)
    painter = QPainter(icon_layer)
    painter.drawPixmap(width, width, pixmap)
    painter.end()

    w, h = new_size.width(), new_size.height()
    opaque = [[icon_layer.pixelColor(x, y).alpha() > 10 for x in range(w)] for y in range(h)]

    # Flood fill from every border pixel across connected transparent
    # cells -- whatever this reaches is genuinely OUTSIDE the
    # silhouette; any transparent pixel it never reaches is enclosed
    # (a hole) and gets filled in below, same as the outline itself.
    exterior = [[False] * w for _ in range(h)]
    queue = deque()
    for x in range(w):
        for y in (0, h - 1):
            if not opaque[y][x] and not exterior[y][x]:
                exterior[y][x] = True
                queue.append((x, y))
    for y in range(h):
        for x in (0, w - 1):
            if not opaque[y][x] and not exterior[y][x]:
                exterior[y][x] = True
                queue.append((x, y))
    while queue:
        cx, cy = queue.popleft()
        for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
            if 0 <= nx < w and 0 <= ny < h and not opaque[ny][nx] and not exterior[ny][nx]:
                exterior[ny][nx] = True
                queue.append((nx, ny))

    result = QImage(new_size, QImage.Format_ARGB32)
    result.fill(Qt.transparent)
    radius_sq = width * width
    for y in range(h):
        for x in range(w):
            if opaque[y][x]:
                continue
            if not exterior[y][x]:
                # Enclosed hole -- fill solid, no dilation-distance
                # check needed (it's inside the silhouette regardless
                # of how far from an opaque pixel it happens to sit).
                result.setPixelColor(x, y, color)
                continue
            found = False
            for dy in range(-width, width + 1):
                ny = y + dy
                if ny < 0 or ny >= h:
                    continue
                for dx in range(-width, width + 1):
                    if dx * dx + dy * dy > radius_sq:
                        continue
                    nx = x + dx
                    if 0 <= nx < w and opaque[ny][nx]:
                        found = True
                        break
                if found:
                    break
            if found:
                result.setPixelColor(x, y, color)

    painter2 = QPainter(result)
    painter2.drawImage(0, 0, icon_layer)
    painter2.end()
    return QPixmap.fromImage(result)


_outline_cache: dict[tuple[str, str, int], QPixmap] = {}


def silhouette_outline_pixmap_cached(cache_key: str, pixmap: QPixmap, color: QColor, width: int) -> QPixmap:
    """Memoized by (cache_key, color, width) -- same reasoning as
    hue_shift_pixmap_cached: a filter icon can appear on many cards at
    once, and this is real per-pixel work, not something to redo for
    every single card showing the same icon."""
    key = (cache_key, color.name(), width)
    if key not in _outline_cache:
        _outline_cache[key] = silhouette_outline_pixmap(pixmap, color, width)
    return _outline_cache[key]


def tint_pixmap(pixmap: QPixmap, color: QColor) -> QPixmap:
    """Recolor every pixel of `pixmap` to a single flat `color`,
    preserving the pixmap's existing alpha channel (so the icon's
    silhouette/shape is kept, but whatever colors/gradient it shipped
    with are replaced entirely) -- used for the Search/Refresh/Sort
    toolbar icons, which are recolored to match the card text color
    rather than shown in their own original artwork colors. A single
    composited QPainter pass (CompositionMode_SourceIn), not a
    per-pixel Python loop like hue_shift_pixmap -- cheap enough to not
    strictly need caching, but cached below anyway for consistency
    with this module's other pixmap effects and because these three
    icons get reloaded/retinted together every time the header is
    rebuilt."""
    result = QPixmap(pixmap.size())
    result.fill(Qt.transparent)
    painter = QPainter(result)
    painter.drawPixmap(0, 0, pixmap)
    painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
    painter.fillRect(result.rect(), color)
    painter.end()
    return result


_tint_cache: dict[tuple[str, str], QPixmap] = {}


def tint_pixmap_cached(cache_key: str, pixmap: QPixmap, color: QColor) -> QPixmap:
    key = (cache_key, color.name())
    if key not in _tint_cache:
        _tint_cache[key] = tint_pixmap(pixmap, color)
    return _tint_cache[key]
