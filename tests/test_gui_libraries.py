import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication, QDialog, QListWidget

from byax import model
from byax.gui import MainWindow


@pytest.fixture
def window(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    w.recovery_dir = tmp_path
    errors = []
    monkeypatch.setattr(w, 'error', lambda error: errors.append(str(error)))
    w.test_errors = errors
    yield w
    w.dirty = False
    w.editor.blockSignals(True)
    w.close()
    assert app


def test_existing_source_picker_inserts_citation_without_network(window, monkeypatch):
    window.doc = model.new_document(text='A claim to cite.')
    window.doc['references']['book'] = {
        'id': 'book',
        'csl': {'type': 'book', 'title': 'Research'},
        'verification': 'manual',
    }
    window.refresh(0)
    cursor = window.editor.textCursor()
    cursor.setPosition(7, QTextCursor.KeepAnchor)
    window.editor.setTextCursor(cursor)

    def choose(dialog):
        items = dialog.findChild(QListWidget)
        assert items.count() == 1
        items.setCurrentRow(0)
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, 'exec', choose)
    window.cite_selection()
    assert not window.test_errors
    citation = next(mark for mark in window.doc['blocks'][0]['marks'] if mark['type'] == 'citation')
    assert citation['value'] == 'book'
    assert citation['start'] == 0
    assert citation['end'] == 7


def test_missing_library_does_not_read_document_path_hint(window, monkeypatch, tmp_path):
    from byax.libraries import LibraryRegistry

    hint = tmp_path / 'untrusted'
    hint.mkdir()
    window.doc['settings']['libraries'] = [
        {'library_id': 'lib_' + 'a' * 32, 'name': 'Missing', 'path_hint': str(hint), 'role': 'supplementary'}
    ]
    monkeypatch.setattr(LibraryRegistry, 'get', lambda self, key: None)
    jobs = []
    monkeypatch.setattr(window, '_index_library', lambda library: jobs.append(library))
    window.refresh_attached_libraries()
    assert not jobs
    assert 'Unavailable libraries: Missing' in window.status_label.text()


@pytest.mark.parametrize(
    'style,presentation', [('mla', 'endnotes'), ('chicago', 'footnotes'), ('apa', 'numbered')]
)
def test_citation_style_switch_preserves_source_and_marks(window, monkeypatch, style, presentation):
    import copy

    from PySide6.QtWidgets import QCheckBox, QComboBox

    window.doc = model.new_document(text='Claim')
    window.doc['references']['source'] = {'id': 'source', 'csl': {'type': 'book', 'title': 'Evidence'}}
    window.doc['blocks'][0]['marks'] = [{'type': 'citation', 'start': 0, 'end': 5, 'value': 'source'}]
    window.refresh(0)
    blocks = copy.deepcopy(window.doc['blocks'])
    sources = copy.deepcopy(window.doc['references'])

    def choose(dialog):
        styles = dialog.findChild(QComboBox, 'citationStyle')
        styles.setCurrentIndex(styles.findData(style))
        modes = dialog.findChild(QComboBox, 'citationPresentation')
        modes.setCurrentIndex(modes.findData(presentation))
        dialog.findChild(QCheckBox, 'citationBibliography').setChecked(False)
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, 'exec', choose)
    window.citation_settings()
    assert not window.test_errors
    assert window.doc['settings']['citations'] == {
        'style': style,
        'presentation': presentation,
        'bibliography': False,
    }
    assert window.doc['blocks'] == blocks
    assert window.doc['references'] == sources


def test_stale_search_result_is_disclosed_and_cannot_be_cited(window, monkeypatch):
    from PySide6.QtWidgets import QDialogButtonBox, QLineEdit, QPlainTextEdit

    window.doc = model.new_document(text='Claim')
    window.refresh(0)
    cursor = window.editor.textCursor()
    cursor.setPosition(5, QTextCursor.KeepAnchor)
    window.editor.setTextCursor(cursor)
    hit = {
        'library_id': 'lib_' + 'a' * 32,
        'document_id': 'doc_test',
        'content_hash': 'a' * 64,
        'chunk_id': 'chunk_test',
        'text': 'Outdated evidence',
        'path': 'research.txt',
        'stale': True,
    }
    monkeypatch.setattr(window, 'background', lambda label, work, done: done([hit]))

    def choose(dialog):
        query = dialog.findChild(QLineEdit)
        query.setText('evidence')
        query.returnPressed.emit()
        items = dialog.findChild(QListWidget)
        assert items.count() == 1
        assert 'stale' in items.item(0).text()
        items.setCurrentRow(0)
        assert 'stale' in dialog.findChild(QPlainTextEdit).toPlainText()
        assert not dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).isEnabled()
        return QDialog.Accepted  # Even a programmatic accept cannot bypass the guard.

    monkeypatch.setattr(QDialog, 'exec', choose)
    window.cite_selection()
    assert not window.doc['references']
    assert not window.doc['blocks'][0]['marks']
    assert 'stale' in window.test_errors[0]


@pytest.mark.parametrize('change', ['revision', 'selection'])
def test_late_citation_search_ignores_changed_context(window, monkeypatch, change):
    from PySide6.QtWidgets import QDialogButtonBox, QLineEdit

    window.doc = model.new_document(text='Claim')
    window.refresh(0)
    cursor = window.editor.textCursor()
    cursor.setPosition(5, QTextCursor.KeepAnchor)
    window.editor.setTextCursor(cursor)
    pending = []
    monkeypatch.setattr(window, 'background', lambda label, work, done: pending.append(done))

    def choose(dialog):
        query = dialog.findChild(QLineEdit)
        query.setText('evidence')
        query.returnPressed.emit()
        assert pending
        if change == 'revision':
            window.doc['revision'] = 'changed'
        else:
            cursor = window.editor.textCursor()
            cursor.clearSelection()
            window.editor.setTextCursor(cursor)
        pending.pop()([{'stale': False}])  # Would fail source conversion if consumed.
        assert dialog.findChild(QListWidget).count() == 0
        assert not dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).isEnabled()
        return QDialog.Rejected

    monkeypatch.setattr(QDialog, 'exec', choose)
    window.cite_selection()
    assert not window.doc['references']
