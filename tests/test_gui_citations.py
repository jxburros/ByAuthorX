"""Citation chips in the assistant transcript and the Checks tab citation audit (plan 6.2, 6.4).

Runs offscreen with a fake provider. The jump-fidelity check covers every fixture format the library
indexes here (plain text, Markdown, a .byax document and a two-page PDF); ``tests/fixtures/eval``
(the Phase 0 harness corpus) is not in this base yet.
"""

import copy

import pytest
from PySide6.QtCore import QSettings, QUrl

from byax import grounding, model
from byax.gui import MainWindow
from byax.gui_libraries import ExtractedTextDialog
from byax.libraries import LibraryIndex, LibraryRegistry
from byax.providers import ProviderConfig, ProviderRegistry
from byax.storage import save_document

FIXTURE_TEXT = {
    'notes.txt': 'Aria is the captain.\n\nThe lamp burned all night and the glass held.',
    'harbour.md': '# Harbour\n\nThe storm reached the harbour at dawn.\n\n## Later\n\nThe café roof was lost.',
}
PDF_PAGES = ['The first page mentions the copper lamp.', 'Page two records the storm at dawn.']
BYAX_TEXT = 'Mira keeps the observatory.\nThe telescope faces north.'


def write_pdf(path, pages):
    from PySide6.QtGui import QPageSize, QPainter, QPdfWriter

    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QPageSize.A4))
    painter = QPainter(writer)
    for number, text in enumerate(pages):
        if number:
            writer.newPage()
        painter.drawText(200, 400, text)
    painter.end()


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    settings = QSettings(str(tmp_path / 'settings.ini'), QSettings.IniFormat)
    monkeypatch.setattr('byax.gui.QSettings', lambda *_: settings)
    monkeypatch.setattr('byax.providers._default_dir', lambda: tmp_path / 'config')
    w = MainWindow()
    qtbot.addWidget(w)
    w.show()
    w.doc = model.new_document(text='The keeper wrote in the log.\nA second line.')
    w.refresh()
    errors = []
    monkeypatch.setattr(w, 'error', lambda error: errors.append(str(error)))
    monkeypatch.setattr(w, 'confirm_discard', lambda: True)
    w.test_errors = errors
    return w


@pytest.fixture
def library(qtbot, tmp_path, monkeypatch):
    root = tmp_path / 'sources'
    root.mkdir()
    for name, text in FIXTURE_TEXT.items():
        (root / name).write_text(text, encoding='utf-8')
    write_pdf(root / 'harbour-log.pdf', PDF_PAGES)
    save_document(model.new_document(text=BYAX_TEXT), root / 'observatory.byax')
    registry = LibraryRegistry(tmp_path / 'libraries')
    definition = registry.create('Research', [root])
    cache = tmp_path / 'cache'
    LibraryIndex(definition, cache).refresh()
    real = LibraryIndex
    monkeypatch.setattr('byax.libraries.LibraryRegistry', lambda *a, **k: registry)
    monkeypatch.setattr(
        'byax.libraries.LibraryIndex', lambda library, cache_dir=None, **k: real(library, cache)
    )
    index = real(definition, cache)
    return definition, index


def passage(index, term):
    hit = index.search(term)['results'][0]
    return dict(hit, library_name='Research', title=hit['path'])


def ask(window, qtbot, monkeypatch, tmp_path, reply, passages):
    ProviderRegistry(tmp_path / 'config').save(ProviderConfig('helper', 'ollama', 'fixture-model'))
    panel = window.ai_panel
    panel.refresh_providers()
    original = panel.context

    def context(frozen):
        value = original(frozen)
        value['library_passages'] = copy.deepcopy(passages)
        return value

    monkeypatch.setattr(panel, 'context', context)
    monkeypatch.setattr(
        ProviderRegistry, 'chat', lambda self, alias, messages, **kw: {'reply': reply, 'proposals': []}
    )
    panel.input.setPlainText('What does the log say?')
    panel.send()
    qtbot.waitUntil(lambda: panel._pending is None)
    return panel


def test_chips_render_with_hover_card_and_unverified_marker(window, qtbot, monkeypatch, tmp_path, library):
    _, index = library
    lamp, storm = passage(index, 'lamp burned'), passage(index, 'storm harbour')
    reply = (
        f'The log says "The lamp burned all night" [[cite:{lamp["chunk_id"]}]]. '
        f'A storm came at dawn [[cite:{storm["chunk_id"]}]]. Gulls nested [[cite:chunk_invented]].'
    )
    panel = ask(window, qtbot, monkeypatch, tmp_path, reply, [lamp, storm])
    html = panel.history.toHtml()
    assert 'cite:0' in html and 'cite:1' in html and 'cite:2' in html
    transcript = panel.history.toPlainText()
    assert '[[cite:' not in transcript
    assert '[1]' in transcript and '[2~]' in transcript and '[3?]' in transcript
    assert 'marked unverified' in transcript
    # Quote found; a paraphrase sharing part of the wording; an ID that was never supplied.
    assert [c['record']['state'] for c in panel.citations] == ['supported', 'partial', 'unverified']
    panel.history.highlighted.emit(QUrl('cite:0'))
    card = panel.last_chip_tooltip
    assert 'notes.txt' in card and 'paragraph 3' in card
    assert 'background-color:#ffe58a; color:#000;">The lamp burned all night</span>' in card
    assert 'Quote found' in card
    panel.history.highlighted.emit(QUrl('https://example.invalid'))
    assert panel.chip_tooltip(2).count('chunk_invented') == 1
    assert not window.test_errors


