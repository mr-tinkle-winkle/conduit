# qt_ui_kit — theming guide

Custom-painted PySide6 widgets and one theming system, extracted from **afterglow** (a clip
library / editor app) so other apps get the same look and behaviour. Defaults: **dark black-and-white**
and **text icons**. Anything else is opt-in (`Style(...)`, `icons.set_icon(...)`).

If you are an assistant building a new app with this kit: read this whole file first. Sections 6
and 7 are lessons that each cost afterglow a real bug; skipping them means re-learning them.

---

## 1. Quick start

```python
import sys
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout
import qt_ui_kit as kit

app = QApplication(sys.argv)
kit.install(app)                        # style + palette + themed tooltips/menus/scroll bars + wheel guard

w = QWidget(); w.setAutoFillBackground(True)          # window colour comes from the palette
lay = QVBoxLayout(w)
lay.addWidget(kit.CustomLineEdit("Hello"))
ok = kit.CustomButton("Save"); lay.addWidget(ok)
w.show(); app.exec()
```

Use the kit as-is by **copying the `qt_ui_kit/` folder into the app** (it's plain Python, PySide6
only), or `pip install -e .` from this folder. Run `python examples/gallery.py` to see every
widget; `--preset light|afterglow|system`; `--screenshot out.png` renders to a file (works with
`QT_QPA_PLATFORM=offscreen`). Self-test: `QT_QPA_PLATFORM=offscreen python tests/test_kit.py`.

Order matters: **`set_style` / `install` before building widgets.** Most widgets read the style
when constructed (some at paint time). To restyle a live window, rebuild it.

---

## 2. The style

One `Style` dataclass (`qt_ui_kit/style.py`), read by everything through `current_style()`:

| field | default (dark) | used for |
|---|---|---|
| `window` | `#000000` | the app window's background (palette `Window`) |
| `base` | `#0a0a0a` | pages, lists, dialogs, popups |
| `surface` | `#161616` | cards, group-box interiors, text fields, menu background |
| `accent` | `#2c2c2c` | button fill, active popover tab, hovered menu item |
| `secondary` | `#484848` | navigation (SegmentButton) fill, selections, progress chunk |
| `border` | `#5c5c5c` | borders of fields, check boxes, group boxes, frames |
| `outline` | `#f0f0f0` | frames of dialogs / popups / tooltips / menus, focused field |
| `text` | `#ffffff` | text and text icons (button text is auto-contrasted to its fill) |
| `text_outline`, `text_outline_width` | `#000000`, `0` | `OutlinedLabel` |
| `danger` | `#e5484d` | destructive buttons (`btn.set_fill_color(style.danger)`) |
| `rounded_corners`, `corner_radius` | `True`, `12` | every rounded shape (one shared radius) |
| `padding` | `10` | the one spacing value apps should use between panels / cards |
| `custom_paint` | `True` | `False` = native Qt buttons instead of custom-painted |
| `fixed_colors` | `True` | `False` = colours from the live desktop (KDE) palette |
| `extra` | `{}` | free-form app values (never interpreted by the kit) |

```python
kit.set_style(kit.Style(accent="#2161bb", corner_radius=20))   # override fields
kit.set_style(kit.presets.light())                             # dark (default), light, afterglow, system
kit.update_style(padding=14)                                   # change some fields of the current one
kit.on_style_changed(lambda s: ...)                            # react to set_style
```

Painting code asks `kit.Theme()` for colours: `window() base() surface() accent() secondary()
border() outline() text() danger()`, `button_color()`, `button_text_color()`, plus
`kit.contrast_text(bg)` (near-black or near-white for readability). Never hard-code a colour in a
widget; add a style field instead.

`kit.apply_palette()` (done by `install`) copies the style into the Qt palette so plain `QLabel`s
and native widgets match. It is a **QPalette**, never an app stylesheet — see §7.1.

Ported afterglow code: the old names still read (`afterglow_color_accent`, `card_text_color`,
`rounded_corner_radius`, `ui_padding`, `Theme.card_background()`, `library_background()`,
`turquoise()` …) as aliases. New code uses the plain names.

---

## 3. Icons — text by default

Every kit icon is a **name** (`check close search refresh sort filter settings edit copy delete
add remove star star_off play pause prev next up down expand collapse menu volume folder file info
warning fullscreen home`, plus `block`, `search_active`). By default each is drawn from a text glyph
in `style.text`; each name has fallbacks, and the first glyph the font can really draw is used
(last fallback is ASCII), so there are never tofu boxes.

```python
kit.icons.icon_pixmap("search", 64)          # QPixmap
kit.icons.set_icon("search", "art/search.png")  # this app's own image instead
kit.icons.set_glyph("search", "Find")          # or different text
kit.icons.text_pixmap("Ab", 48)                # any text as an icon
```

