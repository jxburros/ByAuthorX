"""Research, preservation and publication remain explicit and leave the master intact."""

import copy
import json
import zipfile

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDialog

from byax import engine, model, storage
from byax.authoring import citation_evidence, create_research_sample, protected_spans
from byax.gui import MainWindow
from byax.gui_authoring import ShareDialog, readable_review
from byax.libraries import LibraryIndex, LibraryRegistry
from byax.providers import validate_proposed_edits
from byax.publication import export_clean, publication_preview


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    settings = QSettings(str(tmp_path / 'settings.ini'), QSettings.IniFormat)
    monkeypatch.setattr('byax.gui.QSettings', lambda *_: settings)
    monkeypatch.setattr('byax.providers._default_dir', lambda: tmp_path / 'providers')
    w = MainWindow()
    qtbot.addWidget(w)
    return w


def test_offline_sample_evidence_and_changed_source(tmp_path, monkeypatch):
    registry = LibraryRegistry(tmp_path / 'config')
    cache = tmp_path / 'cache'
    path = create_research_sample(tmp_path, registry=registry, cache_dir=cache)
    doc = storage.load_document(path)
    original = copy.deepcopy(doc)
    # Any attempt to use a model or retrieve remote data must fail the tutorial.
    monkeypatch.setattr('httpx.Client.request', lambda *a, **k: pytest.fail('Unexpected network'))
    report = citation_evidence(doc, registry=registry, cache_dir=cache)
    row = report['citations'][0]
    assert row['status'] == 'available'
    assert row['method'] == 'Exact quotation check'
    assert row['outcome'].startswith('Quotation matches')
    assert row['passages'][0]['text'].startswith('The pilot included 24')
    source_file = path.parent / 'research' / 'pilot-notes.txt'
    source_file.write_text('The pilot included 99 participants.', encoding='utf-8')
    stale = citation_evidence(doc, registry=registry, cache_dir=cache)['citations'][0]
    assert stale['status'] == 'stale'
    assert stale['passages'] == []  # No fallback to the stored excerpt.
    LibraryIndex(registry.list()[0], cache_dir=cache).refresh()
    assert citation_evidence(doc, registry=registry, cache_dir=cache)['citations'][0]['status'] == 'stale'
    assert doc == original


def test_paraphrase_and_manual_sources_do_not_claim_support(tmp_path):
    registry = LibraryRegistry(tmp_path / 'config')
    path = create_research_sample(tmp_path, registry=registry, cache_dir=tmp_path / 'cache')
    doc = storage.load_document(path)
    block = doc['blocks'][1]
    block['text'] = 'The study was representative.'
    block['marks'][0].update(start=0, end=len(block['text']), kind='paraphrase')
    row = citation_evidence(doc, registry=registry, cache_dir=tmp_path / 'cache')['citations'][0]
    assert row['outcome'] == 'Needs interpretation'
    doc['references'][row['source_id']].pop('library')
    assert citation_evidence(doc)['citations'][0]['status'] == 'manual'


@pytest.mark.parametrize(
    'text,start,end,replacement,constraints',
    [
        ('24 participants', 1, 2, '9', {'preserve_numbers': True}),
        ('-24 degrees', 0, 1, '', {'preserve_numbers': True}),
        ('1e-3 grams', 2, 3, '', {'preserve_numbers': True}),
        ('“Distinct voice.”', 1, 9, 'Generic', {'preserve_quotes': True}),
        ("'Distinct voice.'", 1, 9, 'Generic', {'preserve_quotes': True}),
        ('ByAuthorX writes', 0, 9, 'AnotherApp', {'terms': ['ByAuthorX']}),
        ('A sentence.', 0, 1, 'The', {'brief': 'structure'}),
    ],
)
def test_returned_edits_enforce_author_choices(text, start, end, replacement, constraints):
    block = model.new_document(text=text)['blocks'][0]
    edit = {
        'target': block['id'],
        'start': start,
        'end': end,
        'expected_text': text[start:end],
        'replacement': replacement,
    }
    with pytest.raises(ValueError):
        validate_proposed_edits(
            [edit], {'revision': 'rev_1', 'blocks': [block], 'author_constraints': constraints}
        )


