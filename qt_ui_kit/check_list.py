"""
A multi-select dropdown: a TabbedPopover holding a list of check boxes, optionally grouped under
headings (group boxes), optionally with a per-item leading icon, an "+ Add" button at the bottom,
and optionally a THIRD state per item -- right-click to "block" (an x instead of a check) -- for
include / exclude filtering.  It stays open while several items are toggled and closes on an
outside click.

    pop = CheckListPopup(
        {"Games": ["Valorant", "Minecraft"], None: ["Favorites"]},   # None = no heading
        checked={"Minecraft"}, title="Tags", tri_state=True, add_label="+ Add Tag")
    pop.state_changed.connect(lambda key, state: ...)    # state: "none" | "include" | "exclude"
    pop.add_requested.connect(...)
    pop.show_below(button)

(afterglow uses this as its Filters selector: tags grouped by category, blocked tags hide clips.)
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .custom_button import CustomButton
from .custom_checkbox import CustomCheckBox
from .custom_group_box import CustomGroupBox
from .icons import icon_pixmap
from .popover import TabbedPopover
from .theme import Theme

STATE_NONE = "none"
STATE_INCLUDE = "include"
STATE_EXCLUDE = "exclude"


class TriStateCheckBox(CustomCheckBox):
    """Left click: include (check) / none.  Right click: exclude (an x) / none.  The label gets
    " (blocked)" while excluded.  ``state_changed(key, state)``."""
    state_changed = Signal(str, str)

    def __init__(self, text: str, key: "str | None" = None, parent=None, leading_icon=None):
        super().__init__(text, parent, leading_icon=leading_icon)
        self._key = key if key is not None else text
        self._label = text
        self._state = STATE_NONE
        self._x_icon = icon_pixmap("block", 128)
        self.toggled.connect(self._on_toggled)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_right_click)

    @property
    def state(self) -> str:
        return self._state

    def _icon_for_state(self):
        if self._state == STATE_EXCLUDE:
            return self._x_icon
        if self._state == STATE_INCLUDE:
            return self._checkmark
        return None

    def set_state(self, state: str) -> None:
        self._state = state
        self.blockSignals(True)
        self.setChecked(state == STATE_INCLUDE)
        self.blockSignals(False)
        self._refresh_label()

    def _refresh_label(self) -> None:
        self.setText(f"{self._label} (blocked)" if self._state == STATE_EXCLUDE else self._label)
        self.update()

    def _on_toggled(self, checked: bool) -> None:
        self._state = STATE_INCLUDE if checked else STATE_NONE
        self._refresh_label()
        self.state_changed.emit(self._key, self._state)

    def _on_right_click(self, _pos) -> None:
        self._state = STATE_NONE if self._state == STATE_EXCLUDE else STATE_EXCLUDE
        self.blockSignals(True)
        self.setChecked(False)
        self.blockSignals(False)
        self._refresh_label()
        self.state_changed.emit(self._key, self._state)


class CheckListPopup(TabbedPopover):
    toggled = Signal(str, bool)          # key, checked (plain two-state use)
    state_changed = Signal(str, str)     # key, "none" | "include" | "exclude"
    add_requested = Signal()
    closed = Signal()

    def __init__(self, items, checked=(), *, excluded=(), title: str = "Select", tri_state: bool = False,
                 icons: "dict[str, QPixmap | str] | None" = None, add_label: "str | None" = None,
                 empty_text: str = "(nothing yet)", width: int = 320, parent=None):
        """items: {heading or None: [label, ...]} (dict order kept), or a plain list of labels.
        icons: {label: QPixmap or image path} shown before the label."""
        super().__init__(parent, tab_labels=(title,), width=width)
        if not isinstance(items, dict):
            items = {None: list(items)}
        self.checkboxes: "dict[str, CustomCheckBox]" = {}
        checked, excluded, icons = set(checked), set(excluded), dict(icons or {})
        page = QWidget()
        page.setAttribute(Qt.WA_TranslucentBackground, True)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)

        def make(label: str, target: QVBoxLayout) -> None:
            icon = icons.get(label)
            if isinstance(icon, str):
                icon = QPixmap(icon)
            if icon is not None and icon.isNull():
                icon = None
            if tri_state:
                cb = TriStateCheckBox(label, leading_icon=icon)
                cb.set_state(STATE_EXCLUDE if label in excluded else STATE_INCLUDE if label in checked else STATE_NONE)
                cb.state_changed.connect(self.state_changed.emit)
                cb.state_changed.connect(lambda k, st: self.toggled.emit(k, st == STATE_INCLUDE))
            else:
                cb = CustomCheckBox(label, leading_icon=icon)
                cb.setChecked(label in checked)
                cb.toggled.connect(lambda on, k=label: (self.toggled.emit(k, on),
                                                        self.state_changed.emit(k, STATE_INCLUDE if on else STATE_NONE)))
            target.addWidget(cb)
            self.checkboxes[label] = cb

        if not any(items.values()):
            empty = QLabel(empty_text)
            empty.setStyleSheet(f"QLabel {{ color: {Theme().border().name()}; }}")
            layout.addWidget(empty)
        for heading, labels in items.items():
            if heading is None:
                continue
            group = CustomGroupBox(heading)
            gl = group.make_layout(QVBoxLayout)
            for label in labels:
                make(label, gl)
            layout.addWidget(group)
        for label in items.get(None, []):
            make(label, layout)
        if add_label:
            add = CustomButton(add_label)
            add.clicked.connect(self.add_requested.emit)
            layout.addWidget(add)
        layout.addStretch(1)
        self.set_page_widget(0, page)

    def states(self) -> "dict[str, str]":
        out = {}
        for k, cb in self.checkboxes.items():
            out[k] = cb.state if isinstance(cb, TriStateCheckBox) else (STATE_INCLUDE if cb.isChecked() else STATE_NONE)
        return out

    def hideEvent(self, event) -> None:
        self.closed.emit()
        super().hideEvent(event)