Textures (`handle connector marker volume_active volume_inactive backdrop`, used by the trim
timeline, volume bar and backdrop) are painted gradients from the theme; `set_icon(name, path)`
replaces them with images too. Set overrides **before** building widgets.

---

## 4. Widgets

All are drop-in subclasses of the Qt class named in brackets, so normal Qt API works.

**Buttons**
- `CustomButton(text)` [QToolButton] — rounded, accent fill, auto-contrast text, hover/press pulse.
  `set_icon_pixmap(pm)`, `set_circular(diameter)` (round icon buttons), `set_fill_color(c)`,
  `set_outline(color, width)`.
- `SegmentButton(icon_or_name, position, text="")` [QAbstractButton, checkable] — navigation /
  tab buttons on the `secondary` fill; darker when unchecked, lighter on hover,
  `set_loading(True)` darker still. `position`: `full left right middle top bottom` (see §5
  corner rule). Put several in an exclusive `QButtonGroup`, connect `idClicked` to a
  `QStackedWidget` (and `kit.crossfade_to_index(stack, i)` for the fade).
- `CollapseToggleButton(expanded)` — a round expand/collapse chevron that rotates.

**Inputs**
- `CustomLineEdit(text)`, `CustomSpinBox`, `CustomDoubleSpinBox` (round step buttons),
  `CustomComboBox` (its own themed popup list; `itemHovered`, `popupHidden`),
  `CustomCheckBox(text, leading_icon=None)`, `CustomRadioButton(text)`.
- `TriStateCheckBox(text, key)` — left click include ✓ / none, **right click block ✕** / none;
  `state` is `"none" | "include" | "exclude"`, `state_changed(key, state)`.

**Containers**
- `CustomGroupBox(title)` — use `g.make_layout(QVBoxLayout)` (not `setLayout`; see the class
  docstring for why).
- `ThemedFrame`, `SmoothScrollArea` (eased wheel scrolling + custom bar),
  `ThemedListWidget(row_height=30)` (rounded rows; roles for a leading glyph / secondary text),
  `GradientBackdrop(content, image=None)` (a theme gradient border around a page),
  `kit.paint_page_outline(widget, color)` (a thin page outline from `paintEvent`).

**Popups** (`Qt.Popup`: stay open while used, close on an outside click, grow out of their button)
- `TabbedPopover(tab_labels=("A","B"), width=320)` — `set_page_widget(i, w)`, `show_below(button)`.
- `CheckListPopup(items, checked=, excluded=, title=, tri_state=, icons=, add_label=)` — the
  multi-select dropdown (headings as group boxes, optional per-item icons, optional "+ Add",
  optional right-click blocking). Signals `toggled(key, on)`, `state_changed(key, state)`,
  `add_requested`, `closed`; `states()`.
- `SearchBubble(parent, placeholder)` — a speech-bubble search field under its button;
  `line_edit`, `search_confirmed` (Enter or its tick), `show_below(button)`.
- Menus: `kit.themed_menu(title, parent, stay_open=False)`; `StayOpenMenu` keeps a menu open while
  check boxes inside it (`QWidgetAction`) are ticked; `menu_stylesheet()` for any `QMenu` (with
  `install`, every QMenu is themed automatically).

**Dialogs** (frameless, rounded, `base` fill, `outline` frame, draggable by any empty spot)
- `ThemedDialog(title)` — base class: `self.lay`, `self.label(text, dim=)`, `self.button_row(ok, cancel)`.
- `show_message(parent, title, text)`, `ask_confirm(parent, title, text, confirm_label, danger=)`.
- `ask_text(...)` / `get_text(...)` / `TextInputDialog`.
- `get_color(initial, parent, title, alpha=)` — the full colour picker (SV square, hue / alpha
  strips, hex field, swatches, recent colours: `themed_dialogs.RECENT_COLORS_FILE`).
- `get_open_file_name / get_open_file_names / get_save_file_name / get_existing_directory` —
  same signatures as `QFileDialog`'s statics; a themed picker (sidebar places: add the app's own
  with `themed_dialogs.EXTRA_PLACES.append((label, path, glyph))`).
- `ProgressDialog(title)` (`set_fraction`, `cancel_btn`, Esc cancels), `ThemedProgressBar`.

**Media / misc**
- `TrimTimeline` — duration, a start/end range (right-click drag the handles), a playhead
  (left-click / drag seeks). `range_changed`, `seek_requested`, `drag_started`, `drag_finished`.
- `VolumeBar` — 0–100, `value_changed`.
- `OutlinedLabel` — text with a real outline (`set_colors(fill, outline, width)`), drawn in two
  passes so the outline never eats the fill.
