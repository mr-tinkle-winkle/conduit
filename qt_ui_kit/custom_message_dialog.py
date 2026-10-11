"""
Custom-styled replacement for QMessageBox (information / warning /
critical / question) -- the same rounded, theme-coloured, frameless
ThemedDialog look as the rest of the app's dialogs, rather than the
native/KDE message box chrome. show_message() covers the one-button
cases, ask_confirm() the Cancel + confirm ones (the video Delete prompt,
"Replace file?", ...).
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel

from .custom_button import CustomButton
from .themed_dialogs import ThemedDialog

_DANGER = QColor("#d64545")
DIALOG_WIDTH = 420
_RUN = 28          # the longest run of characters without a break opportunity


def _breakable(text: str) -> str:
    """Let a long unbroken token (a file name, a clip title without spaces) wrap instead of being
    clipped: a zero-width space every ``_RUN`` characters."""
    out = []
    for word in text.split(" "):
        while len(word) > _RUN:
            out.append(word[:_RUN] + "\u200b")
            word = word[_RUN:]
        out.append(word)
    return " ".join(o if o.endswith("\u200b") else o for o in out).replace("\u200b ", "\u200b")


class CustomMessageDialog(ThemedDialog):
    def __init__(self, title: str, text: str, parent=None, confirm_label: "str | None" = None,
                 danger: bool = False):
        super().__init__(title, parent)
        text_label = QLabel(_breakable(text))
        text_label.setTextFormat(Qt.PlainText)      # a clip title with "<b>" in it is text, not markup
        text_label.setWordWrap(True)
        text_label.setMinimumWidth(1)
        text_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        text_label.setStyleSheet(f"QLabel {{ color: {self._appearance.card_text_color}; }}")
        self.lay.addWidget(text_label)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        if confirm_label:
            cancel_btn = CustomButton("Cancel")
            cancel_btn.clicked.connect(self.reject)
            cancel_btn.setMinimumWidth(90)
            cancel_btn.setMinimumHeight(30)
            button_row.addWidget(cancel_btn)
        ok_btn = CustomButton(confirm_label or "OK")
        ok_btn.clicked.connect(self.accept)
        ok_btn.setMinimumWidth(90)
        ok_btn.setMinimumHeight(30)
        if danger:
            ok_btn.set_fill_color(_DANGER)
        button_row.addWidget(ok_btn)
        self.ok_button = ok_btn
        self.lay.addLayout(button_row)
        self.text_label = text_label
        self.setFixedWidth(DIALOG_WIDTH)
        self.fit_height()
        # A destructive confirm doesn't fire on a stray Enter; Cancel
        # (Escape) is always the safe way out.
        (cancel_btn if confirm_label and danger else ok_btn).setFocus()

    def fit_height(self) -> None:
        """A word-wrapped label's height depends on the width it gets, and a layout that is only asked
        for its size hint answers for the wrong width, which cuts the last lines off.  The width is
        fixed, so ask for the height at exactly that width and make it the minimum."""
        self.lay.activate()
        h = max(self.lay.heightForWidth(DIALOG_WIDTH), self.lay.minimumSize().height(), self.sizeHint().height())
        self.setMinimumHeight(h)
        self.resize(DIALOG_WIDTH, h)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.fit_height()        # fonts / scaling may differ from construction time

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            focused = self.focusWidget()
            if isinstance(focused, CustomButton):
                focused.click()
                return
        super().keyPressEvent(event)


def show_message(parent, title: str, text: str) -> None:
    """Drop-in replacement for QMessageBox.information/warning/critical
    -- there's no severity-based icon distinction here (this app has no
    error/warning/info icon set of its own), just a consistently
    custom-styled dialog for all three."""
    dialog = CustomMessageDialog(title, text, parent=parent)
    dialog.exec()


def ask_confirm(parent, title: str, text: str, confirm_label: str, danger: bool = False) -> bool:
    """Same styled dialog with Cancel + a confirm button; True if confirmed.
    danger=True paints the confirm button red (Delete and the like)."""
    dialog = CustomMessageDialog(title, text, parent=parent, confirm_label=confirm_label, danger=danger)
    return dialog.exec() == QDialog.Accepted
