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


# --- evidence kind, local/remote labels and the extracted-text preview ----------------------------


def select(window, text, start, end):
    window.doc = model.new_document(text=text)
    window.refresh(0)
    cursor = window.editor.textCursor()
    cursor.setPosition(start)
    cursor.setPosition(end, QTextCursor.KeepAnchor)
    window.editor.setTextCursor(cursor)


def add_source(window, excerpt):
    window.doc['references']['book'] = {
        'id': 'book',
        'csl': {'type': 'book', 'title': 'Research'},
        'verification': 'manual',
        'excerpt': excerpt,
    }


def cite_with_picker(window, monkeypatch, *, after_select=None):
    from PySide6.QtWidgets import QComboBox, QLabel

    seen = {}

    def choose(dialog):
        items = dialog.findChild(QListWidget)
        kind = dialog.findChild(QComboBox, 'citationKind')
        seen['initial'] = kind.currentData()
        items.setCurrentRow(0)
        seen['selected'] = kind.currentData()
        seen['reason'] = dialog.findChild(QLabel, 'citationKindReason').text()
        seen['options'] = [kind.itemData(i) for i in range(kind.count())]
        if after_select:
            after_select(dialog, kind)
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, 'exec', choose)
    window.cite_selection()
    assert not window.test_errors
    return seen, window.doc['blocks'][0]['marks'][-1]


def test_cite_picker_preselects_quotation_for_text_found_in_the_source(window, monkeypatch):
    select(window, 'Mars has two moons.', 0, 19)
    add_source(window, 'Mars has two\nmoons. Phobos is larger.')
    seen, mark = cite_with_picker(window, monkeypatch)
    assert seen['selected'] == 'quotation' and mark['kind'] == 'quotation'
    assert 'verbatim' in seen['reason']
    assert seen['options'] == ['quotation', 'paraphrase', 'inference']


def test_cite_picker_preselects_quotation_for_text_in_quotation_marks(window, monkeypatch):
    select(window, '“A fresh idea” appears.', 0, 14)
    add_source(window, 'Unrelated source text.')
    seen, mark = cite_with_picker(window, monkeypatch)
    assert seen['initial'] == 'quotation' and mark['kind'] == 'quotation'
    assert 'quotation marks' in seen['reason']


def test_cite_picker_defaults_to_paraphrase_and_the_user_can_override(window, monkeypatch):
    select(window, 'The red planet has a pair of satellites.', 0, 40)
    add_source(window, 'Mars has two moons: Phobos and Deimos.')
    seen, mark = cite_with_picker(window, monkeypatch)
    assert seen['selected'] == 'paraphrase' and mark['kind'] == 'paraphrase'

    select(window, 'The red planet has a pair of satellites.', 0, 40)
    add_source(window, 'Mars has two moons: Phobos and Deimos.')

    def override(dialog, kind):
        kind.setCurrentIndex(kind.findData('inference'))
        kind.activated.emit(kind.currentIndex())

    seen, mark = cite_with_picker(window, monkeypatch, after_select=override)
    assert mark['kind'] == 'inference'


def test_user_choice_survives_picking_another_result(window, monkeypatch):
    from PySide6.QtWidgets import QComboBox

    select(window, 'Mars has two moons.', 0, 19)
    add_source(window, 'Mars has two moons.')
    window.doc['references']['other'] = {
        'id': 'other',
        'csl': {'type': 'book', 'title': 'Other'},
        'verification': 'manual',
        'excerpt': 'Nothing similar here.',
    }

    def choose(dialog):
        items = dialog.findChild(QListWidget)
        kind = dialog.findChild(QComboBox, 'citationKind')
        items.setCurrentRow(0)
        assert kind.currentData() == 'quotation'
        kind.setCurrentIndex(kind.findData('paraphrase'))
        kind.activated.emit(kind.currentIndex())
        items.setCurrentRow(1)
        items.setCurrentRow(0)
        assert kind.currentData() == 'paraphrase'
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, 'exec', choose)
    window.cite_selection()
    assert window.doc['blocks'][0]['marks'][-1]['kind'] == 'paraphrase'


