"""
App-styled replacements for the stock Qt/KDE dialogs: a text prompt
(QInputDialog.getText), a colour picker (QColorDialog.getColor) and a
file picker (QFileDialog's getOpenFileName / getOpenFileNames /
getSaveFileName / getExistingDirectory). Each has a function with the
same shape and return value as the static Qt call it replaces, so call
sites only change the name.

All of them are frameless rounded dialogs on the library background
with an accent outline (the same look as the Add Filter, Export and
message dialogs), use CustomButton / CustomLineEdit / CustomComboBox /
CustomCheckBox for their controls, and can be dragged by any empty spot.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QRectF, QPointF, Signal, QSize, QTimer
from PySide6.QtGui import (
    QColor, QPainter, QPen, QLinearGradient, QBrush, QConicalGradient, QPainterPath,
)
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QWidget, QGridLayout, QListWidgetItem,
    QAbstractItemView, QSizePolicy,
)

from .style import current_style
from .custom_button import CustomButton
from .custom_checkbox import CustomCheckBox
from .custom_combo_box import CustomComboBox
from .custom_line_edit import CustomLineEdit
from .rounded_rect import rounded_rect_path
from .theme import Theme, contrast_text
from .themed_list import ThemedListWidget, ROLE_GLYPH, ROLE_SECONDARY, draw_glyph


# ---------------------------------------------------------------------------
# Base dialog
# ---------------------------------------------------------------------------

class ThemedDialog(QDialog):
    def __init__(self, title: str, parent=None, margins: int = 20):
        super().__init__(parent)
        self.setWindowTitle(title)
        a = current_style()
        self._appearance = a
        self._theme = Theme(a)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(margins + 2, margins, margins + 2, margins)
        self.lay.setSpacing(10)
        self.title_label = QLabel(title)
        self.title_label.setStyleSheet(f"QLabel {{ color: {a.card_text_color}; font-size: 16px; font-weight: bold; }}")
        if title:
            self.lay.addWidget(self.title_label)

    def label(self, text: str, dim: bool = False) -> QLabel:
        lab = QLabel(text)
        lab.setTextFormat(Qt.PlainText)
        lab.setWordWrap(True)
        color = QColor(self._appearance.card_text_color)
        if dim:
            color.setAlphaF(0.7)
        lab.setStyleSheet(f"QLabel {{ color: {color.name(QColor.HexArgb)}; }}")
        return lab

    def button_row(self, ok_label: str, cancel_label: str = "Cancel") -> tuple[QHBoxLayout, CustomButton]:
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = CustomButton(cancel_label)
        cancel.clicked.connect(self.reject)
        ok = CustomButton(ok_label)
        ok.clicked.connect(self.accept)
        for b in (cancel, ok):
            b.setMinimumWidth(90)
            b.setMinimumHeight(30)
            row.addWidget(b)
        self.ok_button = ok
        return row, ok

    def mousePressEvent(self, event) -> None:
        # Frameless: drag the dialog by any empty spot. startSystemMove()
        # is what works on Wayland, where a window can't place itself.
        if event.button() == Qt.LeftButton and self.windowHandle() is not None:
            if self.windowHandle().startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        radius = self._appearance.rounded_corner_radius if self._appearance.rounded_corners_enabled else 12
        path = rounded_rect_path(r, radius)
        p.fillPath(path, self._theme.library_background())
        p.setPen(QPen(self._theme.outline(), 2))
        p.drawPath(path)
        p.end()


# ---------------------------------------------------------------------------
# Text prompt
# ---------------------------------------------------------------------------

class TextInputDialog(ThemedDialog):
    def __init__(self, title: str, text: str, default: str = "", parent=None,
                 ok_label: str = "OK", placeholder: str = ""):
        super().__init__(title, parent)
        if text:
            self.lay.addWidget(self.label(text))
        self.edit = CustomLineEdit(default)
        self.edit.setMinimumHeight(30)
        if placeholder:
            self.edit.setPlaceholderText(placeholder)
        self.edit.selectAll()
        self.edit.returnPressed.connect(self.accept)
        self.lay.addWidget(self.edit)
        row, _ok = self.button_row(ok_label)
        self.lay.addLayout(row)
        self.setMinimumWidth(380)
        self.edit.setFocus()

    def value(self) -> str:
        return self.edit.text().strip()


def ask_text(parent, title: str, text: str, default: str = "", ok_label: str = "OK",
             placeholder: str = "") -> "str | None":
    """Themed QInputDialog.getText: the stripped text, or None when
    cancelled or left empty."""
    dlg = TextInputDialog(title, text, default, parent, ok_label, placeholder)
    if dlg.exec() == QDialog.Accepted and dlg.value():
        return dlg.value()
    return None


def get_text(parent, title: str, label: str, text: str = "") -> tuple[str, bool]:
    """Same return shape as QInputDialog.getText: (text, ok)."""
    dlg = TextInputDialog(title, label, text, parent)
    ok = dlg.exec() == QDialog.Accepted
    return (dlg.edit.text() if ok else "", ok)


# ---------------------------------------------------------------------------
# Colour picker
# ---------------------------------------------------------------------------

# Where the colour picker keeps its recently used colours.  Set per app if wanted:
#   kit.themed_dialogs.RECENT_COLORS_FILE = Path(...)  (None = keep them in memory only)
RECENT_COLORS_FILE: "Path | None" = None
_recent_memory: list = []


def _recent_file() -> "Path | None":
    if RECENT_COLORS_FILE is not None:
        return Path(RECENT_COLORS_FILE)
    from PySide6.QtCore import QStandardPaths
    loc = QStandardPaths.writableLocation(QStandardPaths.AppConfigLocation)
    return Path(loc) / "recent_colors.json" if loc else None
_PRESETS = [
    "#000000", "#ffffff", "#7f7f7f", "#ff3b30", "#ff9500", "#ffcc00", "#34c759",
    "#00c7be", "#32ade6", "#007aff", "#5856d6", "#af52de", "#ff2d55", "#a2845e",
]


def _load_recent() -> list[str]:
    f = _recent_file()
    if f is None:
        return list(_recent_memory)
    try:
        data = json.loads(f.read_text())
        return [c for c in data if isinstance(c, str) and QColor(c).isValid()][:14]
    except Exception:
        return list(_recent_memory)


def _save_recent(color: QColor) -> None:
    name = color.name(QColor.HexArgb) if color.alpha() < 255 else color.name()
    recent = [c for c in _load_recent() if c.lower() != name.lower()]
    recent.insert(0, name)
    _recent_memory[:] = recent[:14]
    f = _recent_file()
    if f is None:
        return
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(recent[:14]))
    except OSError:
        pass


def _checker(p: QPainter, rect: QRectF, size: int = 6) -> None:
    p.save()
    p.setClipRect(rect)
    p.fillRect(rect, QColor("#d8d8d8"))
    y = rect.top()
    row = 0
    while y < rect.bottom():
        x = rect.left() + (size if row % 2 else 0)
        while x < rect.right():
            p.fillRect(QRectF(x, y, size, size), QColor("#a8a8a8"))
            x += size * 2
        y += size
        row += 1
    p.restore()


class _SVSquare(QWidget):
    changed = Signal()

    def __init__(self, picker: "ColorPickerDialog"):
        super().__init__()
        self._picker = picker
        self.setMinimumSize(240, 180)
        self.setCursor(Qt.CrossCursor)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = rounded_rect_path(r, 8)
        p.setClipPath(path)
        hue = QColor.fromHsvF(max(0.0, self._picker.h), 1, 1)
        g = QLinearGradient(r.topLeft(), r.topRight())
        g.setColorAt(0, QColor(255, 255, 255))
        g.setColorAt(1, hue)
        p.fillRect(r, g)
        g2 = QLinearGradient(r.topLeft(), r.bottomLeft())
        g2.setColorAt(0, QColor(0, 0, 0, 0))
        g2.setColorAt(1, QColor(0, 0, 0, 255))
        p.fillRect(r, g2)
        p.setClipping(False)
        x = r.left() + self._picker.s * r.width()
        y = r.top() + (1 - self._picker.v) * r.height()
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, 160), 3))
        p.drawEllipse(QPointF(x, y), 7, 7)
        p.setPen(QPen(QColor(255, 255, 255), 2))
        p.drawEllipse(QPointF(x, y), 7, 7)
        p.end()

    def _set(self, pos) -> None:
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        self._picker.s = min(1.0, max(0.0, (pos.x() - r.left()) / r.width()))
        self._picker.v = min(1.0, max(0.0, 1 - (pos.y() - r.top()) / r.height()))
        self.changed.emit()

    def mousePressEvent(self, e) -> None:
        self._set(e.position())

    def mouseMoveEvent(self, e) -> None:
        if e.buttons() & Qt.LeftButton:
            self._set(e.position())


class _Strip(QWidget):
    """Horizontal hue or alpha slider."""
    changed = Signal()

    def __init__(self, picker: "ColorPickerDialog", kind: str):
        super().__init__()
        self._picker = picker
        self.kind = kind
        self.setFixedHeight(18)
        self.setMinimumWidth(200)
        self.setCursor(Qt.PointingHandCursor)

    def _value(self) -> float:
        return self._picker.h if self.kind == "hue" else self._picker.a

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(7, 2, -7, -2)
        path = rounded_rect_path(r, r.height() / 2)
        p.setClipPath(path)
        g = QLinearGradient(r.topLeft(), r.topRight())
        if self.kind == "hue":
            for i in range(7):
                g.setColorAt(i / 6, QColor.fromHsvF((i / 6) % 1.0, 1, 1))
        else:
            _checker(p, r, 5)
            c = self._picker.current_color()
            c0 = QColor(c)
            c0.setAlpha(0)
            c.setAlpha(255)
            g.setColorAt(0, c0)
            g.setColorAt(1, c)
        p.fillRect(r, g)
        p.setClipping(False)
        x = r.left() + self._value() * r.width()
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, 160), 3))
        p.drawEllipse(QPointF(x, r.center().y()), 7, 7)
        p.setPen(QPen(QColor(255, 255, 255), 2))
        p.drawEllipse(QPointF(x, r.center().y()), 7, 7)
        p.end()

    def _set(self, pos) -> None:
        r = QRectF(self.rect()).adjusted(7, 2, -7, -2)
        v = min(1.0, max(0.0, (pos.x() - r.left()) / r.width()))
        if self.kind == "hue":
            self._picker.h = min(v, 0.9999)
        else:
            self._picker.a = v
        self.changed.emit()

    def mousePressEvent(self, e) -> None:
        self._set(e.position())

    def mouseMoveEvent(self, e) -> None:
        if e.buttons() & Qt.LeftButton:
            self._set(e.position())


class _Swatch(QWidget):
    clicked = Signal(QColor)

    def __init__(self, color: QColor, size: int = 22):
        super().__init__()
        self.color = QColor(color)
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(self.color.name(QColor.HexArgb) if self.color.alpha() < 255 else self.color.name())

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        path = rounded_rect_path(r, 5)
        p.setClipPath(path)
        if self.color.alpha() < 255:
            _checker(p, r, 4)
        p.fillRect(r, self.color)
        p.setClipping(False)
        p.setPen(QPen(QColor(255, 255, 255, 90), 1))
        p.drawPath(path)
        p.end()

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self.clicked.emit(QColor(self.color))


class _Preview(QWidget):
    """Old colour on the left, new on the right."""

    def __init__(self, picker: "ColorPickerDialog"):
        super().__init__()
        self._picker = picker
        self.setFixedSize(84, 40)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = rounded_rect_path(r, 8)
        p.setClipPath(path)
        _checker(p, r, 5)
        half = QRectF(r.left(), r.top(), r.width() / 2, r.height())
        p.fillRect(half, self._picker.initial)
        p.fillRect(half.translated(r.width() / 2, 0), self._picker.current_color())
        p.setClipping(False)
        p.setPen(QPen(self._picker._theme.border(), 1.5))
        p.drawPath(path)
        p.end()


class ColorPickerDialog(ThemedDialog):
    def __init__(self, initial: QColor, parent=None, title: str = "Choose Color", alpha: bool = False):
        super().__init__(title, parent)
        initial = QColor(initial) if initial is not None and QColor(initial).isValid() else QColor("#ffffff")
        if not alpha:
            initial.setAlpha(255)
        self.initial = QColor(initial)
        self.alpha_enabled = alpha
        h, s, v, a = initial.getHsvF()
        self.h = h if h >= 0 else 0.0
        self.s, self.v, self.a = s, v, a
        self._syncing = False

        self.square = _SVSquare(self)
        self.square.changed.connect(self._changed)
        self.lay.addWidget(self.square, 1)
        self.hue = _Strip(self, "hue")
        self.hue.changed.connect(self._changed)
        self.lay.addWidget(self.hue)
        self.alpha_strip = None
        if alpha:
            self.alpha_strip = _Strip(self, "alpha")
            self.alpha_strip.changed.connect(self._changed)
            self.lay.addWidget(self.alpha_strip)

        row = QHBoxLayout()
        self.preview = _Preview(self)
        row.addWidget(self.preview)
        row.addSpacing(6)
        self.hex_edit = CustomLineEdit()
        self.hex_edit.setMinimumHeight(30)
        self.hex_edit.setPlaceholderText("#rrggbbaa" if alpha else "#rrggbb")
        self.hex_edit.editingFinished.connect(self._hex_entered)
        self.hex_edit.returnPressed.connect(self._hex_entered)
        row.addWidget(self.hex_edit, 1)
        self.lay.addLayout(row)

        self.lay.addWidget(self.label("Swatches", dim=True))
        self.lay.addLayout(self._swatch_grid(_PRESETS))
        recent = _load_recent()
        if recent:
            self.lay.addWidget(self.label("Recent", dim=True))
            self.lay.addLayout(self._swatch_grid(recent))

        buttons, _ok = self.button_row("Select")
        self.lay.addLayout(buttons)
        self.setMinimumWidth(340)
        self._changed()

    def _swatch_grid(self, colors) -> QGridLayout:
        grid = QGridLayout()
        grid.setSpacing(5)
        for i, c in enumerate(colors):
            sw = _Swatch(QColor(c))
            sw.clicked.connect(self.set_color)
            grid.addWidget(sw, i // 7, i % 7)
        grid.setColumnStretch(7, 1)
        return grid

    def current_color(self) -> QColor:
        return QColor.fromHsvF(self.h, self.s, self.v, self.a if self.alpha_enabled else 1.0)

    def set_color(self, c: QColor) -> None:
        h, s, v, a = QColor(c).getHsvF()
        if h >= 0:
            self.h = h
        self.s, self.v = s, v
        self.a = a if self.alpha_enabled else 1.0
        self._changed()

    @staticmethod
    def _hex_of(c: QColor, alpha: bool) -> str:
        if alpha and c.alpha() < 255:
            return "#%02x%02x%02x%02x" % (c.red(), c.green(), c.blue(), c.alpha())
        return c.name()

    def _changed(self) -> None:
        self._syncing = True
        self.hex_edit.setText(self._hex_of(self.current_color(), self.alpha_enabled))
        self._syncing = False
        for w in (self.square, self.hue, self.alpha_strip, self.preview):
            if w is not None:
                w.update()

    def _hex_entered(self) -> None:
        if self._syncing:
            return
        t = self.hex_edit.text().strip().lstrip("#")
        if re.fullmatch(r"[0-9a-fA-F]{3}", t):
            t = "".join(ch * 2 for ch in t)
        if re.fullmatch(r"[0-9a-fA-F]{6}", t):
            c = QColor("#" + t)
            c.setAlphaF(self.a if self.alpha_enabled else 1.0)
            self.set_color(c)
        elif self.alpha_enabled and re.fullmatch(r"[0-9a-fA-F]{8}", t):
            c = QColor("#" + t[:6])
            c.setAlpha(int(t[6:], 16))
            self.set_color(c)
        else:
            self._changed()   # put the valid value back

    def accept(self) -> None:
        _save_recent(self.current_color())
        super().accept()


def get_color(initial=None, parent=None, title: str = "Choose Color", alpha: bool = False) -> QColor:
    """Themed QColorDialog.getColor: an invalid QColor when cancelled."""
    dlg = ColorPickerDialog(initial, parent, title, alpha)
    if dlg.exec() == QDialog.Accepted:
        return dlg.current_color()
    return QColor()


# ---------------------------------------------------------------------------
# File picker
# ---------------------------------------------------------------------------

_VIDEO = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".flv", ".m4v", ".ts", ".wmv"}
_AUDIO = {".wav", ".mp3", ".ogg", ".flac", ".m4a", ".aac", ".opus", ".wma"}
_IMAGE = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".tif", ".tiff"}
_TEXT = {".txt", ".toml", ".json", ".md", ".ini", ".cfg", ".log", ".srt", ".ass", ".vtt"}
_last_dir: "str | None" = None


def _glyph_for(path: Path, is_dir: bool) -> str:
    if is_dir:
        return "folder"
    ext = path.suffix.lower()
    if ext in _VIDEO:
        return "video"
    if ext in _AUDIO:
        return "audio"
    if ext in _IMAGE:
        return "image"
    if ext in _TEXT:
        return "text"
    return "file"


def parse_filters(filter_str: str) -> list[tuple[str, list[str]]]:
    """"Audio (*.wav *.mp3);;All Files (*)" -> [("Audio (*.wav *.mp3)", ["*.wav", "*.mp3"]), ...]"""
    out = []
    for part in (filter_str or "").split(";;"):
        part = part.strip()
        if not part:
            continue
        m = re.search(r"\(([^)]*)\)", part)
        pats = m.group(1).split() if m else part.split()
        out.append((part, pats or ["*"]))
    return out or [("All Files (*)", ["*"])]


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


# The file picker's sidebar after the standard folders: [(label, path, glyph)], glyph one of
# "folder" "video" "music" "image" "drive" "home" -- e.g. afterglow added ("Clips", clips_dir, "video").
EXTRA_PLACES: "list[tuple[str, str, str]]" = []


def _places() -> list[tuple[str, str, str]]:
    home = Path.home()
    out = [("Home", str(home), "home")]
    for name in ("Desktop", "Documents", "Downloads", "Videos", "Music", "Pictures"):
        p = home / name
        if p.is_dir():
            out.append((name, str(p), "folder"))
    for label, path, glyph in EXTRA_PLACES:          # the app's own folders (see EXTRA_PLACES)
        if Path(path).expanduser().is_dir():
            out.append((label, str(Path(path).expanduser()), glyph or "folder"))
    out.append(("Computer", "/", "drive"))
    return out


class FilePickerDialog(ThemedDialog):
    """mode: "open" | "open_many" | "save" | "dir"."""

    def __init__(self, parent=None, caption: str = "", directory: str = "", filters: str = "",
                 mode: str = "open", selected_filter: str = ""):
        default_caption = {"open": "Open File", "open_many": "Open Files",
                           "save": "Save As", "dir": "Choose Folder"}[mode]
        super().__init__(caption or default_caption, parent, margins=18)
        self.mode = mode
        self.filters = parse_filters(filters) if mode != "dir" else [("Folders", ["*"])]
        self.result_paths: list[str] = []
        self.history: list[str] = []
        self._entries: list[tuple[str, bool]] = []

        base = _last_dir or str(Path.home())
        start = os.path.expanduser(directory or "") or base
        if not os.path.isabs(start):
            start = os.path.join(base, start)   # e.g. a bare suggested file name
        start_name = ""
        sp = Path(start)
        if not sp.is_dir():
            if sp.parent.is_dir():
                start_name = sp.name
                sp = sp.parent
            else:
                sp = Path.home()
        self.cwd = str(sp.resolve())

        # --- top bar: back / up / path field
        top = QHBoxLayout()
        top.setSpacing(6)
        self.back_btn = CustomButton("‹")
        self.back_btn.setToolTip("Back")
        self.back_btn.clicked.connect(self.go_back)
        self.up_btn = CustomButton("↑")
        self.up_btn.setToolTip("Up one folder")
        self.up_btn.clicked.connect(self.go_up)
        for b in (self.back_btn, self.up_btn):
            b.setFixedSize(32, 30)
            top.addWidget(b)
        self.path_edit = CustomLineEdit(self.cwd)
        self.path_edit.setMinimumHeight(30)
        self.path_edit.returnPressed.connect(self._path_entered)
        top.addWidget(self.path_edit, 1)
        self.lay.addLayout(top)

        # --- places + file list
        mid = QHBoxLayout()
        mid.setSpacing(10)
        self.places = ThemedListWidget(row_height=30, background=self._theme.card_background())
        self.places.setFixedWidth(150)
        for label, path, glyph in _places():
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, path)
            it.setData(ROLE_GLYPH, glyph)
            it.setToolTip(path)
            self.places.addItem(it)
        self.places.itemClicked.connect(lambda it: self.navigate(it.data(Qt.UserRole)))
        mid.addWidget(self.places)
        self.list = ThemedListWidget(row_height=30, background=self._theme.card_background())
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection if mode == "open_many"
                                   else QAbstractItemView.SingleSelection)
        self.list.itemActivated.connect(self._activated)
        self.list.itemSelectionChanged.connect(self._selection_changed)
        self.list.setMinimumSize(420, 300)
        mid.addWidget(self.list, 1)
        self.lay.addLayout(mid, 1)

        # --- name + filter row
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        name_label = self.label("Folder:" if mode == "dir" else "Name:")
        name_label.setWordWrap(False)
        bottom.addWidget(name_label)
        self.name_edit = CustomLineEdit(start_name)
        self.name_edit.setMinimumHeight(30)
        self.name_edit.returnPressed.connect(self.accept)
        if mode == "dir":
            self.name_edit.setPlaceholderText("(this folder)")
        bottom.addWidget(self.name_edit, 1)
        self.filter_combo = CustomComboBox()
        for label, _pats in self.filters:
            self.filter_combo.addItem(label)
        if selected_filter:
            i = self.filter_combo.findText(selected_filter)
            if i >= 0:
                self.filter_combo.setCurrentIndex(i)
        if self.filter_combo.count():                    # wide enough for its longest filter name
            fm = self.filter_combo.fontMetrics()
            longest = max(fm.horizontalAdvance(self.filter_combo.itemText(i)) for i in range(self.filter_combo.count()))
            self.filter_combo.setMinimumWidth(min(320, longest + 56))
        self.filter_combo.currentIndexChanged.connect(lambda _i: self.refresh())
        self.filter_combo.setVisible(mode != "dir" and len(self.filters) > 0)
        bottom.addWidget(self.filter_combo)
        self.lay.addLayout(bottom)

        last = QHBoxLayout()
        self.hidden_box = CustomCheckBox("Show hidden files")
        self.hidden_box.toggled.connect(lambda _c: self.refresh())
        last.addWidget(self.hidden_box)
        last.addStretch(1)
        ok_label = {"open": "Open", "open_many": "Open", "save": "Save", "dir": "Choose Folder"}[mode]
        buttons, _ok = self.button_row(ok_label)
        last.addLayout(buttons)
        self.lay.addLayout(last)

        self.resize(760, 520)
        self.navigate(self.cwd, record=False)
        if start_name:
            self._select_name(start_name)
        (self.name_edit if mode == "save" else self.list).setFocus()

    # --- navigation ------------------------------------------------------
    def current_patterns(self) -> list[str]:
        if self.mode == "dir":
            return ["*"]
        i = max(0, self.filter_combo.currentIndex())
        return self.filters[i][1] if i < len(self.filters) else ["*"]

    def navigate(self, path: str, record: bool = True) -> None:
        p = Path(os.path.expanduser(path))
        if not p.is_dir():
            return
        try:
            p = p.resolve()
            os.listdir(p)
        except OSError as e:
            from .custom_message_dialog import show_message
            show_message(self, "Can't Open Folder", str(e))
            return
        if record and str(p) != self.cwd:
            self.history.append(self.cwd)
        self.cwd = str(p)
        self.path_edit.setText(self.cwd)
        self.back_btn.setEnabled(bool(self.history))
        self.up_btn.setEnabled(p.parent != p)
        for i in range(self.places.count()):
            it = self.places.item(i)
            it.setSelected(it.data(Qt.UserRole) == self.cwd)
        self.refresh()

    def go_back(self) -> None:
        if self.history:
            self.navigate(self.history.pop(), record=False)

    def go_up(self) -> None:
        p = Path(self.cwd)
        if p.parent != p:
            child = p.name
            self.navigate(str(p.parent))
            self._select_name(child)

    def refresh(self) -> None:
        show_hidden = self.hidden_box.isChecked()
        pats = [x.lower() for x in self.current_patterns()]
        dirs, files = [], []
        try:
            with os.scandir(self.cwd) as it:
                for e in it:
                    if not show_hidden and e.name.startswith("."):
                        continue
                    try:
                        is_dir = e.is_dir()
                    except OSError:
                        continue
                    if is_dir:
                        dirs.append(e)
                    elif self.mode != "dir" and any(fnmatch.fnmatch(e.name.lower(), pt) for pt in pats):
                        files.append(e)
        except OSError:
            pass
        key = lambda e: e.name.lower()
        self.list.clear()
        self._entries = []
        for e in sorted(dirs, key=key):
            it = QListWidgetItem(e.name)
            it.setData(Qt.UserRole, e.path)
            it.setData(Qt.UserRole + 1, True)
            it.setData(ROLE_GLYPH, "folder")
            self.list.addItem(it)
        for e in sorted(files, key=key):
            it = QListWidgetItem(e.name)
            it.setData(Qt.UserRole, e.path)
            it.setData(Qt.UserRole + 1, False)
            it.setData(ROLE_GLYPH, _glyph_for(Path(e.name), False))
            try:
                st = e.stat()
                it.setData(ROLE_SECONDARY, f"{_human_size(st.st_size)}   "
                                           f"{datetime.fromtimestamp(st.st_mtime):%Y-%m-%d %H:%M}")
            except OSError:
                pass
            self.list.addItem(it)

    def _select_name(self, name: str) -> None:
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.text() == name:
                self.list.setCurrentItem(it)
                self.list.scrollToItem(it, QAbstractItemView.PositionAtCenter)
                return

    def _path_entered(self) -> None:
        t = os.path.expanduser(self.path_edit.text().strip())
        if not t:
            return
        p = Path(t) if os.path.isabs(t) else Path(self.cwd) / t
        if p.is_dir():
            self.navigate(str(p))
        elif self.mode != "dir" and (p.is_file() or (self.mode == "save" and p.parent.is_dir())):
            self.navigate(str(p.parent))
            self.name_edit.setText(p.name)
            self.accept()
        else:
            self.path_edit.setText(self.cwd)

    def _activated(self, item) -> None:
        if item.data(Qt.UserRole + 1):
            self.navigate(item.data(Qt.UserRole))
        else:
            self.accept()

    def _selection_changed(self) -> None:
        items = self.list.selectedItems()
        if self.mode == "dir":
            dirs = [it for it in items if it.data(Qt.UserRole + 1)]
            self.name_edit.setText(dirs[0].text() if dirs else "")
            return
        files = [it for it in items if not it.data(Qt.UserRole + 1)]
        if not files:
            return
        if self.mode == "open_many" and len(files) > 1:
            self.name_edit.setText(" ".join(f'"{it.text()}"' for it in files))
        else:
            self.name_edit.setText(files[0].text())

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Backspace and not self.name_edit.hasFocus() and not self.path_edit.hasFocus():
            self.go_up()
            return
        if event.key() == Qt.Key_H and event.modifiers() & Qt.ControlModifier:
            self.hidden_box.setChecked(not self.hidden_box.isChecked())
            return
        super().keyPressEvent(event)

    # --- result ----------------------------------------------------------
    def _names(self) -> list[str]:
        t = self.name_edit.text().strip()
        if not t:
            return []
        quoted = re.findall(r'"([^"]+)"', t)
        return quoted if quoted else [t]

    def _resolve(self, name: str) -> Path:
        name = os.path.expanduser(name)
        return Path(name) if os.path.isabs(name) else Path(self.cwd) / name

    def accept(self) -> None:
        global _last_dir
        if self.mode == "dir":
            names = self._names()
            target = self._resolve(names[0]) if names else Path(self.cwd)
            if not target.is_dir():
                return
            self.result_paths = [str(target)]
            _last_dir = str(target.parent if names else target)
            super().accept()
            return
        names = self._names()
        if not names:
            sel = [it for it in self.list.selectedItems() if it.data(Qt.UserRole + 1)]
            if sel:
                self.navigate(sel[0].data(Qt.UserRole))
            return
        paths = [self._resolve(n) for n in names]
        if len(paths) == 1 and paths[0].is_dir():
            self.navigate(str(paths[0]))
            self.name_edit.clear()
            return
        if self.mode == "save":
            target = paths[0]
            pats = self.current_patterns()
            exts = [pt[1:] for pt in pats if re.fullmatch(r"\*\.[A-Za-z0-9]+", pt)]
            if not target.suffix and exts:
                target = target.with_name(target.name + exts[0])
            if not target.parent.is_dir():
                return
            if target.exists():
                from .custom_message_dialog import ask_confirm
                if not ask_confirm(self, "Replace File?",
                                   f"“{target.name}” already exists. Replace it?", "Replace"):
                    return
            self.result_paths = [str(target)]
        else:
            missing = [p for p in paths if not p.is_file()]
            if missing:
                from .custom_message_dialog import show_message
                show_message(self, "File Not Found", f"“{missing[0].name}” doesn't exist here.")
                return
            self.result_paths = [str(p) for p in (paths if self.mode == "open_many" else paths[:1])]
        _last_dir = self.cwd
        super().accept()

    def selected_filter(self) -> str:
        if self.mode == "dir" or not self.filters:
            return ""
        return self.filters[max(0, self.filter_combo.currentIndex())][0]


def get_open_file_name(parent=None, caption: str = "", directory: str = "", filter: str = "",
                       selected_filter: str = "") -> tuple[str, str]:
    dlg = FilePickerDialog(parent, caption, directory, filter, "open", selected_filter)
    if dlg.exec() == QDialog.Accepted and dlg.result_paths:
        return dlg.result_paths[0], dlg.selected_filter()
    return "", ""


def get_open_file_names(parent=None, caption: str = "", directory: str = "", filter: str = "",
                        selected_filter: str = "") -> tuple[list[str], str]:
    dlg = FilePickerDialog(parent, caption, directory, filter, "open_many", selected_filter)
    if dlg.exec() == QDialog.Accepted and dlg.result_paths:
        return list(dlg.result_paths), dlg.selected_filter()
    return [], ""


def get_save_file_name(parent=None, caption: str = "", directory: str = "", filter: str = "",
                       selected_filter: str = "") -> tuple[str, str]:
    dlg = FilePickerDialog(parent, caption, directory, filter, "save", selected_filter)
    if dlg.exec() == QDialog.Accepted and dlg.result_paths:
        return dlg.result_paths[0], dlg.selected_filter()
    return "", ""


def get_existing_directory(parent=None, caption: str = "", directory: str = "") -> str:
    dlg = FilePickerDialog(parent, caption, directory, "", "dir")
    if dlg.exec() == QDialog.Accepted and dlg.result_paths:
        return dlg.result_paths[0]
    return ""
