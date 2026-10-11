"""
Shared QSS reskin for QComboBox -- theme colors (card background,
accent border/highlight, rounded corners) instead of native/KDE
chrome, applied everywhere a QComboBox is used across Settings and
the Add Filter dialog. A genuine custom dropdown (its own popup
widget, not a styled QComboBox) would be a bigger, separate rebuild;
this covers the "fully custom" LOOK for every combo box in the app
without that.
"""
from __future__ import annotations


def combo_box_stylesheet(appearance) -> str:
    return f"""
        QComboBox {{
            background-color: {appearance.afterglow_color_card_background};
            color: {appearance.card_text_color};
            border: 1px solid {appearance.afterglow_color_accent};
            border-radius: 6px;
            padding: 4px 8px;
        }}
        QComboBox::drop-down {{
            border: none;
        }}
        QComboBox QAbstractItemView {{
            background-color: {appearance.afterglow_color_card_background};
            color: {appearance.card_text_color};
            border: 1px solid {appearance.afterglow_color_accent};
            selection-background-color: {appearance.afterglow_color_accent};
            outline: none;
        }}
    """
