"""Library quotations use current, authorized indexed evidence, not stored excerpts."""

import copy

import pytest

from byax import engine, model
from byax.checks import run_checks
from byax.citations import source_from_hit
from byax.libraries import LibraryIndex, LibraryRegistry
from byax.library_checks import invalidate_library_findings


@pytest.fixture
def cited(tmp_path, monkeypatch):
    root = tmp_path / 'sources'
    root.mkdir()
    path = root / 'notes.txt'
    path.write_text('Aria is the captain.\n\nBren is the navigator.')
    registry = LibraryRegistry(tmp_path / 'config')
    definition = registry.create('Research', [root])
    index = LibraryIndex(definition, tmp_path / 'cache')
    index.refresh()
    monkeypatch.setattr('byax.libraries.LibraryRegistry', lambda: registry)
    monkeypatch.setattr('byax.libraries.LibraryIndex', lambda library, cache_dir=None: index)
    hit = index.search('Aria')['results'][0]
    source = source_from_hit(hit)
    doc = model.new_document(text='“Aria is the captain.”')
    doc['references'][source['id']] = source
    doc['settings']['libraries'] = [
        {'library_id': definition['library_id'], 'name': 'Research', 'role': 'exclusive'}
    ]
    doc['blocks'][0]['marks'] = [
        {
            'type': 'citation',
            'value': source['id'],
            'start': 0,
            'end': len(doc['blocks'][0]['text']),
            'locator': {'chunk': hit['chunk_id']},
        }
    ]
    return doc, path, index, source['id'], hit


def library_coverage(result):
    return [c for c in result['coverage'] if c.get('rule') == 'factual.library_citation']


def test_live_chunk_evidence_and_exact_locator(cited):
    doc, _, _, source_id, hit = cited
    original = copy.deepcopy(doc)
    report = run_checks(doc, 'factual', track=True)
    record = next(c['evidence'] for c in library_coverage(report) if c.get('evidence'))
    evidence = record['evidence'][0]
    assert record['verification'] == 'verified'
    assert evidence['chunk_id'] == hit['chunk_id']
    assert evidence['content_hash'] == hit['content_hash']
    assert doc == original
    doc['blocks'][0]['text'] = '“Aria is the admiral.”'
    report = run_checks(doc, 'factual')
    finding = next(f for f in report['findings'] if f['rule'] == 'factual.library_citation')
    assert finding['range'] == {'start': 0, 'end': len(doc['blocks'][0]['text'])}
    assert finding['measured']['source_id'] == source_id
    assert finding['operations'] == []


def test_library_change_breaks_incremental_reuse_before_and_after_refresh(cited):
    doc, path, index, _, _ = cited
    first = run_checks(doc, 'factual', track=True)
    same = run_checks(doc, 'factual', previous=first, track=True)
    assert same['evaluation']['reused']
    path.write_text('Aria is now the admiral of the fleet.')
    changed = run_checks(doc, 'factual', previous=first, track=True)
    assert not changed['evaluation']['reused']
    assert changed['findings'][0]['measured']['verification'] == 'stale'
    index.refresh()
    refreshed = run_checks(doc, 'factual')
    assert refreshed['findings'][0]['measured']['verification'] == 'stale'


def test_missing_or_unattached_library_never_falls_back_to_stored_excerpt(cited, monkeypatch):
    doc, _, index, _, _ = cited
    doc['settings']['libraries'] = []
    monkeypatch.setattr(index, 'read', lambda *args, **kwargs: pytest.fail('Unattached library read'))
    report = run_checks(doc, 'factual')
    assert report['findings'][0]['measured']['verification'] == 'unverifiable'
    assert not any(c.get('evidence', {}).get('verification') == 'verified' for c in library_coverage(report))


def test_refresh_invalidates_findings_and_unknown_dependency_scope_expands(cited):
    doc, _, index, _, _ = cited
    doc['blocks'][0]['text'] = '“Aria is the admiral.”'
    report = run_checks(doc, 'factual', scope=doc['blocks'][0]['id'], track=True)
    assert report['evaluation']['expanded']
    doc['review']['findings'] = report['findings']
    invalidate_library_findings(doc, index.library['library_id'])
    assert doc['review']['findings'][0]['status'] == 'stale'


def test_recorded_check_keeps_snapshot_without_reading_library_during_edits(cited, monkeypatch):
    doc, _, index, _, _ = cited
    doc['blocks'][0]['text'] = '“Aria is the admiral.”'
    doc, _ = engine.apply(doc, {'operations': [{'type': 'record_checks', 'values': {'suites': ['factual']}}]})
    assert doc['review']['findings'][0]['status'] == 'open'
    monkeypatch.setattr(
        index, 'read', lambda *args, **kwargs: pytest.fail('Typing must not read the library')
    )
    doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {'type': 'insert_text', 'target': doc['blocks'][0]['id'], 'offset': 0, 'text': 'Changed '}
            ]
        },
    )
    assert doc['review']['findings'][0]['status'] == 'stale'


def test_search_hit_full_locator_survives_cite_and_factual_review(cited):
    doc, _, _, source_id, hit = cited
    doc['blocks'][0]['marks'] = []
    doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {
                    'type': 'cite',
                    'target': doc['blocks'][0]['id'],
                    'start': 0,
                    'end': len(doc['blocks'][0]['text']),
                    'source': source_id,
                    'kind': 'quotation',
                    'locator': {**hit['locator'], 'chunk': hit['chunk_id']},
                }
            ]
        },
    )
    result = run_checks(doc, 'factual')
    record = next(c['evidence'] for c in library_coverage(result) if c.get('evidence'))
    assert record['verification'] == 'verified'
    assert record['evidence'][0]['locator'] == hit['locator']


def test_gui_index_completion_invalidates_current_document_only(cited):
    from types import SimpleNamespace

    from byax.gui_libraries import LibraryPanels

    doc, _, index, source_id, _ = cited
    doc['review']['findings'] = [{'status': 'open', 'measured': {'source_id': source_id}}]
    callbacks, updated = [], []
    window = SimpleNamespace(
        doc=doc,
        update_sidebar=lambda: updated.append(True),
        start_job=lambda kind, payload, worker, done: callbacks.append(done),
    )
    LibraryPanels._index_library(window, index.library)
    callbacks.pop()({})
    assert doc['review']['findings'][0]['status'] == 'stale'
    assert updated == [True]
    LibraryPanels._index_library(window, index.library)
    window.doc = model.new_document(text='Another document')
    callbacks.pop()({})
    assert updated == [True]
