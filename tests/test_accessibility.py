import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QCoreApplication, Qt, QTranslator
from PySide6.QtGui import QAction, QColor, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QDialog,
    QFileDialog,
    QInputDialog,
    QMenu,
    QMessageBox,
    QToolBar,
    QWidget,
    QWidgetAction,
)

from byax import gui_a11y, model
from byax.gui import MainWindow


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, monkeypatch, tmp_path):
    w = MainWindow()
    w.recovery_dir = tmp_path / 'recovery'
    w.recovery_dir.mkdir()
    errors = []
    monkeypatch.setattr(w, 'error', lambda error: errors.append(str(error)))
    w.test_errors = errors
    yield w
    w.dirty = False
    w.editor.blockSignals(True)
    w.close()


def unlabeled(root):
    problems = []
    for widget in [root, *root.findChildren(QWidget)]:
        if not gui_a11y.actionable(widget):
            continue
        if not widget.accessibleName().strip():
            problems.append(f'{type(widget).__name__} {widget.objectName()!r}: no accessible name')
        if not widget.toolTip().strip():
            problems.append(f'{type(widget).__name__} {widget.accessibleName()!r}: no tooltip')
    return problems


def all_actions(window):
    actions = []

    def visit(menu):
        for action in menu.actions():
            if action.menu():
                visit(action.menu())
            elif not action.isSeparator():
                actions.append(action)

    for top in window.menuBar().actions():
        visit(top.menu())
    for toolbar in window.findChildren(QToolBar):
        actions.extend(
            a
            for a in toolbar.actions()
            if not a.isSeparator() and not a.menu() and not isinstance(a, QWidgetAction) and a not in actions
        )
    return actions


def test_main_window_controls_and_actions_are_named(window):
    window.show()
    QApplication.processEvents()
    assert unlabeled(window) == []
    ai_controls = [w for w in window.ai_panel.findChildren(QWidget) if gui_a11y.actionable(w)]
    assert len(ai_controls) >= 5 and unlabeled(window.ai_panel) == []
    actions = all_actions(window)
    assert len(actions) > 80
    for action in actions:
        assert action.toolTip().strip(), action.text()
        assert action.statusTip().strip(), action.text()
        keys = action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
        if keys:
            assert keys in action.toolTip(), (action.text(), action.toolTip())
    for toolbar in window.findChildren(QToolBar):
        for action in toolbar.actions():
            button = toolbar.widgetForAction(action)
            if button is not None and gui_a11y.actionable(button):
                assert button.accessibleName().strip()
                assert button.toolTip().strip()
    bold = next(a for a in actions if a.text() == 'Bold')
    assert (
        bold.toolTip()
        == 'Bold (' + QKeySequence('Ctrl+B').toString(QKeySequence.SequenceFormat.NativeText) + ')'
    )
    check = next(a for a in actions if a.text() == 'Check')
    assert check.statusTip() == 'Run document checks'


def test_shortcuts_are_unambiguous_and_toolbar_actions_have_keyboard_routes(window):
    seen = {}
    for action in all_actions(window):
        for sequence in action.shortcuts():
            key = sequence.toString(QKeySequence.SequenceFormat.PortableText)
            if key and seen.get(key, action) is not action:
                raise AssertionError(f'{key} is bound to {seen[key].text()!r} and {action.text()!r}')
            seen[key] = action
    for name in (
        'Ctrl+B',
        'Ctrl+I',
        'Ctrl+U',
        'Ctrl+F',
        'Ctrl+S',
        'Ctrl+O',
        'Ctrl+Shift+E',
        'F7',
        'Ctrl+Alt+M',
    ):
        assert name in seen, name
    # Dropdowns open menus whose commands are in the menu bar; every other ribbon button has a shortcut.
    toolbar = window.findChild(QToolBar, 'writingRibbon')
    for action in toolbar.actions():
        if not action.isSeparator() and not isinstance(action, QWidgetAction) and not action.menu():
            assert not action.shortcut().isEmpty(), action.text()
    assert window.style_box.focusPolicy() & Qt.TabFocus


