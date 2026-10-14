# Accessibility audit — 14 October 2026

Scope: the native desktop window (`src/byax/gui*.py`) and the dialogs it opens. This audit covers what can be verified in a headless Linux container with Qt's `offscreen` platform (Qt 6.12.0, PySide6 6.12.0, Python 3.13, Linux 6.18). It is **not** an assistive-technology certification.

**Pending physical verification.** No screen reader was run. NVDA on Windows 11 and Orca on Ubuntu 24.04 still need a person at a real desktop to record what is and is not announced, together with the screen-reader and OS versions. High-contrast themes, 200% scaling and the installed applications have not been checked on either platform either. Every statement below about names, focus and shortcuts describes what Qt exposes to the accessibility layer, not what a screen reader actually announces.

## What is verified automatically

All of these run in `tests/test_accessibility.py` on every test run.

| Check | How | Result |
| --- | --- | --- |
| Every actionable control in the main window has an accessible name and a tooltip | Walks `MainWindow.findChildren(QWidget)`: buttons, combo boxes, line and text edits, spin boxes, item views and sliders. Qt-internal sub-widgets such as scroll bars, headers and the line edit inside a spin box are skipped. | Pass |
| Every menu and toolbar action has a tooltip and a status description, and the tooltip names its shortcut | Walks every `QAction` in the menu bar and the toolbar (90+ actions) | Pass, for example "Bold (Ctrl+B)" |
| Every dialog names its controls | Opens 13 dialogs through their real menu entry points, plus the report viewer, the provider authorization dialog and About: Font and paragraph, Define named style, Find and replace, Page setup, Define template field, Revision history, Background jobs, Document properties, Markdown source, Checking and voice profiles, Providers and credentials, Table properties and Microphone dictation | Pass |
| No shortcut is bound to two actions, and the core shortcuts exist | Collects every shortcut across menus and the toolbar | Pass |
| Toolbar actions can be reached without the toolbar | Every toolbar button has a shortcut. Bold, Italic and Underline are also in the Format menu, and Check and Comment are also in the Review menu. | Pass |
| The focus chain cycles through the workspace | Follows `nextInFocusChain()` from the editor and keeps controls that accept Tab focus | Pass. Order: Document editor → Paragraph style → Document outline → Inspector tabs → Comments, proposals and findings → back to the editor. The review buttons join the chain once an item is selected. |
| All user-visible strings go through `tr()` | `pyside6-lupdate` extracts 485 source texts from the GUI modules. A pseudo-locale `QTranslator` changes menu titles, File menu entries, toolbar actions, the status bar and dialog titles. | Pass |
| Brand colours meet WCAG AA | Contrast ratios computed from `resources/brand/tokens.json` | See below |

Accessible names come from three places. Explicit names are set in the GUI modules for ambiguous controls such as the paragraph style box, the outline, the review and layer lists, and the profile and provider pickers. Every other control takes the text a sighted user reads beside it: the button text, the form-row label or buddy label, or the placeholder (`gui_a11y.ensure_accessible`). A window or dialog parented to the main window is labelled when it is first shown.

## Keyboard-only workflows (offscreen, programmatic)

Only the routes were checked: that shortcuts exist, are unique and trigger, that menus have mnemonics, and that the focus order is as listed. Nobody typed through these flows on a physical keyboard.

| Workflow | Keyboard route | Verified here |
| --- | --- | --- |
| Open | Ctrl+O, or Alt+F then Open | The shortcut is bound. The native file dialog belongs to the platform and was not exercised. |
| Type and edit | The editor has focus on start. Undo is Ctrl+Z and redo Ctrl+Y. Cut, copy, paste and select all use the platform keys. | Typing through `QTest` key events is covered by `scripts/benchmark.py` and the GUI tests. |
| Format | Ctrl+B, Ctrl+I and Ctrl+U. Alt+Shift+Right and Alt+Shift+Left change the list level. Tab reaches the paragraph style box. Alt+O opens the Format menu for everything else. | Shortcuts bound and unique. The style box accepts Tab focus. |
| Find and replace | Ctrl+F opens the dialog, and Tab moves through Scope, Find, Replace with, the options and the buttons. | Every dialog control is named. Scope choices now carry data, so translated labels no longer break matching. |
| Save | Ctrl+S, or Ctrl+Shift+S for Save As | The Save action triggered on an untitled document opens Save As (test). |
| Export | Ctrl+Shift+E | Bound. The export now runs on a worker. |
| Print | Ctrl+P. Print preview is in the File menu. | Bound |
| Review | F7 runs the checks, Ctrl+Alt+M adds a comment, and the review list and its buttons are in the focus chain. | Pass |
| Views | Ctrl+Alt+L or Ctrl+Alt+P switches between writing and pages. The View menu shows and hides the Outline and Inspector docks. | Bound |
| Dictation | Tools → Dictation. Alt+P pauses, Alt+R resumes and Alt+T stops, as the dialog announces. | The dialog's controls are named. No microphone or audio was used. |

## Contrast (WCAG 2.2 AA)

Computed with the WCAG relative-luminance formula (`brand.contrast_ratio`):

| Pair | Ratio | Requirement |
| --- | ---: | --- |
| ink `#252927` on paper `#F4EEE3` / surface `#FFFCF7` | 12.76 / 14.40 | 4.5 text |
| muted `#52615B` on paper / surface (secondary and disabled text, inactive tabs) | 5.65 / 6.37 | 4.5 text |
| greenText `#246052` on paper / surface (links, selected tabs, focus ring) | 6.34 / 7.15 | 4.5 text, 3 focus |
| white on greenText (selection, pressed) / green `#367B6C` (primary button) | 7.32 / 4.99 | 4.5 text |
| **Fixed:** input and button outlines were rule `#D7D0C3` on surface | 1.50 | 3 (1.4.11 non-text) |
| Input and button outlines now use control `#857C6D` on surface / paper / white | 4.02 / 3.56 / 4.12 | 3 (1.4.11) |

The decorative rule colour is still used for separators and panel frames. WCAG does not require it to identify a control.

The palette sets the same colours for the active, inactive and disabled groups, and disabled text uses muted, so an inactive window keeps the ratios above. If Qt reports a high-contrast preference (`QStyleHints.accessibility().contrastPreference()`), the brand palette and stylesheet are not applied and the platform's high-contrast theme takes over. That path is unit-tested by forcing the preference. No real Windows or GNOME high-contrast theme was used.

## Known limitations

- Screen reader announcements (NVDA, Orca), focus-indicator visibility at high DPI, 200% scaling and the high-contrast themes on the real platforms still need physical verification.
- Toolbar buttons are not in the Tab order, which is Qt's default. Every toolbar action has a shortcut and, except Pages, a menu entry. Pages is the same as View → Writing / pages.
- Some labels come from the document's data and stay untranslated by design: comment and proposal text, review record kinds such as "Tracked changes", provider aliases, style names and file paths.
- The native file, colour and print dialogs come from the platform and are outside this audit.
- No translation catalogue (`.qm`) ships yet. Only the hooks and the pseudo-locale smoke test exist.
