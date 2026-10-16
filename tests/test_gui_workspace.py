"""Workspace controls apply semantic operations and persist local panel preferences."""

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QToolBar, QWidgetAction

from byax import model
from byax.formats import FORMATS, ribbon_items
from byax.gui import MainWindow


def make_window(qtbot, monkeypatch, tmp_path):
    settings = QSettings(str(tmp_path / 'workspace.ini'), QSettings.IniFormat)
    monkeypatch.setattr('byax.gui.QSettings', lambda *_: settings)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    return window, settings


def test_ribbon_rows_sit_beside_the_logo_and_apply_formatting(qtbot, monkeypatch, tmp_path):
    window, _ = make_window(qtbot, monkeypatch, tmp_path)
    window.doc = model.new_document(text='A paragraph')
    window.refresh(0)
    first = window.findChild(QToolBar, 'writingRibbon')
    second = window.findChild(QToolBar, 'formattingRibbon')
    assert (
        second.mapTo(window, second.rect().topLeft()).y()
        >= first.mapTo(window, first.rect().bottomLeft()).y()
    )
    logo = window.brand_mark
    top = logo.mapTo(window, logo.rect().topLeft())
    assert first.mapTo(window, first.rect().topLeft()).x() > top.x() + logo.width() - 1
    rows = (
        second.mapTo(window, second.rect().bottomLeft()).y() - first.mapTo(window, first.rect().topLeft()).y()
    )
    assert abs(rows + 1 - logo.height()) <= 2
    align = next(a for a in second.actions() if a.menu() and a.text() == 'Alignment')
    next(a for a in align.menu().actions() if a.text() == 'Align center').trigger()
    assert window.doc['blocks'][0]['attrs']['alignment'] == 'center'
    window.sync_ribbon_format()
    assert align.property('byaxIcon') == 'align-center'
    lists = next(a for a in second.actions() if a.menu() and a.text() == 'Lists')
    next(a for a in lists.menu().actions() if a.text() == 'Bulleted list').trigger()
    assert window.doc['blocks'][0]['attrs']['list']['kind'] == 'bullet'
    window.ribbon_size.setValue(18)
    window.apply_ribbon_size()
    assert window.doc['blocks'][0]['attrs']['font_size'] == 18
    assert window.ribbon_size.value() == 18
    window.dirty = False


def ribbon_texts(window):
    texts = set()
    for name in ('writingRibbon', 'formattingRibbon'):
        for action in window.findChild(QToolBar, name).actions():
            if isinstance(action, QWidgetAction):
                texts.add(action.defaultWidget().accessibleName())
            elif not action.isSeparator():
                texts.add(action.text())
                if action.menu():
                    texts.update(a.text() for a in action.menu().actions())
    return texts


def test_each_file_view_has_its_own_ribbon_and_byax_has_everything(qtbot, monkeypatch, tmp_path):
    window, _ = make_window(qtbot, monkeypatch, tmp_path)
    window.doc = model.new_document(text='A paragraph')
    window.refresh(0)
    seen = {}
    for view in FORMATS:
        assert window.set_file_view(view)
        seen[view] = ribbon_texts(window)
        assert set(ribbon_items(view)) <= set(ribbon_items('byax'))
    full = seen['byax']
    for view, texts in seen.items():
        # Rendered/Source and the Markdown source command are the same feature in different views.
        assert texts - {'Rendered or source'} <= full | {'Rendered or source'}, (view, texts - full)
    assert {'Track changes', 'Checkpoint…', 'Protect selected paragraph'} <= full
    assert 'Underline' not in seen['md'] and 'Bold' in seen['md']
    assert 'Track changes' not in seen['docx'] and 'Comment' in seen['docx']
    assert 'Align center' not in seen['odt']
    assert {'Bold', 'Insert'}.isdisjoint(seen['txt']) and 'Export' in seen['txt']
    window.dirty = False


