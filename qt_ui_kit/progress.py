"""A themed progress bar and a progress dialog with Cancel (Escape cancels too).

    dlg = ProgressDialog("Exporting…", parent)
    dlg.cancel_btn.clicked.connect(cancel_event.set)
    worker_signal.connect(dlg.set_fraction)        # 0..1, from a Qt signal (never from a thread directly)
    dlg.exec()                                     # close it with dlg.accept() / dlg.reject()
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QProgressBar

from .custom_button import CustomButton
from .style import current_style
from .themed_dialogs import ThemedDialog


def progress_bar_stylesheet(style=None) -> str:
    s = style or current_style()
    return (f"QProgressBar {{ background-color: {s.surface}; color: {s.text}; border: 1px solid {s.border};"
            f" border-radius: 8px; text-align: center; height: 22px; }}"
            f"QProgressBar::chunk {{ background-color: {s.secondary}; border-radius: 7px; }}")


class ThemedProgressBar(QProgressBar):
    """0..1000 internally; set_fraction(0..1).  Shows ``placeholder`` until the first progress."""

    def __init__(self, placeholder: str = "Preparing…", parent=None):
        super().__init__(parent)
        self.setRange(0, 1000)
        self.setTextVisible(True)
        self.setValue(0)
        self.setFormat(placeholder)
        self.setStyleSheet(progress_bar_stylesheet())

    def set_fraction(self, f: float) -> None:
        v = int(max(0.0, min(1.0, f)) * 1000)
        if v > 0 and self.format() != "%p%":
            self.setFormat("%p%")
        self.setValue(v)


class ProgressDialog(ThemedDialog):
    def __init__(self, title: str, parent=None, cancel_label: str = "Cancel"):
        super().__init__(title, parent)
        self.bar = ThemedProgressBar()
        self.lay.addWidget(self.bar)
        row = QHBoxLayout()
        row.addStretch(1)
        self.cancel_btn = CustomButton(cancel_label)
        row.addWidget(self.cancel_btn)
        self.lay.addLayout(row)
        self.setMinimumWidth(420)

    def set_fraction(self, f: float) -> None:
        self.bar.set_fraction(f)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.cancel_btn.click()
            return
        super().keyPressEvent(event)
