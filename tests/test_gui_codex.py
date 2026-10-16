"""Codex panel and project find dialog (offscreen)."""

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QDialog, QInputDialog, QMessageBox

from byax import codex, engine, project_index
from byax.gui import MainWindow
from byax.gui_codex import CodexDialog, CodexEntryDialog, ProjectFindDialog
from byax.model import new_document
from byax.storage import save_document


def novel():
    doc = new_document('Novel', 'Chapter One\nMara lit the lamp.\nChapter Two\nMara and Tobin rowed out.')
    for index in (0, 2):
        doc['blocks'][index].update(type='heading', style='heading1', attrs={'level': 1})
    doc, _ = engine.apply(doc, {'operations': [{'type': 'project_init'}]})
    doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {
                    'type': 'codex_add',
                    'id': 'cx_mara',
                    'values': {
                        'kind': 'character',
                        'name': 'Mara',
                        'traits': [{'name': 'secret', 'value': 'x', 'hide_from_ai': True}],
                    },
                },
                {
                    'type': 'codex_add',
                    'id': 'cx_tobin',
                    'values': {'kind': 'character', 'name': 'Tobin', 'hide_from_ai': True},
                },
            ]
        },
    )
    return doc


def make_window(qtbot, monkeypatch, tmp_path, *, saved=True):
    settings = QSettings(str(tmp_path / 'codex.ini'), QSettings.IniFormat)
    monkeypatch.setattr('byax.gui.QSettings', lambda *_: settings)
    monkeypatch.setattr('byax.providers._default_dir', lambda: tmp_path / 'providers')
    monkeypatch.setattr(project_index, 'cache_root', lambda: tmp_path / 'cache')
    window = MainWindow()
    qtbot.addWidget(window)
    doc = novel()
    if saved:
        folder = tmp_path / 'novel'
        folder.mkdir()
        save_document(doc, folder / 'novel.byax')
        save_document(new_document('Notes', 'Mara keeps the lamp at Harrow.'), folder / 'notes.byax')
        window.open_path(str(folder / 'novel.byax'))
    else:
        window.doc = doc
        window.refresh()

    def synchronous(label, work, done, failed=None):
        done(work())

    monkeypatch.setattr(window, 'background', synchronous)
    monkeypatch.setattr(window, 'confirm_discard', lambda *args: True)
    return window