@pytest.fixture
def indexed_library(window, tmp_path, monkeypatch):
    import platformdirs

    from byax import libraries
    from byax.libraries import LibraryIndex, LibraryRegistry
    from byax.providers import ProviderConfig, ProviderRegistry

    monkeypatch.setattr(libraries, '_default_dir', lambda: tmp_path / 'config')
    monkeypatch.setattr('byax.providers._default_dir', lambda: tmp_path / 'providers')
    monkeypatch.setattr(platformdirs, 'user_cache_path', lambda *a, **k: tmp_path / 'cache')
    root = tmp_path / 'research'
    root.mkdir()
    (root / 'notes.md').write_text(
        '# Moons\n\nMars has two moons.\n\n# Phobos\n\nPhobos is the larger moon.', encoding='utf-8'
    )
    registry = LibraryRegistry()
    library = registry.create('Research', [str(root)])
    LibraryIndex(library).refresh()
    window.doc['settings']['libraries'] = [
        {'library_id': library['library_id'], 'name': 'Research', 'role': 'supplementary'}
    ]
    ProviderRegistry().save(ProviderConfig('home', 'ollama', 'nomic'))
    ProviderRegistry().save(ProviderConfig('cloud', 'openai', 'embed', credential_ref='env:BYAX_KEY'))
    return registry, library


def run_library_panel(window, monkeypatch, inspect):
    from PySide6.QtWidgets import QLabel, QPlainTextEdit, QPushButton

    from byax.gui_libraries import ExtractedTextDialog

    seen = {}

    def choose(dialog):
        if isinstance(dialog, ExtractedTextDialog):
            seen['preview'] = dialog
            seen['preview_text'] = dialog.view.toPlainText()
            seen['preview_summary'] = dialog.summary.text()
            seen['preview_read_only'] = dialog.view.isReadOnly()
            return QDialog.Rejected
        libraries = dialog.findChild(QListWidget)
        libraries.setCurrentRow(0)
        seen['embedder'] = dialog.findChild(QLabel, 'libraryEmbedder').text()
        inspect(dialog)
        seen['buttons'] = [b.text() for b in dialog.findChildren(QPushButton)]
        seen['status'] = dialog.findChild(QPlainTextEdit).toPlainText()
        return QDialog.Rejected

    monkeypatch.setattr(QDialog, 'exec', choose)
    window.library_panel()
    assert not window.test_errors
    return seen


def test_library_panel_labels_the_embedder_local_or_remote(window, monkeypatch, indexed_library):
    registry, library = indexed_library
    seen = run_library_panel(window, monkeypatch, lambda dialog: None)
    assert 'Semantic search: off' in seen['embedder']
    registry.configure_embedding(library['library_id'], {'provider': 'home', 'dimension': 3})
    seen = run_library_panel(window, monkeypatch, lambda dialog: None)
    assert 'home' in seen['embedder'] and 'Local' in seen['embedder']
    assert 'stays on this computer' in seen['embedder']
    registry.configure_embedding(library['library_id'], {'provider': 'cloud', 'dimension': 3})
    seen = run_library_panel(window, monkeypatch, lambda dialog: None)
    assert (
        'cloud' in seen['embedder'] and 'Remote' in seen['embedder'] and 'api.openai.com' in seen['embedder']
    )


def test_extracted_text_preview_shows_text_and_locators_as_indexed(window, monkeypatch, indexed_library):
    from PySide6.QtWidgets import QPushButton

    def open_preview(dialog):
        button = next(b for b in dialog.findChildren(QPushButton) if b.text() == 'Preview extracted text…')
        button.click()

    seen = run_library_panel(window, monkeypatch, open_preview)
    text = seen['preview_text']
    assert 'Mars has two moons.' in text and 'Phobos is the larger moon.' in text
    assert '"ordinal": 0' in text and '"heading_path": ["Moons"]' in text
    assert seen['preview_read_only'] is True
    assert '2 of 2 passages shown' in seen['preview_summary']
    assert 'Preview extracted text…' in seen['buttons']


