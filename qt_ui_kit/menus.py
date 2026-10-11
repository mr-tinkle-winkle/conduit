"""
Themed menus.  ``menu_stylesheet()`` reskins any QMenu (its own submenus too, once each gets it --
Qt doesn't cascade a QMenu's stylesheet into child QMenus); ``app_chrome.install()`` applies it to
every QMenu automatically.  ``StayOpenMenu`` is a QMenu that stays open when an embedded widget
(a CustomCheckBox in a QWidgetAction) is clicked, so several boxes can be ticked in one visit.
"""
from __future__ import annotations

from PySide6.QtWidgets import QMenu, QWidgetAction

from .style import Style, current_style


def menu_stylesheet(style: "Style | None" = None) -> str:
    s = style or current_style()
    return f"""
        QMenu {{
            background-color: {s.surface};
            color: {s.text};
            border: 1px solid {s.outline};
            border-radius: 8px;
            padding: 4px;
        }}
        QMenu::item {{
            padding: 6px 24px 6px 12px;
            border-radius: 6px;
        }}
        QMenu::item:selected {{
            background-color: {s.accent};
        }}
        QMenu::item:disabled {{
            color: {s.border};
        }}
        QMenu::separator {{
            height: 1px;
            background: {s.border};
            margin: 4px 8px;
        }}
    """


# afterglow-era name
_menu_stylesheet = menu_stylesheet


class StayOpenMenu(QMenu):
    """A QMenu that doesn't close when the click lands on a QWidgetAction's embedded widget.

    Belt and braces, because QMenu closes itself from C++ paths a Python override doesn't see:
    the press / release on a widget action are swallowed, AND a hide that follows one is undone
    (re-shown at the same place).  Real closes (outside click, Escape, a plain action) are untouched."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._suppress_next_hide = False
        self.setStyleSheet(menu_stylesheet())

    def _is_widget_action_click(self, pos) -> bool:
        action = self.activeAction() or self.actionAt(pos)
        return isinstance(action, QWidgetAction)

    def hideEvent(self, event) -> None:
        if self._suppress_next_hide:
            self._suppress_next_hide = False
            event.ignore()
            pos = self.pos()
            self.show()
            self.move(pos)
            return
        super().hideEvent(event)

    def mousePressEvent(self, event) -> None:
        if self._is_widget_action_click(event.pos()):
            self._suppress_next_hide = True
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._is_widget_action_click(event.pos()):
            return
        super().mouseReleaseEvent(event)


def themed_menu(title: str = "", parent=None, stay_open: bool = False) -> QMenu:
    m = StayOpenMenu(title, parent) if stay_open else QMenu(title, parent)
    m.setStyleSheet(menu_stylesheet())
    return m
