"""File > Compile… in the desktop window (offscreen)."""

import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QInputDialog, QWidget
from test_compile import manuscript

from byax import gui_a11y
from byax.gui import MainWindow
from byax.gui_compile import CompileDialog
from byax.model import canonical_json


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
    w.doc = manuscript()
    w.refresh(0)
    yield w
    w.dirty = False
    w.editor.blockSignals(True)
    w.close()


@pytest.fixture
def dialog(window, monkeypatch):
    shown = []

    def fake_exec(self):
        self.show()
        QApplication.processEvents()
        shown.append(self)
        return 0

    monkeypatch.setattr(QDialog, 'exec', fake_exec)
    window.compile_book()
    assert shown and isinstance(shown[0], CompileDialog)
    return shown[0]


def test_file_menu_offers_compile(window):
    labels = [action.text() for action in window.findChildren(QAction)]
    assert 'Compile…' in labels


def test_dialog_lists_binder_items_with_notes_left_out_and_every_control_is_named(dialog):
    rows = [dialog.items.item(i) for i in range(dialog.items.count())]
    assert len(rows) == 9
    notes = next(row for row in rows if row.text().startswith('Research notes'))
    assert notes.checkState() == Qt.Unchecked
    assert all(row.checkState() == Qt.Checked for row in rows if row is not notes)
    assert [dialog.preset.itemData(i) for i in range(dialog.preset.count())] == [
        'novel',
        'nonfiction',
        'manuscript',
    ]
    dialog.preset.setCurrentIndex(2)
    assert dialog.target.currentData() == 'docx'  # the manuscript preset's own target
    for widget in [dialog, *dialog.findChildren(QWidget)]:
        if gui_a11y.actionable(widget):
            assert widget.accessibleName().strip() and widget.toolTip().strip(), widget


def test_preflight_lists_findings_without_writing(dialog, window):
    window.doc['metadata']['author'] = ''
    before = canonical_json(window.doc)
    dialog.target.setCurrentIndex(dialog.target.findData('pdf'))
    report = dialog.run_preflight()
    assert report['preflight_only'] is True and report['outputs'] == []
    lines = [dialog.findings.item(i).text() for i in range(dialog.findings.count())]
    assert any('no author' in line for line in lines)
    assert 'pages' in dialog.status.text()
    assert canonical_json(window.doc) == before and not window.test_errors


def test_compile_writes_the_chosen_items_and_leaves_the_document_alone(dialog, window, tmp_path, monkeypatch):
    before = canonical_json(window.doc)
    dialog.target.setCurrentIndex(dialog.target.findData('epub'))
    for i in range(dialog.items.count()):
        item = dialog.items.item(i)
        if item.text().strip().startswith('The Storm'):
            item.setCheckState(Qt.Unchecked)
    path = tmp_path / 'book.epub'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', staticmethod(lambda *a, **k: (str(path), '')))
    report = dialog.run_compile()
    assert path.is_file() and report['outputs'] == [str(path.resolve())]
    assert 'ch_2' not in report['compile']['epub']['chapters'][0]['blocks']
    assert all('ch_2' not in chapter['blocks'] for chapter in report['compile']['epub']['chapters'])
    assert canonical_json(window.doc) == before
    assert window.last_compile_report is report and 'Saved book.epub' in dialog.status.text()
    assert not window.test_errors


def test_save_preset_commits_one_settings_operation(dialog, window, monkeypatch):
    dialog.trim.setCurrentIndex(dialog.trim.findData('6x9'))
    monkeypatch.setattr(QInputDialog, 'getText', staticmethod(lambda *a, **k: ('Six by nine', True)))
    assert dialog.save_preset() is True
    saved = window.doc['settings']['compile_presets']['Six by nine']
    assert saved['design']['trim'] == '6x9'
    assert dialog.preset.findData('Six by nine') >= 0
    window.undo()
    assert 'compile_presets' not in window.doc['settings']