def test_extracted_text_preview_pages_and_never_writes(window, tmp_path, indexed_library):
    from byax.gui_libraries import ExtractedTextDialog, format_extracted
    from byax.libraries import LibraryIndex

    _, library = indexed_library
    index = LibraryIndex(library)
    files = [f for f in index.status()['files'] if f.get('document_id')]
    source = tmp_path / 'research' / 'notes.md'
    before = source.read_bytes()
    dialog = ExtractedTextDialog(window, library, files)
    dialog.PAGE_CHARS = 15  # a deliberately tiny page
    dialog.load()
    assert dialog.more.isEnabled() and '1 of 2 passages shown' in dialog.summary.text()
    assert 'shown truncated by the read limit' in dialog.view.toPlainText()
    dialog.more.click()
    assert not dialog.more.isEnabled() and 'Phobos' in dialog.view.toPlainText()
    page = index.read(files[0]['document_id'])
    assert format_extracted(page).startswith('[0] {')
    assert source.read_bytes() == before


def test_passage_preview_names_the_provider_and_whether_it_is_remote(window, monkeypatch, indexed_library):
    from PySide6.QtWidgets import QLabel

    from byax.gui_retrieval import select_passages

    labels = {}

    def accept(dialog):
        labels['text'] = dialog.findChild(QLabel, 'passageDestination').text()
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, 'exec', accept)
    passage = {
        'chunk_id': 'c1',
        'text': 'Mars has two moons.',
        'library_name': 'Research',
        'path': 'notes.txt',
        'locator': {'ordinal': 0},
    }
    retrieval = {'included': [passage], 'omitted': [], 'allowed_citation_ids': []}
    select_passages(window, dict(retrieval), 'cloud', True)
    assert labels['text'].startswith('Provider: cloud — Remote') and 'api.openai.com' in labels['text']
    select_passages(window, dict(retrieval), 'home', False)
    assert labels['text'].startswith('Provider: home — Local')
    # An alias that is not saved still gets a label from the caller's own knowledge.
    select_passages(window, dict(retrieval), 'unsaved', True)
    assert 'Remote' in labels['text']


def test_payload_dialog_assistant_picker_and_provider_settings_show_locality(
    window, monkeypatch, indexed_library
):
    from PySide6.QtWidgets import QLabel

    from byax.gui_providers import ProviderDialog, authorize_payload
    from byax.providers import ProviderRegistry

    labels = {}

    def accept(dialog):
        found = dialog.findChild(QLabel, 'payloadDestination')
        labels['text'] = found.text() if found else None
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, 'exec', accept)
    preview = ProviderRegistry().preview('cloud', text='x')
    assert authorize_payload(window, 'Authorize', preview)
    assert labels['text'].startswith('Remote') and 'api.openai.com' in labels['text']
    assert authorize_payload(window, 'Authorize', ProviderRegistry().preview('home', text='x'))
    assert labels['text'].startswith('Local')

    window.ai_panel.refresh_providers()
    box = window.ai_panel.provider_box
    items = [box.itemText(i) for i in range(box.count())]
    assert any(text.startswith('home') and text.endswith('Local') for text in items)

    dialog = ProviderDialog(window)
    assert 'Remote' in dialog.saved.itemText(dialog.saved.findData('cloud'))
    dialog.kind.setCurrentText('ollama')
    assert dialog.locality.text().startswith('Local')
    dialog.endpoint.setText('https://gpu.example.org')
    assert dialog.locality.text().startswith('Remote') and 'gpu.example.org' in dialog.locality.text()


def test_provider_dialog_saves_the_context_cap_and_keeps_the_learned_window(window, indexed_library):
    from byax.gui_providers import ProviderDialog
    from byax.providers import ProviderConfig, ProviderRegistry

    ProviderRegistry().save(ProviderConfig('big', 'ollama', 'llama', context_tokens=32768))
    dialog = ProviderDialog(window)
    dialog.saved.setCurrentIndex(dialog.saved.findData('big'))
    dialog.context_cap.setValue(16384)
    assert dialog.save_config()
    saved = ProviderRegistry().get('big')
    assert saved.max_context_tokens == 16384 and saved.context_tokens == 32768
    dialog.model.setText('other-model')
    assert dialog.save_config()
    assert ProviderRegistry().get('big').context_tokens is None  # a different model: learn again
