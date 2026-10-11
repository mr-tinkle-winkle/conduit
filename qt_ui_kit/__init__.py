"""
qt_ui_kit -- the custom-painted PySide6 widgets and theming extracted from afterglow, for giving
other apps the same look.  Dark black-and-white by default; every icon is text unless the app
supplies images.  Read UI_THEMING_GUIDE.md (next to this package) before building with it.

    import qt_ui_kit as kit
    app = QApplication(sys.argv)
    kit.install(app)                                  # style + palette + tooltips/menus/scroll bars + wheel guard
    btn = kit.CustomButton("Save")
"""
from __future__ import annotations

__version__ = "1.0.0"

from .style import Style, current_style, set_style, update_style, on_style_changed
from . import presets, icons
from .theme import Theme, contrast_text
from .rounded_rect import rounded_rect_path, round_pixmap_corners
from .press_pulse import PressPulse, scaled_cached
from .custom_button import CustomButton
from .custom_checkbox import CustomCheckBox
from .custom_radio_button import CustomRadioButton
from .custom_combo_box import CustomComboBox, paint_dropdown_field
from .custom_combo_style import combo_box_stylesheet
from .custom_line_edit import CustomLineEdit
from .custom_spinbox import CustomSpinBox, CustomDoubleSpinBox
from .custom_group_box import CustomGroupBox
from .custom_scrollbar import CustomScrollBar
from .smooth_scroll_area import SmoothScrollArea
from .themed_frame import ThemedFrame
from .themed_list import ThemedListWidget, ThemedItemDelegate
from .collapse_toggle_button import CollapseToggleButton
from .segment_button import SegmentButton
from .outlined_label import OutlinedLabel
from .search_bubble import SearchBubble
from .popover import TabbedPopover
from .check_list import CheckListPopup, TriStateCheckBox, STATE_NONE, STATE_INCLUDE, STATE_EXCLUDE
from .menus import menu_stylesheet, StayOpenMenu, themed_menu
from .themed_dialogs import (ThemedDialog, TextInputDialog, ask_text, get_text, ColorPickerDialog, get_color,
                             FilePickerDialog, get_open_file_name, get_open_file_names, get_save_file_name,
                             get_existing_directory)
from .custom_message_dialog import CustomMessageDialog, show_message, ask_confirm
from .progress import ThemedProgressBar, ProgressDialog
from .trim_timeline import TrimTimeline
from .volume_bar import VolumeBar
from .gradient_backdrop import GradientBackdrop
from .page_outline import paint_page_outline
from .scale_reveal import crossfade_to_index, reveal_from_point, animate_popup_from_point
from .pulse_animation import PulseAnimator
from .pixmap_effects import tint_pixmap, tint_pixmap_cached, hue_shift_pixmap, silhouette_outline_pixmap
from .scaling import compute_scale


def apply_palette(app=None, style: "Style | None" = None) -> None:
    """Make Qt's own palette match the style, so anything NOT custom-painted (plain QLabels, native
    widgets, text cursors) agrees with the kit.  Uses QPalette, never an app-wide stylesheet (an
    unscoped stylesheet cascades into every custom-painted widget -- see the guide)."""
    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QApplication
    app = app or QApplication.instance()
    s = style or current_style()
    if not s.fixed_colors:
        return                              # following the desktop palette: leave it alone
    pal = app.palette()
    roles = {
        QPalette.Window: s.window, QPalette.WindowText: s.text, QPalette.Base: s.surface,
        QPalette.AlternateBase: s.base, QPalette.Text: s.text, QPalette.Button: s.accent,
        QPalette.ButtonText: contrast_text(QColor(s.accent)).name(), QPalette.Highlight: s.secondary,
        QPalette.HighlightedText: contrast_text(QColor(s.secondary)).name(), QPalette.ToolTipBase: s.surface,
        QPalette.ToolTipText: s.text, QPalette.PlaceholderText: s.border, QPalette.Mid: s.border,
        QPalette.BrightText: s.outline, QPalette.Link: s.outline,
    }
    for role, value in roles.items():
        pal.setColor(role, QColor(value))
    pal.setColor(QPalette.Disabled, QPalette.WindowText, QColor(s.border))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(s.border))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(s.border))
    app.setPalette(pal)


def install(app=None, style: "Style | None" = None, *, palette: bool = True, chrome: bool = True,
            wheel_guard: bool = True) -> None:
    """One call at start-up, after QApplication exists and before building windows:
      style        set_style(style) (default: the current one -- dark black and white)
      palette      apply_palette()
      chrome       themed tooltips, every QMenu themed, every scroll area's bars custom (app_chrome)
      wheel_guard  the mouse wheel never changes a combo box / spin box by accident

    chrome and wheel_guard install APP-WIDE Python event filters.  Never use them in a process
    that shows QtWebEngine views: PySide6 crashes (see the guide, "QtWebEngine").  Pass
    chrome=False, wheel_guard=False there, or keep web views in a separate process."""
    from PySide6.QtWidgets import QApplication
    app = app or QApplication.instance()
    if style is not None:
        set_style(style)
    if palette:
        apply_palette(app)
    if chrome:
        from . import app_chrome
        app_chrome.install()
    if wheel_guard:
        from . import wheel_guard as wg
        wg.install()