def test_selection_inside_quote_and_citation_cannot_evade_preservation():
    block = model.new_document(text='Start “keep this” end')['blocks'][0]
    constraints = {'preserve_quotes': True}
    selection = {
        **block,
        'text': 'keep',
        'source_offset': 7,
        'author_protected_spans': protected_spans(block, constraints),
    }
    edit = {'target': block['id'], 'start': 0, 'end': 4, 'expected_text': 'keep', 'replacement': 'change'}
    with pytest.raises(ValueError, match='quotation'):
        validate_proposed_edits(
            [edit], {'revision': 'rev_1', 'blocks': [selection], 'author_constraints': constraints}
        )
    selection.pop('author_protected_spans')
    selection['marks'] = [{'type': 'citation', 'start': 7, 'end': 16, 'value': 's'}]
    with pytest.raises(ValueError, match='cited'):
        validate_proposed_edits(
            [edit],
            {'revision': 'rev_1', 'blocks': [selection], 'author_constraints': {'preserve_citations': True}},
        )


def test_valid_preserving_edit_registers_review_only():
    doc = model.new_document(text='The 24 participants was here.')
    block = doc['blocks'][0]
    constraints = {'brief': 'light', 'preserve_numbers': True}
    edit = {
        'target': block['id'],
        'start': 20,
        'end': 23,
        'expected_text': 'was',
        'replacement': 'were',
        'explanation': 'Subject agreement',
    }
    proposals = validate_proposed_edits(
        [edit], {'revision': doc['revision'], 'blocks': [block], 'author_constraints': constraints}
    )
    updated, _ = engine.apply(doc, {'operations': [{'type': 'propose', 'values': proposals[0]}]})
    assert model.text_of(updated) == model.text_of(doc)
    record = updated['review']['proposals'][0]
    assert record['author_constraints'] == constraints
    assert 'Subject agreement' in readable_review(record)
    accepted, _ = engine.apply(updated, {'operations': [{'type': 'accept_proposal', 'target': record['id']}]})
    assert model.text_of(accepted) == 'The 24 participants were here.'


def test_share_preview_matches_serialized_policy_and_preserves_master(tmp_path):
    doc = model.new_document(text='Public statement')
    doc['metadata']['author'] = 'PRIVATE AUTHOR'
    doc['review']['comments'] = [
        {'id': 'comment_1', 'target': doc['blocks'][0]['id'], 'text': 'PRIVATE COMMENT', 'status': 'open'}
    ]
    original = copy.deepcopy(doc)
    preview = publication_preview(doc, 'docx')
    result = export_clean(doc, tmp_path / 'public.docx')
    assert preview['inclusion'] == result['inclusion']
    with zipfile.ZipFile(tmp_path / 'public.docx') as archive:
        serialized = b''.join(archive.read(name) for name in archive.namelist())
    assert b'PRIVATE AUTHOR' not in serialized and b'PRIVATE COMMENT' not in serialized
    assert b'Public statement' in serialized
    assert doc == original


def test_review_stops_before_provider_when_flush_fails(window, monkeypatch):
    from byax.gui_providers import review_document

    monkeypatch.setattr(window, 'flush_edits', lambda: False)
    monkeypatch.setattr(
        'byax.gui_providers.select_provider', lambda *a: pytest.fail('Selected provider after failed flush')
    )
    review_document(window)


def test_preservation_ui_reaches_exact_outbound_review_context(window, monkeypatch):
    from byax import gui_providers
    from byax.providers import ProviderConfig, ProviderRegistry

    registry = ProviderRegistry()
    registry.save(ProviderConfig('local', 'ollama', 'test-model'))
    monkeypatch.setattr(gui_providers, 'select_provider', lambda *a: (registry, 'local'))
    previews = []
    monkeypatch.setattr(
        gui_providers, 'authorize_payload', lambda w, t, preview: previews.append(preview) or False
    )
    window.review_checks.brief.setCurrentIndex(window.review_checks.brief.findData('voice'))
    window.review_checks.preservation['preserve_numbers'].setChecked(True)
    window.review_checks.terms.setText('ByAuthorX, API')
    gui_providers.review_document(window, check='voice', instructions='Check voice')
    assert 'preserve_numbers' in json.dumps(previews[0])
    assert 'ByAuthorX' in json.dumps(previews[0])
    assert not window.doc['review']['proposals']