- Effects: `tint_pixmap`, `hue_shift_pixmap`, `silhouette_outline_pixmap` (outline an icon's
  shape), `round_pixmap_corners`, `rounded_rect_path(rect, r, top_left=…)`.
- Motion: `PressPulse(widget)` (the hover/press scale used by every button; call
  `pulse.apply(painter)` first in `paintEvent`), `crossfade_to_index`, `animate_popup_from_point`,
  `reveal_from_point`, `PulseAnimator`.
- `compute_scale(w, h)` — a UI scale factor relative to a 1920×1080 window.

---

## 5. Design rules (the afterglow look)

- **Never round a corner that touches another element.** Touching buttons / tabs round only their
  outer corners (`position="left"/"right"/"middle"`); a page directly under a tab strip rounds only
  its bottom corners. Free-standing things round all four.
- **One radius, one padding**: `style.corner_radius`, `style.padding`. Don't invent per-widget values.
- Buttons custom-painted, filled `accent`, text auto-contrasted; navigation on `secondary`;
  destructive actions red (`danger`) with **Cancel focused**, so a stray Enter never destroys.
- Popups grow out of the button that opened them and close on an outside click; menus that hold
  check boxes stay open while ticking.
- Hover = slightly lighter / bigger, press = slightly darker / smaller (PressPulse), everywhere.
- Text icons by default; an app that has art sets it with `icons.set_icon`.
- Every card / list item in a grid must be the **same size**: never create optional rows only when
  that item has data — gate rows on the setting, show a blank line otherwise.
- Long text elides to one line rather than wrapping (wrapping made cards different heights).

---

## 6. Lessons from afterglow — behaviour

1. **QtWebEngine + an app-wide Python event filter = segfault (PySide6).** `install()`'s `chrome`
   and `wheel_guard` are app-wide filters. When a web view's internal Qt Quick item gets an event,
   PySide wraps it on the fly, wrapping sets a dynamic property, the property-change event goes
   back through the same filter and re-enters the half-built wrapper (gdb:
   `getWrapperForQObject → doSetProperty → sendThroughApplicationEventFilters`). Even a filter that
   only `return False`s crashes. **Run web views in a separate process** (afterglow's
   `afterglow-youtube` helper, JSON lines over a QLocalSocket), or call
   `kit.install(app, chrome=False, wheel_guard=False)` in a process that shows web views.
   Also: QtWebEngine needs `QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)` **before**
   `QApplication`, refuses page fullscreen unless `QWebEngineSettings.FullScreenSupportEnabled`
   is on, and then the app must actually put a window fullscreen (`fullScreenRequested`).
2. **Never touch widgets from a worker thread.** Do the work in a `threading.Thread`, emit a
   `Signal` on a `QObject` that lives in the GUI thread (queued automatically). Signal argument
   types are C types: an `int` signal **overflows silently past 2³¹** (a key built from
   `time_ns()` made results vanish) — use `Signal(object, …)` for big or arbitrary values.
3. **`deleteLater()` doesn't hide.** Removing a widget from a layout and `deleteLater()`ing it
   leaves it painted until the event loop runs: `hide()` first (stale "duplicate" cards on spam
   clicks).
4. **Rebuild grids off the event loop in slices** (a few ms per tick, `QTimer.singleShot(0, …)`),
   coalesce repeated refresh requests into one pending flag, and **skip the rebuild entirely when
   the data signature hasn't changed**. A synchronous rebuild froze the app and made 20 clicks
   into 20 back-to-back rebuilds.
5. **`QMenu.exec()` blocks** and the menu is parented to its widget: don't rebuild (delete) that
   widget while a menu is open — count open menus and defer refreshes until they close.