def test_focus_chain_reaches_core_workspace_controls(window):
    window.doc = model.new_document(text='Heading\nBody')
    window.doc['blocks'][0]['type'] = 'heading'
    window.refresh(0)
    window.commit([{'type': 'add_comment', 'target': window.doc['blocks'][1]['id'], 'text': 'Note'}])
    window.show()
    QApplication.processEvents()
    chain, widget = [], window.editor
    for _ in range(500):
        widget = widget.nextInFocusChain()
        if widget is window.editor:
            break
        if widget.focusPolicy() & Qt.TabFocus and widget.isVisibleTo(window) and widget.isEnabled():
            chain.append(widget)
    assert widget is window.editor, 'focus chain must cycle back to the editor'
    for required in (window.style_box, window.outline, window.review_list):
        assert required in chain, required.accessibleName()
    assert all(w.accessibleName() for w in chain if gui_a11y.actionable(w))


DIALOG_METHODS = [
    'edit_export_policies',
    'edit_page_masters',
    'edit_section_profiles',
    'linked_assets',
    'formatting_inspector',
    'layer_inspector',
    'export_profiles',
    'show_view_losses',
    'markdown_preferences',
    'paragraph_format',
    'define_style',
    'find_replace',
    'page_setup',
    'define_field',
    'history',
    'jobs_dialog',
    'document_properties',
    'source_view',
    'edit_profiles',
    'provider_settings',
    'table_properties',
    'dictation',
]


@pytest.fixture
def dialogs(monkeypatch, app):
    from byax.transcription import QtMicrophoneRecorder

    monkeypatch.setattr(QtMicrophoneRecorder, 'devices', staticmethod(list))
    visited = []

    def fake_exec(dialog):
        dialog.show()
        QApplication.processEvents()
        visited.append((dialog.windowTitle(), unlabeled(dialog)))
        dialog.hide()
        return 0

    monkeypatch.setattr(QDialog, 'exec', fake_exec)
    for name in ('question', 'information', 'warning', 'critical'):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.No))
    monkeypatch.setattr(QInputDialog, 'getText', staticmethod(lambda *a, **k: ('', False)))
    monkeypatch.setattr(QInputDialog, 'getMultiLineText', staticmethod(lambda *a, **k: ('', False)))
    monkeypatch.setattr(QInputDialog, 'getInt', staticmethod(lambda *a, **k: (0, False)))
    monkeypatch.setattr(QInputDialog, 'getDouble', staticmethod(lambda *a, **k: (0.0, False)))
    monkeypatch.setattr(QInputDialog, 'getItem', staticmethod(lambda *a, **k: ('', False)))
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', staticmethod(lambda *a, **k: ('', '')))
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', staticmethod(lambda *a, **k: ('', '')))
    monkeypatch.setattr(QColorDialog, 'getColor', staticmethod(lambda *a, **k: QColor()))
    return visited


def test_every_dialog_names_its_controls(window, dialogs):
    from byax.gui_providers import authorize_payload

    window.doc = model.new_document(text='First\nSecond')
    window.doc['blocks'][0].update(type='heading', style='heading1', attrs={'level': 1})
    window.refresh(0)
    window.commit([{'type': 'insert_table', 'after': window.doc['blocks'][0]['id'], 'rows': 2, 'columns': 2}])
    table = next(b for b in window.doc['blocks'] if b['type'] == 'table')
    for name in DIALOG_METHODS:
        if name == 'table_properties':
            assert window.editor.navigate(table['rows'][0]['cells'][0]['id'])
        try:
            getattr(window, name)()
        except Exception as exc:  # noqa: BLE001 - reported by the assertion below
            dialogs.append((name, [f'raised {exc!r}']))
        dialog = getattr(window, '_dictation_dialog', None)
        if name == 'dictation' and dialog is not None:
            dialog.show()
            QApplication.processEvents()
            dialogs.append((dialog.windowTitle(), unlabeled(dialog)))
            dialog.reject()
    window.report('Summary', {'summary': 'Done', 'findings': [{'message': 'x'}]})
    authorize_payload(window, 'Authorize', {'text': 'x'})
    about = window.create_about_dialog()
    about.show()
    QApplication.processEvents()
    dialogs.append((about.windowTitle(), unlabeled(about)))
    about.close()
    assert len(dialogs) >= len(DIALOG_METHODS) + 3
    titles = [title for title, _ in dialogs]
    assert 'Dictation' in titles and 'Checking and voice profiles' in titles
    assert [entry for entry in dialogs if entry[1]] == []
    assert not window.test_errors