def test_export_button_asks_in_byax_and_defaults_to_the_open_format(qtbot, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QToolButton

    window, _ = make_window(qtbot, monkeypatch, tmp_path)
    window.doc = model.new_document(text='A paragraph')
    window.refresh(0)
    calls = []
    monkeypatch.setattr(window, 'export', lambda fmt=None: calls.append(fmt))
    export = window.ribbon_actions['export']
    row = window.findChild(QToolBar, 'formattingRibbon')
    assert row.widgetForAction(export).popupMode() == QToolButton.InstantPopup
    assert export.menu().defaultAction() is None
    assert [a.data() for a in export.menu().actions()] == ['pdf', 'docx', 'odt', 'md', 'html', 'rtf', 'txt']
    window.set_file_view('md')
    assert row.widgetForAction(export).popupMode() == QToolButton.MenuButtonPopup
    assert export.menu().defaultAction().data() == 'md'
    export.trigger()
    next(a for a in export.menu().actions() if a.data() == 'pdf').trigger()
    assert calls == ['md', 'pdf']
    window.dirty = False


def test_long_menus_nest_and_scroll_instead_of_splitting(qtbot, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication, QStyle

    window, _ = make_window(qtbot, monkeypatch, tmp_path)
    for top in window.menuBar().actions():
        visible = [a for a in top.menu().actions() if not a.isSeparator()]
        assert len(visible) <= 20, (top.text(), len(visible))
    assert QApplication.style().styleHint(
        QStyle.SH_Menu_Scrollable, None, window.menuBar().actions()[2].menu()
    )
    window.dirty = False


def test_layout_sidebar_holds_layers_on_either_side(qtbot, monkeypatch, tmp_path):
    window, settings = make_window(qtbot, monkeypatch, tmp_path)
    for side in ('right', 'left'):
        selector = window.sidebar_selectors[side]
        selector.setCurrentIndex(selector.findData('layout'))
        assert window.sidebar_stacks[side].currentWidget().isAncestorOf(window.layer_list)
        assert settings.value(f'workspace/{side}Sidebar') == 'layout'
    assert window.sidebar_panels['right']['layout-note'].isVisibleTo(window.sidebar_panels['right']['layout'])
    assert not window.sidebar_panels['left']['layout-note'].isVisibleTo(
        window.sidebar_panels['left']['layout']
    )
    window.dirty = False


def test_sidebar_selection_and_personal_notes_persist(qtbot, monkeypatch, tmp_path):
    window, settings = make_window(qtbot, monkeypatch, tmp_path)
    for side in ('left', 'right'):
        selector = window.sidebar_selectors[side]
        # The selector is the dock's title: no separate dropdown sits above the panel.
        assert window.sidebar_docks[side].titleBarWidget().isAncestorOf(selector)
        selector.setCurrentIndex(selector.findData('scratchpad'))
        assert window.sidebar_stacks[side].currentWidget() is window.sidebar_panels[side]['scratchpad']
        assert settings.value(f'workspace/{side}Sidebar') == 'scratchpad'
    window.sidebar_panels['left']['scratchpad'].setPlainText('Remember the introduction')
    assert window.sidebar_panels['right']['scratchpad'].toPlainText() == 'Remember the introduction'
    assert settings.value('workspace/scratchpad') == 'Remember the introduction'
    assert not window.dirty
    reopened = MainWindow()
    qtbot.addWidget(reopened)
    assert reopened.sidebar_selectors['left'].currentData() == 'scratchpad'
    assert reopened.sidebar_panels['left']['scratchpad'].toPlainText() == 'Remember the introduction'
    selector = window.sidebar_selectors['right']
    selector.setCurrentIndex(selector.findData('default'))
    assert window.sidebar_stacks['right'].currentWidget() is window.inspector


def test_bookmark_sidebar_navigates_and_statistics_refresh(qtbot, monkeypatch, tmp_path):
    window, _ = make_window(qtbot, monkeypatch, tmp_path)
    window.doc = model.new_document(text='First paragraph\nSecond paragraph')
    target = window.doc['blocks'][1]['id']
    window.doc['settings']['bookmarks'] = {'Second': {'target': target}}
    window.refresh(0)
    panel = window.sidebar_panels['left']
    assert '4 words' in panel['statistics'].text()
    assert panel['bookmarks'].topLevelItem(0).text(0) == 'Second'
    panel['bookmarks'].itemActivated.emit(panel['bookmarks'].topLevelItem(0), 0)
    assert window.editor.current_id() == target


def test_inspector_underline_stays_inside_selected_tab(qtbot, monkeypatch, tmp_path):
    window, _ = make_window(qtbot, monkeypatch, tmp_path)
    tabs = window.inspector.tabBar()
    for selected in range(tabs.count()):
        tabs.setCurrentIndex(selected)
        image = tabs.grab().toImage()
        for index in range(tabs.count()):
            rect = tabs.tabRect(index)
            ink = 0
            for y in range(rect.bottom() - 6, rect.bottom() + 1):
                for x in range(rect.left() + 4, rect.right() - 3):
                    color = image.pixelColor(x, y)
                    ink += color.green() > color.red() * 1.3 and color.blue() > color.red() * 1.2
            assert (ink > 0) == (index == selected)