6. A click handled by a child can bubble up and **undo what it just did** (a selection set on
   press, then cleared by the parent's press handler): `event.accept()` and return.
7. Single vs double click: Qt has no "click that isn't a double click" — start a ~250 ms timer on
   press and cancel it on `mouseDoubleClickEvent`.
8. Popup animations need the event loop running; `time.sleep()` in a test shows them mid-way.
9. Writing to a local socket the peer already closed **aborts it with unread data lost**: a
   server should reply only after the client speaks (a `hello`), so fire-and-forget senders work.
10. A `QSpinBox` silently **clamps a value outside its range** — and saving the settings page then
    writes the clamped value. Make every range include every default the code can ever have.
11. Settings saved by an older build keep their old values when a code default changes: migrate
    known stale defaults on load, and offer "Revert to defaults" buttons.

## 7. Lessons from afterglow — painting and layout

1. **Unscoped stylesheets cascade.** `w.setStyleSheet("background-color: #000")` (no selector)
   applies to **every descendant**, wrecking custom-painted children — and the offscreen test
   platform does **not** reproduce it, so tests pass while the real desktop is wrong. Use
   `QPalette` for backgrounds (`setAutoFillBackground(True)` + palette `Window`), or always scope:
   `"QLabel { color: … }"`. `tests/test_kit.py` walks the tree for unscoped sheets — keep a test
   like it in every app.
2. **Size policies inside nested layouts.** A widget with the default `Preferred` policy, wrapped in
   its own sub-layout (e.g. a video between two arrow buttons), stops claiming space and collapses
   (the video shrank to 21 % of the window). Set `QSizePolicy.Expanding` explicitly. Conversely a
   widget with a free height grows to fill everything (the trim timeline): give it `Fixed`
   vertically and a `sizeHint`.
3. **A `QStackedWidget`'s minimum size is the largest of all its pages**, hidden ones included —
   one tall settings page forced a window taller than the screen. Wrap tall pages in scroll areas.
4. **Crop before rounding.** Scale an image with `KeepAspectRatioByExpanding`, then **centre-crop
   to the exact target size, then round the corners** — rounding the larger image puts the rounded
   corners in the part the label cuts off (square corners, plus any letterbox bars).
5. **Draw outlined text in two passes** (stroke underneath, fill on top). A single
   stroke+fill path eats into the fill; at small sizes the fill disappears entirely.
6. **Scale icons before outlining them**, never after — an outline computed at 512 px and scaled
   to 40 px goes sub-pixel and vanishes (measured: 0 visible outline pixels). And cache by a
   **stable** key (not a per-item size), or the cache never hits (the app took 30 s to open).
7. Rounded-corner radius: cap it by the shape's own height (`min(r, h/2)`), not by an unrelated
   width — a cap by border width made a 24 px design render at 9 px ("not rounded at all").
8. Odd vs even pixel heights shift centred markers by half a pixel — round sizes to even where
   things must line up (`volume_bar._round_to_even`).
9. When checking pixels in a test, **don't sample a rounded corner or a glyph**: sample a flat
   area a few pixels in from the edge, away from text.
10. A passing test that only checks that **values were set or signals fired** can hide a
    completely broken layout. Measure geometry (`widget.height() / window.height()`) and sample
    pixels of the real render; render screenshots (`examples/gallery.py --screenshot`) and look.

## 8. Testing an app built on the kit

- Logic and most painting: `QT_QPA_PLATFORM=offscreen`.
- Real windows, GL (video), QtWebEngine: Xvfb —
  `Xvfb :99 -screen 0 1920x1080x24 +extension GLX & DISPLAY=:99 openbox &`, then
  `DISPLAY=:99 QT_QPA_PLATFORM=xcb python3 test.py` (the xcb plugin needs `libxcb-cursor0`;
  QtWebEngine as root needs `QTWEBENGINE_DISABLE_SANDBOX=1`).
- Drive widgets with real events (`QTest.mouseClick`, `QContextMenuEvent` for right-click), not
  direct method calls; for a `QMenu`, pick an item with `setActiveAction` + Enter —
  `action.trigger()` does not make `exec()` return it.
- A widget that must be "visible" without a window on screen (a hidden web page that still needs
  layout and real clicks): `setAttribute(Qt.WA_DontShowOnScreen, True)` then `show()`.

## 9. Files

`qt_ui_kit/` — `style.py` (Style, current/set/update), `presets.py`, `theme.py`, `icons.py`,
`resources.py` (afterglow file-name shim), the widgets (`custom_*.py`, `segment_button.py`,
`check_list.py`, `popover.py`, `search_bubble.py`, `menus.py`, `themed_*.py`, `progress.py`,
`trim_timeline.py`, `volume_bar.py`, `outlined_label.py`, `collapse_toggle_button.py`,
`gradient_backdrop.py`), helpers (`rounded_rect.py`, `press_pulse.py`, `pulse_animation.py`,
`scale_reveal.py`, `pixmap_effects.py`, `page_outline.py`, `scaling.py`), app-wide extras
(`app_chrome.py`: tooltips / menus / scroll bars; `wheel_guard.py`). `examples/gallery.py`,
`tests/test_kit.py`, `pyproject.toml`.

Module docstrings keep afterglow's history (why something is built the way it is — often after a
reported bug); read them before "simplifying" anything. Nix: package it with
`python3Packages.buildPythonPackage { pname = "qt-ui-kit"; version = "1.0.0"; format = "pyproject";
src = ./.; nativeBuildInputs = [ setuptools ]; propagatedBuildInputs = [ pyside6 ]; }`, or vendor
the folder into the app's own package (simplest).
