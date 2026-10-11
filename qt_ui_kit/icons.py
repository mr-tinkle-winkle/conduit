"""
Icons: TEXT by default, images only when an app says so.

Every icon in the kit has a name ("check", "close", "search", ...).  By default it is drawn from a
text glyph in the current style's text colour; an app replaces any of them with an image file:

    kit.icons.set_icon("search", "/path/to/search.png")     # this app's own art
    kit.icons.set_glyph("search", "Find")                   # or different text

``icon_pixmap(name, size)`` is what widgets call.  Glyph fallbacks: each name lists several
candidates and the first one the font can actually draw is used (so a missing symbol never shows
as a tofu box); the last candidate is plain ASCII.

Textures (the trim timeline's handles, the volume bar's fill, the backdrop gradient) are painted
from the theme colours by default and overridable the same way (``set_icon("handle", path)``).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPixmap

GLYPHS: "dict[str, list[str]]" = {
    "check": ["✓", "✔", "v"],
    "close": ["✕", "×", "x"],
    "block": ["✕", "×", "x"],
    "search": ["⌕", "⚲", "?"],
    "search_active": ["⌕", "⚲", "?"],
    "refresh": ["⟳", "↻", "R"],
    "sort": ["⇅", "↕", "S"],
    "filter": ["⧩", "▽", "F"],
    "settings": ["⚙", "*"],
    "edit": ["✎", "✏", "E"],
    "copy": ["⎘", "⧉", "C"],
    "delete": ["\U0001f5d1", "⌦", "D"],
    "add": ["+"],
    "remove": ["−", "-"],
    "star": ["★", "*"],
    "star_off": ["☆", "o"],
    "play": ["▶", ">"],
    "pause": ["⏸", "‖", "||"],
    "prev": ["◀", "<"],
    "next": ["▶", ">"],
    "up": ["▲", "^"],
    "down": ["▼", "v"],
    "expand": ["▸", ">"],
    "collapse": ["▾", "v"],
    "menu": ["☰", "="],
    "volume": ["♪", "♫", "V"],
    "folder": ["▤", "⌂", "D"],
    "file": ["□", "F"],
    "info": ["ℹ", "i"],
    "warning": ["⚠", "!"],
    "fullscreen": ["⛶", "⤢", "[]"],
    "home": ["⌂", "H"],
}

_overrides: "dict[str, str]" = {}          # name -> image path
_glyph_overrides: "dict[str, str]" = {}
_cache: dict = {}


def set_icon(name: str, image_path: "str | Path | None") -> None:
    """Use an image file for this icon (None = back to the text glyph)."""
    if image_path:
        _overrides[name] = str(image_path)
    else:
        _overrides.pop(name, None)
    _cache.clear()


def set_glyph(name: str, text: str) -> None:
    _glyph_overrides[name] = text
    _cache.clear()


def glyph(name: str, font: "QFont | None" = None) -> str:
    if name in _glyph_overrides:
        return _glyph_overrides[name]
    cands = GLYPHS.get(name, [name])
    fm = QFontMetricsF(font or QFont())
    for c in cands:
        if all(fm.inFontUcs4(ord(ch)) for ch in c):
            return c
    return cands[-1]


def text_pixmap(text: str, size: int = 64, color: "QColor | str | None" = None, bold: bool = True) -> QPixmap:
    """``text`` drawn centred and as large as fits in a size x size transparent pixmap."""
    from .theme import Theme
    col = QColor(color) if color is not None else Theme().text()
    key = ("t", text, size, col.rgba(), bold)
    if key in _cache:
        return _cache[key]
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    f = QFont()
    f.setBold(bold)
    f.setPixelSize(max(6, int(size * 0.8)))
    fm = QFontMetricsF(f)
    w = fm.horizontalAdvance(text)
    if w > size * 0.92:
        f.setPixelSize(max(6, int(f.pixelSize() * size * 0.92 / w)))
        fm = QFontMetricsF(f)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    p.setFont(f)
    p.setPen(col)
    p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, text)
    p.end()
    _cache[key] = pm
    return pm


def icon_pixmap(name: str, size: int = 64, color: "QColor | str | None" = None) -> QPixmap:
    """The named icon: the app's image if one was set, else the text glyph."""
    path = _overrides.get(name)
    if path:
        pm = QPixmap(path)
        if not pm.isNull():
            return pm
    if name in TEXTURES:
        return texture(name)
    f = QFont()
    f.setBold(True)
    return text_pixmap(glyph(name, f), size, color)


# ---------------------------------------------------------------- painted textures

def _gradient_pm(w: int, h: int, stops, vertical: bool, radius: float = 0.0) -> QPixmap:
    pm = QPixmap(w, h)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, 0 if not vertical else 0, h if vertical else 0)
    if not vertical:
        g = QLinearGradient(0, 0, w, 0)
    for pos, c in stops:
        g.setColorAt(pos, c)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, w, h), radius, radius)
    p.fillPath(path, g)
    p.end()
    return pm


def _tex_handle():
    from .theme import Theme
    t = Theme()
    return _gradient_pm(32, 128, [(0, t.text()), (1, t.secondary())], True, 6)


def _tex_connector():
    from .theme import Theme
    t = Theme()
    a, b = QColor(t.text()), QColor(t.secondary())
    a.setAlpha(110)
    b.setAlpha(110)
    return _gradient_pm(128, 32, [(0, a), (1, b)], False)


def _tex_marker():
    from .theme import Theme
    return _gradient_pm(16, 128, [(0, Theme().outline()), (1, Theme().outline())], True, 4)


def _tex_volume_active():
    from .theme import Theme
    t = Theme()
    return _gradient_pm(128, 32, [(0, t.secondary()), (1, t.text())], False)


def _tex_volume_inactive():
    from .theme import Theme
    t = Theme()
    return _gradient_pm(128, 32, [(0, t.surface()), (1, t.accent())], False)


def _tex_backdrop():
    from .theme import Theme
    t = Theme()
    return _gradient_pm(256, 256, [(0, t.accent()), (1, t.window())], True)


TEXTURES = {"handle": _tex_handle, "connector": _tex_connector, "marker": _tex_marker,
            "volume_active": _tex_volume_active, "volume_inactive": _tex_volume_inactive,
            "backdrop": _tex_backdrop}


def texture(name: str) -> QPixmap:
    path = _overrides.get(name)
    if path:
        pm = QPixmap(path)
        if not pm.isNull():
            return pm
    return TEXTURES[name]()