def test_jump_fidelity_is_total_on_the_fixture_library(window, qtbot, monkeypatch, tmp_path, library):
    _, index = library
    cases = [
        (passage(index, 'glass held'), 'the glass held', {'ordinal': 2}),
        (passage(index, 'café roof'), 'The café roof was lost', {'heading_path': ['Harbour', 'Later']}),
        (passage(index, 'telescope'), 'telescope faces north', {}),
        (passage(index, 'copper lamp'), 'mentions the copper lamp', {'page': 1}),
        (passage(index, 'records storm'), 'records the storm at dawn', {'page': 2}),
    ]
    reply = ' '.join(f'It says "{quote}" [[cite:{p["chunk_id"]}]].' for p, quote, _ in cases)
    panel = ask(window, qtbot, monkeypatch, tmp_path, reply, [p for p, _, _ in cases])
    monkeypatch.setattr(ExtractedTextDialog, 'exec', lambda dialog: 0)
    landed = 0
    for number, (hit, quote, expected) in enumerate(cases):
        panel.history.anchorClicked.emit(QUrl(f'cite:{number}'))
        dialog = panel.last_citation_dialog
        focused = dialog.focused
        assert focused['chunk_id'] == hit['chunk_id'] and focused['document_id'] == hit['document_id']
        assert focused['locator'] == {**hit['locator'], 'chunk': hit['chunk_id']}
        for key, value in expected.items():
            assert focused['locator'][key] == value
        if hit['path'].endswith('.byax'):
            assert focused['locator']['block'].startswith('p_')
        assert ' '.join(focused['selected'].split()) == quote
        assert 'Opened at' in dialog.summary.text()
        landed += 1
    assert landed == len(cases)  # 100% of jumps land on the recorded page, block or offset


def test_insert_as_citation_creates_a_verifiable_mark(window, qtbot, monkeypatch, tmp_path, library):
    _, index = library
    lamp = passage(index, 'lamp burned')
    panel = ask(
        window,
        qtbot,
        monkeypatch,
        tmp_path,
        f'"The lamp burned all night" [[cite:{lamp["chunk_id"]}]].',
        [lamp],
    )
    monkeypatch.setattr(ExtractedTextDialog, 'exec', lambda dialog: 0)
    window.doc['settings']['libraries'] = [{'library_id': library[0]['library_id'], 'name': 'Research'}]
    panel.open_citation(0)
    dialog = panel.last_citation_dialog
    assert dialog.insert_button.text() == 'Insert as citation'
    window.editor.selection_targets = list
    dialog.insert_button.click()
    assert 'Select the text' in window.status_label.text()
    block = window.doc['blocks'][0]
    window.editor.selection_targets = lambda: [(block['id'], 4, 10)]
    dialog.insert_button.click()
    mark = next(m for m in window.doc['blocks'][0]['marks'] if m['type'] == 'citation')
    assert mark['quote'] == 'The lamp burned all night' and mark['kind'] == 'quotation'
    assert mark['locator']['chunk'] == lamp['chunk_id'] and mark['locator']['ordinal'] == 2
    assert mark['anchor']['quote'] == 'The lamp burned all night'
    assert mark['anchor']['content_hash'] == lamp['content_hash'] and mark['anchor']['state'] == 'anchored'
    assert window.doc['references'][mark['value']]['library']['chunk_id'] == lamp['chunk_id']
    # Ordinary verification covers it afterwards.
    report = grounding.audit_citations(window.doc)
    assert [item['status'] for item in report['citations']] == ['quote_found']
    assert not window.test_errors


def test_checks_tab_citation_audit_matches_the_cli_report(window, qtbot, monkeypatch, tmp_path, library):
    from byax import engine
    from byax.gui_review import audit_html

    definition, index = library
    lamp = index.search('lamp burned')['results'][0]
    from byax.citations import source_from_hit

    source = source_from_hit(lamp)
    doc = model.new_document(text='“The lamp burned all night”\nA paraphrase of the log.')
    doc['settings']['libraries'] = [{'library_id': definition['library_id'], 'name': 'Research'}]
    blocks = doc['blocks']
    doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {'type': 'add_reference', 'id': source['id'], 'values': source},
                {
                    'type': 'cite',
                    'target': blocks[0]['id'],
                    'start': 0,
                    'end': len(blocks[0]['text']),
                    'source': source['id'],
                    'kind': 'quotation',
                    'locator': {'chunk': lamp['chunk_id']},
                },
                {
                    'type': 'cite',
                    'target': blocks[1]['id'],
                    'start': 0,
                    'end': len(blocks[1]['text']),
                    'source': source['id'],
                    'kind': 'paraphrase',
                },
            ]
        },
    )
    window.doc = doc
    window.refresh()
    checks = window.checks_panel
    assert 'citations' in checks.toggles and not checks.toggles['citations'].isChecked()
    checks.run_buttons['citations'].click()
    qtbot.waitUntil(lambda: getattr(checks, 'last_audit', None) is not None)
    assert checks.last_audit == grounding.audit_citations(doc)
    shown = checks.audit_dialog.browser.toPlainText()
    assert 'Quote found' in shown and 'Not checkable' in shown
    assert not checks.audit_dialog.apply_button.isEnabled()
    unanchored = copy.deepcopy(checks.last_audit)
    unanchored['citations'][0]['status'] = 'unanchored'
    assert '<s>The lamp burned all night</s>' in audit_html(unanchored)
    checks.audit_dialog.close()