def test_save_action_triggers_with_keyboard_route(window, monkeypatch):
    calls = []
    monkeypatch.setattr(
        QFileDialog, 'getSaveFileName', staticmethod(lambda *a, **k: calls.append(a) or ('', ''))
    )
    save = next(a for a in all_actions(window) if a.text() == 'Save')
    save.trigger()
    assert calls, 'Save on an untitled document opens Save As'


class PseudoLocale(QTranslator):
    def translate(self, context, source, disambiguation=None, n=-1):
        if not source:
            return source
        return '⟦' + source.upper() + '⟧'

    def isEmpty(self):
        return False


def test_pseudo_locale_changes_menus_toolbar_and_dialogs(app, monkeypatch, tmp_path, dialogs):
    translator = PseudoLocale()
    QCoreApplication.installTranslator(translator)
    try:
        assert QCoreApplication.translate('MainWindow', 'Save') == '⟦SAVE⟧'
        window = MainWindow()
        window.recovery_dir = tmp_path
        titles = [action.text() for action in window.menuBar().actions()]
        assert titles and all(title.startswith('⟦') for title in titles), titles
        assert '⟦&FILE⟧' in titles
        file_menu = window.menuBar().actions()[0].menu()
        assert isinstance(file_menu, QMenu)
        texts = [a.text() for a in file_menu.actions() if not a.isSeparator()]
        assert '⟦SAVE⟧' in texts and '⟦OPEN…⟧' in texts
        toolbar_texts = [
            a.text() for a in window.findChild(QToolBar, 'writingRibbon').actions() if not a.isSeparator()
        ]
        assert '⟦BOLD⟧' in toolbar_texts
        assert window.status_label.text() == '⟦READY⟧'
        tabs = [window.inspector.tabText(i) for i in range(window.inspector.count())]
        assert '⟦AI⟧' in tabs and '⟦PROPOSED CHANGES⟧' in tabs
        window.find_replace()
        assert dialogs[-1][0] == '⟦FIND AND REPLACE⟧'
        assert all(isinstance(a, QAction) for a in all_actions(window))
        window.dirty = False
        window.close()
    finally:
        QCoreApplication.removeTranslator(translator)


def test_brand_text_and_control_colors_meet_wcag_aa(app):
    from PySide6.QtGui import QPalette

    from byax.brand import COLORS, configure_application, contrast_ratio, stylesheet

    configure_application(app)

    text_pairs = [
        ('ink', 'paper'),
        ('ink', 'surface'),
        ('muted', 'paper'),
        ('muted', 'surface'),
        ('greenText', 'paper'),
        ('greenText', 'surface'),
    ]
    for foreground, background in text_pairs:
        assert contrast_ratio(COLORS[foreground], COLORS[background]) >= 4.5, (foreground, background)
    for background in ('green', 'greenText'):
        assert contrast_ratio('#FFFFFF', COLORS[background]) >= 4.5, background
    for outline in ('control', 'greenText', 'green'):
        for background in ('paper', 'surface', '#FFFFFF'):
            assert contrast_ratio(COLORS[outline], COLORS.get(background, background)) >= 3, (
                outline,
                background,
            )
    palette = QApplication.instance().palette()
    for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
        assert (
            contrast_ratio(palette.color(group, QPalette.WindowText), palette.color(group, QPalette.Window))
            >= 4.5
        )
        assert contrast_ratio(palette.color(group, QPalette.Text), palette.color(group, QPalette.Base)) >= 4.5
        assert (
            contrast_ratio(
                palette.color(group, QPalette.HighlightedText), palette.color(group, QPalette.Highlight)
            )
            >= 4.5
        )
    sheet = stylesheet()
    for selector in ('QLineEdit, QComboBox', 'QToolButton, QPushButton {'):
        rule = sheet[sheet.index(selector) :]
        assert f'border: 1px solid {COLORS["control"]}' in rule[: rule.index('}')], selector


def test_high_contrast_preference_keeps_platform_theme(app, monkeypatch):
    from byax import brand

    previous_sheet, previous_palette = app.styleSheet(), app.palette()
    monkeypatch.setattr(brand, 'prefers_high_contrast', lambda _app: True)
    try:
        brand.configure_application(app)
        assert app.styleSheet() == ''
        # the brand face gives way to the plain one for readers who asked for high contrast
        assert app.font().families() == ['Manrope']
    finally:
        app.setPalette(previous_palette)
        app.setStyleSheet(previous_sheet)
        app.setFont(brand.interface_font())