def test_menus_offer_codex_and_project_find(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    menus = {a.text(): a.menu() for a in window.menuBar().actions()}
    tools = next(a for a in menus['&Tools'].actions() if a.text() == 'Codex…')
    find = next(a for a in menus['&Edit'].actions() if a.text() == 'Find in project…')
    assert tools.statusTip() and find.shortcut().toString() == 'Ctrl+Shift+F'
    tools.trigger()
    find.trigger()
    titles = {w.windowTitle() for w in window.findChildren(QDialog) if w.isVisible()}
    assert {'Codex', 'Find in project'} <= titles


def test_codex_panel_lists_entries_mentions_and_an_accessible_heatmap(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    dialog = CodexDialog(window)
    qtbot.addWidget(dialog)
    names = [dialog.entries.item(r, 0).text() for r in range(dialog.entries.rowCount())]
    assert names == ['Mara', 'Tobin']
    assert dialog.entries.item(0, 3).text() == '2' and dialog.entries.item(0, 4).text() == '1 hidden traits'
    assert dialog.entries.item(1, 4).text() == 'Hidden' and dialog.entries.accessibleName() == 'Codex entries'
    dialog.entries.selectRow(0)
    assert dialog.mention_list.count() == 2
    assert dialog.heatmap.rowCount() == 2 and dialog.heatmap.item(1, 0).text() in ('1',)
    assert 'mentions of' in dialog.heatmap.item(0, 0).toolTip()
    navigated = []
    monkeypatch.setattr(window, 'navigate_target', lambda target: navigated.append(target) or True)
    dialog.go_to(dialog.mention_list.item(0))
    assert navigated == [window.doc['blocks'][1]['id']]
    dialog.filter.setText('tob')
    assert dialog.entries.rowCount() == 1


def test_entry_dialog_builds_undoable_canonical_operations(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    entry = codex.find_entry(window.doc, 'cx_mara')
    dialog = CodexEntryDialog(window, entry)
    qtbot.addWidget(dialog)
    dialog.aliases.setPlainText('M.\nMara Vey')
    dialog.hidden.setChecked(True)
    dialog.traits.item(0, 2).setCheckState(Qt.Unchecked)
    row = dialog.add_trait()
    dialog.traits.item(row, 0).setText('eyes')
    dialog.traits.item(row, 1).setText('grey')
    operations = dialog.operations()
    assert [o['type'] for o in operations] == ['codex_update', 'codex_update_trait', 'codex_add_trait']
    assert operations[0]['values'] == {'aliases': ['M.', 'Mara Vey'], 'hide_from_ai': True}
    assert window.commit(operations)
    updated = codex.find_entry(window.doc, 'cx_mara')
    assert updated['hide_from_ai'] and updated['traits'][1]['value'] == 'grey'
    window.undo()
    assert codex.find_entry(window.doc, 'cx_mara') == entry
    new = CodexEntryDialog(window)
    qtbot.addWidget(new)
    new.name.setText(' Harrow  Point ')
    new.kind.setCurrentIndex(new.kind.findData('place'))
    [operation] = new.operations()
    assert operation['type'] == 'codex_add' and operation['values']['name'] == 'Harrow Point'


def test_codex_panel_add_and_remove_through_the_window(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    dialog = CodexDialog(window)
    qtbot.addWidget(dialog)

    def accept(self):
        self.name.setText('Harrow')
        return QDialog.Accepted

    monkeypatch.setattr(CodexEntryDialog, 'exec', accept)
    assert dialog.add_entry()
    assert [e['name'] for e in codex.list_entries(window.doc)] == ['Harrow', 'Mara']
    dialog.entries.selectRow(0)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.Yes)
    assert dialog.remove_entry()
    assert [e['name'] for e in codex.list_entries(window.doc)] == ['Mara']


def test_project_find_searches_siblings_saves_searches_and_shows_links(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    dialog = ProjectFindDialog(window)
    qtbot.addWidget(dialog)
    dialog.query.setText('lamp')
    assert dialog.search()
    assert dialog.results.rowCount() == 2
    assert {dialog.results.item(r, 0).text() for r in range(2)} == {'novel.byax', 'notes.byax'}
    assert dialog.status.text().startswith('2 matching passages')
    navigated = []
    monkeypatch.setattr(window, 'navigate_target', lambda target: navigated.append(target) or True)
    for row in range(2):
        dialog.go_to(dialog.results.item(row, 2))
    assert navigated == [window.doc['blocks'][1]['id']] and 'Open that document' in dialog.status.text()
    dialog.scope.setCurrentIndex(1)
    dialog.search()
    assert dialog.results.rowCount() == 1
    monkeypatch.setattr(QInputDialog, 'getText', lambda *args, **kwargs: ('Lamps', True))
    assert dialog.save_search()
    assert window.doc['settings']['saved_searches']['Lamps']['scope'] == 'document'
    assert dialog.saved.count() == 1
    assert dialog.run_saved('Lamps') and 'Saved search “Lamps”' in dialog.status.text()
    dialog.saved.setCurrentRow(0)
    assert dialog.remove_saved() and dialog.saved.count() == 0
    labels = [dialog.subject.itemText(i) for i in range(dialog.subject.count())]
    assert 'Heading: Chapter One' in labels and 'Codex: Mara' in labels
    dialog.subject.setCurrentIndex(labels.index('Codex: Mara'))
    assert dialog.show_links()
    assert dialog.backlinks.count() == 3  # two in this document, one in notes.byax
    assert 'backlinks' in dialog.status.text()


def test_project_find_needs_a_saved_document(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path, saved=False)
    dialog = ProjectFindDialog(window)
    qtbot.addWidget(dialog)
    dialog.query.setText('lamp')
    assert not dialog.search()
    assert 'Save the document' in dialog.status.text()


def test_saving_updates_an_existing_project_index_only(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    started = []
    monkeypatch.setattr('PySide6.QtCore.QThreadPool.start', lambda self, worker: started.append(worker.fn()))
    window.update_project_index(window.path, window.doc)
    assert started == []  # no index yet: saving never creates one
    index = project_index.ProjectIndex(window.path, doc=window.doc)
    index.refresh(window.doc)
    edited, _ = engine.apply(
        window.doc,
        {
            'operations': [
                {
                    'type': 'insert_text',
                    'target': window.doc['blocks'][1]['id'],
                    'offset': 0,
                    'text': 'Quietly, ',
                }
            ]
        },
    )
    window.update_project_index(window.path, edited)
    assert started == [{'added': 0, 'changed': 1, 'removed': 0, 'unchanged': 3}]
    assert index.search('Quietly')['total'] == 1
