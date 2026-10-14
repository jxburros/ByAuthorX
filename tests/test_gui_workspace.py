"""Workspace controls apply semantic operations and persist local panel preferences."""

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QToolBar

from byax import model
from byax.gui import MainWindow


def make_window(qtbot, monkeypatch, tmp_path):
    settings = QSettings(str(tmp_path / 'workspace.ini'), QSettings.IniFormat)
    monkeypatch.setattr('byax.gui.QSettings', lambda *_: settings)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    return window, settings


def test_two_row_ribbon_applies_formatting(qtbot, monkeypatch, tmp_path):
    window, _ = make_window(qtbot, monkeypatch, tmp_path)
    window.doc = model.new_document(text='A paragraph')
    window.refresh(0)
    first = window.findChild(QToolBar, 'writingRibbon')
    second = window.findChild(QToolBar, 'formattingRibbon')
    assert second.geometry().top() >= first.geometry().bottom()
    next(a for a in second.actions() if a.text() == 'Align center').trigger()
    assert window.doc['blocks'][0]['attrs']['alignment'] == 'center'
    next(a for a in second.actions() if a.text() == 'Bulleted list').trigger()
    assert window.doc['blocks'][0]['attrs']['list']['kind'] == 'bullet'
    window.ribbon_size.setValue(18)
    window.apply_ribbon_size()
    assert window.doc['blocks'][0]['attrs']['font_size'] == 18
    assert window.ribbon_size.value() == 18
    window.dirty = False


def test_sidebar_selection_and_personal_notes_persist(qtbot, monkeypatch, tmp_path):
    window, settings = make_window(qtbot, monkeypatch, tmp_path)
    for side in ('left', 'right'):
        selector = window.sidebar_selectors[side]
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
    for selected in (0, 1, 2):
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