def test_share_dialog_ignores_old_preview_and_blocks_changed_master(window, monkeypatch):
    jobs = []
    monkeypatch.setattr(window, 'background', lambda label, fn, done, **kw: jobs.append((fn, done)))
    dialog = ShareDialog(window)
    dialog.format.setCurrentIndex(dialog.format.findData('txt'))
    jobs[0][1](jobs[0][0]())
    assert not dialog.publish.isEnabled()
    jobs[1][1](jobs[1][0]())
    assert dialog.publish.isEnabled()
    monkeypatch.setattr(window, 'message', lambda *args: None)
    window.doc['revision'] = 'changed'
    assert not dialog.current()


def test_research_guide_is_modeless_and_evidence_has_readable_passage(window, monkeypatch):
    window.research_walkthrough()
    assert not window._research_guide.isModal()
    window._research_guide.close()
    visited = []
    monkeypatch.setattr(QDialog, 'exec', lambda self: visited.append(self.windowTitle()) or QDialog.Rejected)
    window.show_evidence({'revision': window.doc['revision'], 'citations': []})
    assert visited == ['Claims and evidence']


def test_late_review_cannot_register_in_another_document(window, monkeypatch):
    from byax import gui_providers
    from byax.providers import ProviderConfig, ProviderRegistry

    registry = ProviderRegistry()
    registry.save(ProviderConfig('local', 'ollama', 'test-model'))
    monkeypatch.setattr(gui_providers, 'select_provider', lambda *a: (registry, 'local'))
    monkeypatch.setattr(gui_providers, 'authorize_payload', lambda *a: True)
    jobs, reports = [], []
    monkeypatch.setattr(window, 'background', lambda label, fn, done: jobs.append(done))
    monkeypatch.setattr(window, 'report', lambda title, report: reports.append(report))
    gui_providers.review_document(window, check='voice')
    revision = window.doc['revision']
    window.doc = model.new_document(text='Another document')
    window.doc['revision'] = revision  # Even identical revision strings must not grant authority.
    jobs[0]({'findings': [], 'proposals': [{'operations': [{'type': 'set_text'}]}]})
    assert reports[0]['stale']
    assert not window.doc['review']['proposals']


def test_evidence_view_does_not_mix_previous_document_assessments(window, monkeypatch):
    from PySide6.QtWidgets import QListWidget

    window._last_report = {
        'findings': [{'document_id': 'another-doc', 'claim': 'Private old claim', 'outcome': 'supported'}]
    }
    counts = []
    monkeypatch.setattr(
        QDialog, 'exec', lambda dialog: counts.append(dialog.findChild(QListWidget).count()) or 0
    )
    window.show_evidence({'revision': window.doc['revision'], 'citations': []})
    assert counts == [0]


def test_bounded_evidence_is_identical_through_cli_and_mcp(tmp_path, monkeypatch):
    from byax.cli import execute
    from byax.mcp import handle

    doc = model.new_document(text='First claim\nSecond claim\nThird claim')
    doc['references']['s'] = {
        'id': 's',
        'csl': {'type': 'book', 'title': 'Manual source'},
        'verification': 'manual',
    }
    for block in doc['blocks']:
        block['marks'] = [{'type': 'citation', 'start': 0, 'end': len(block['text']), 'value': 's'}]
    path = tmp_path / 'claims.byax'
    storage.save_document(doc, path)
    original = path.read_bytes()
    reads = []
    monkeypatch.setattr('byax.authoring.read_citation', lambda *args, **kw: reads.append(args[1]) or None)
    args = {'path': str(path), 'limit': 1, 'offset': 1}
    result = execute('evidence', args)
    assert len(reads) == 1
    assert result['total'] == 3 and result['next_offset'] == 2 and result['truncated']
    assert result['citations'][0]['claim'] == 'Second claim'
    mcp = handle(
        {
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'tools/call',
            'params': {'name': 'byax_evidence', 'arguments': args},
        }
    )
    payload = json.loads(mcp['result']['content'][0]['text'])
    assert payload['citations'] == result['citations']
    listing = handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})['result']['tools']
    assert next(t for t in listing if t['name'] == 'byax_evidence')['annotations']['readOnlyHint']
    assert path.read_bytes() == original
    from byax.errors import ByaxError

    with pytest.raises(ByaxError, match='limit'):
        execute('evidence', {**args, 'limit': 101})
