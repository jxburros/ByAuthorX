"""Version-pinned citations: a citation to a superseded draft or library version is detected, not hidden."""

import copy

import pytest

from byax import engine, grounding, model, storage, version_pins
from byax.citation_render import citation_view
from byax.citations import source_from_hit
from byax.cli import execute
from byax.errors import ByaxError
from byax.libraries import LibraryIndex, LibraryRegistry


def _cite_all(doc, source_id):
    block = doc['blocks'][0]
    return engine.apply(
        doc,
        {
            'operations': [
                {
                    'type': 'cite',
                    'target': block['id'],
                    'start': 0,
                    'end': len(block['text']),
                    'source': source_id,
                }
            ]
        },
    )[0]


@pytest.fixture
def drafts(tmp_path):
    folder = tmp_path / 'book'
    folder.mkdir()
    cited_path = folder / 'notes.byax'
    cited = model.new_document('Research notes', 'The lighthouse was built in 1820.')
    storage.save_document(cited, cited_path)
    essay_path = folder / 'essay.byax'
    source = version_pins.source_from_draft(essay_path, cited_path)
    essay = model.new_document('Essay', 'Built early in the century.')
    essay, _ = engine.apply(
        essay, {'operations': [{'type': 'add_reference', 'id': source['id'], 'values': source}]}
    )
    essay = _cite_all(essay, source['id'])
    storage.save_document(essay, essay_path)
    return essay_path, cited_path, source['id']


def test_a_draft_source_is_pinned_and_current_until_the_draft_changes(drafts):
    essay_path, _cited_path, source_id = drafts
    essay = storage.load_document(essay_path)
    pointer = essay['references'][source_id]['byax']
    assert pointer['path'] == 'notes.byax' and len(pointer['content_hash']) == 64
    report = version_pins.check_pins(essay, document_path=essay_path)
    assert report['summary']['current'] == 1 and not report['findings']


def test_a_superseded_draft_is_reported_with_record_and_repin_proposals(drafts):
    essay_path, cited_path, source_id = drafts
    cited = storage.load_document(cited_path)
    block = cited['blocks'][0]['id']
    cited, _ = engine.apply(
        cited,
        {'operations': [{'type': 'replace_text', 'target': block, 'match': '1820', 'replacement': '1822'}]},
    )
    storage.save_document(cited, cited_path)
    essay = storage.load_document(essay_path)
    original = copy.deepcopy(essay)
    report = version_pins.check_pins(essay, document_path=essay_path)
    assert essay == original  # read-only
    (row,) = report['sources']
    assert row['state'] == 'superseded' and row['message'].startswith(
        'Cites an earlier version of “Research notes”'
    )
    (finding,) = report['findings']
    assert finding['rule'] == 'citations.superseded_version' and finding['range'] == {'start': 0, 'end': 27}
    # Rendering is unchanged until the writer asks for annotation.
    plain = citation_view(essay)
    recorded, _ = engine.apply(essay, {'operations': report['operations']})
    assert (
        recorded['references'][source_id]['superseded_by']['content_hash'] == row['current']['content_hash']
    )
    assert citation_view(recorded)['blocks'][0]['text'] == plain['blocks'][0]['text']
    recorded['settings']['citations'] = {'annotate_superseded': True}
    assert citation_view(recorded)['blocks'][0]['text'].endswith(version_pins.ANNOTATION)
    # The re-pin is a review proposal; accepting it pins the current version again.
    proposed, _ = engine.apply(recorded, {'operations': report['proposals']})
    proposal = proposed['review']['proposals'][-1]
    assert proposal['origin'] == 'version_pin' and proposed['references'] == recorded['references']
    repinned, _ = engine.apply(
        proposed, {'operations': [{'type': 'accept_proposal', 'target': proposal['id']}]}
    )
    assert 'superseded_by' not in repinned['references'][source_id]
    assert version_pins.check_pins(repinned, document_path=essay_path)['summary']['current'] == 1


def test_missing_or_escaping_drafts_are_unavailable(drafts, tmp_path):
    essay_path, cited_path, _source_id = drafts
    cited_path.unlink()
    essay = storage.load_document(essay_path)
    assert version_pins.check_pins(essay, document_path=essay_path)['sources'][0]['state'] == 'unavailable'
    assert (
        version_pins.check_pins(essay)['sources'][0]['state'] == 'unavailable'
    )  # unsaved: no folder to resolve
    outside = tmp_path.parent / 'elsewhere.byax'
    with pytest.raises(ByaxError):
        version_pins.source_from_draft(essay_path, outside)


def test_the_citation_audit_shows_superseded_sources(drafts):
    essay_path, cited_path, _source_id = drafts
    cited = storage.load_document(cited_path)
    cited, _ = engine.apply(
        cited,
        {
            'operations': [
                {'type': 'set_text', 'target': cited['blocks'][0]['id'], 'text': 'Rebuilt in 1901.'}
            ]
        },
    )
    storage.save_document(cited, cited_path)
    report = execute('check', {'path': str(essay_path), 'citations': True})
    assert report['versions']['summary']['superseded'] == 1 and report['review'] == 'required'
    assert report['citations'][0]['superseded']['message'].startswith('Cites an earlier version')
    assert any(line.startswith('Superseded source:') for line in grounding.describe_audit(report))


def test_cli_add_draft_source(drafts, tmp_path):
    essay_path, cited_path, _ = drafts
    other = cited_path.parent / 'other.byax'
    storage.save_document(model.new_document('Other draft', 'Text'), other)
    result = execute('source', {'path': str(essay_path), 'action': 'add-draft', 'draft_path': str(other)})
    added = [
        k
        for k, v in storage.load_document(essay_path)['references'].items()
        if v.get('byax', {}).get('path') == 'other.byax'
    ]
    assert result['changed_ids'] and added


def test_a_superseded_library_version_is_detected(tmp_path, monkeypatch):
    root = tmp_path / 'sources'
    root.mkdir()
    (root / 'notes.txt').write_text('Aria is the captain.', encoding='utf-8')
    registry = LibraryRegistry(tmp_path / 'config')
    definition = registry.create('Research', [root])
    cache = tmp_path / 'cache'
    LibraryIndex(definition, cache).refresh()
    monkeypatch.setattr('byax.libraries.LibraryRegistry', lambda *a, **k: registry)
    original = LibraryIndex
    monkeypatch.setattr(
        'byax.libraries.LibraryIndex', lambda library, cache_dir=None, **k: original(library, cache)
    )
    hit = original(definition, cache).search('Aria')['results'][0]
    source = source_from_hit(hit)
    doc = model.new_document(text='“Aria is the captain.”')
    doc['settings']['libraries'] = [{'library_id': definition['library_id'], 'name': 'Research'}]
    doc, _ = engine.apply(
        doc, {'operations': [{'type': 'add_reference', 'id': source['id'], 'values': source}]}
    )
    doc = _cite_all(doc, source['id'])
    assert version_pins.check_pins(doc)['summary']['current'] == 1
    (root / 'notes.txt').write_text('Aria was the captain until spring.', encoding='utf-8')
    original(definition, cache).refresh()
    report = version_pins.check_pins(doc)
    assert report['summary']['superseded'] == 1 and report['findings'][0]['affected_ids'] == [
        doc['blocks'][0]['id']
    ]
