"""Live excerpts, the where-used view and unanchored-never-erased (context engine plan 9, A2)."""

import copy

import pytest

from byax import engine, excerpts, model
from byax.citations import source_from_hit
from byax.errors import ByaxError
from byax.libraries import LibraryIndex, LibraryRegistry
from byax.migration import migrate_document

NOTES = 'Aria is the captain.\n\nBren is the navigator of the long ship.\n\nCora keeps the lighthouse lamp.'


@pytest.fixture
def library(tmp_path, monkeypatch):
    root = tmp_path / 'sources'
    root.mkdir()
    (root / 'notes.txt').write_text(NOTES, encoding='utf-8')
    registry = LibraryRegistry(tmp_path / 'config')
    definition = registry.create('Research', [root])
    cache = tmp_path / 'cache'
    LibraryIndex(definition, cache).refresh()
    monkeypatch.setattr('byax.libraries.LibraryRegistry', lambda *a, **k: registry)
    original = LibraryIndex
    monkeypatch.setattr(
        'byax.libraries.LibraryIndex', lambda library, cache_dir=None, **k: original(library, cache)
    )
    return root, registry, definition, cache


def hit(definition, cache, term):
    return next(iter(LibraryIndex(definition, cache).search(term)['results']))


def document(library):
    _root, _registry, definition, cache = library
    aria = hit(definition, cache, 'Aria')
    source = source_from_hit(aria)
    doc = model.new_document(text='Intro\nBody paragraph about the crew.\nConclusion')
    doc['blocks'][0].update(type='heading', style='heading1', attrs={'level': 1})
    doc['blocks'][2].update(type='heading', style='heading1', attrs={'level': 1})
    doc['settings']['libraries'] = [{'library_id': definition['library_id'], 'name': 'Research'}]
    doc, _ = engine.apply(
        doc, {'operations': [{'type': 'add_reference', 'id': source['id'], 'values': source}]}
    )
    return doc, source, aria


def add_excerpt(doc, library, source, aria, **kwargs):
    _root, _registry, _definition, cache = library
    planned = excerpts.add_operations(
        doc,
        source=source['id'],
        after=doc['blocks'][1]['id'],
        chunk_id=aria['chunk_id'],
        cache_dir=cache,
        **kwargs,
    )
    doc, _ = engine.apply(doc, {'operations': planned['operations']})
    return doc, planned


def mark_of(doc):
    return next(m for _, m in excerpts.excerpt_marks(doc))


def test_an_excerpt_is_a_quotation_paragraph_with_a_back_pointer_to_the_source(library):
    doc, source, aria = document(library)
    before = copy.deepcopy(doc)
    doc, planned = add_excerpt(doc, library, source, aria)
    block = next(b for b in doc['blocks'] if b['id'] == planned['excerpt']['block_id'])
    assert block['text'] == aria['text'].strip() == 'Aria is the captain.' and block['style'] == 'quotation'
    assert [b['id'] for b in doc['blocks']].index(block['id']) == 2  # inserted after the body paragraph
    mark = mark_of(doc)
    assert mark['value'] == source['id'] and (mark['start'], mark['end']) == (0, len(block['text']))
    assert mark['locator']['chunk'] == aria['chunk_id'] and mark['quote'] == block['text']
    anchor = mark['anchor']
    assert anchor['state'] == 'anchored' and anchor['quote'] == 'Aria is the captain.'
    assert anchor['content_hash'] == source['library']['content_hash']
    assert planned['excerpt']['source'] == source['id']
    assert before['blocks'][1] == doc['blocks'][1]  # nothing else changed
    model.validate_document(doc)


def test_an_excerpt_can_name_a_phrase_and_it_is_checked_against_the_source(library):
    doc, source, aria = document(library)
    _root, _registry, _definition, cache = library
    planned = excerpts.add_operations(doc, source=source['id'], text='is the captain', cache_dir=cache)
    assert planned['operations'][0]['text'] == 'is the captain' and planned['operations'][1]['anchor'][
        'position'
    ] == {
        'start': 5,
        'end': 19,
    }
    with pytest.raises(ByaxError, match='not found in the source'):
        excerpts.add_operations(doc, source=source['id'], text='the captain is Dara', cache_dir=cache)
    with pytest.raises(ByaxError, match='not in that chunk'):
        excerpts.add_operations(
            doc, source=source['id'], chunk_id=aria['chunk_id'], text='navigator', cache_dir=cache
        )
    with pytest.raises(ByaxError):
        excerpts.add_operations(doc, source=source['id'], cache_dir=cache)
    with pytest.raises(ByaxError, match='outside the chunk'):
        excerpts.add_operations(
            doc, source=source['id'], chunk_id=aria['chunk_id'], start=3, end=999, cache_dir=cache
        )
    manual = {'id': 'manual', 'title': 'No library', 'excerpt': 'x', 'verification': 'manual'}
    doc['references']['manual'] = manual
    with pytest.raises(ByaxError, match='attached library'):
        excerpts.add_operations(doc, source='manual', text='x', cache_dir=cache)


def test_where_used_lists_citations_and_excerpts_per_source_with_their_section(library):
    doc, source, aria = document(library)
    doc, _ = add_excerpt(doc, library, source, aria)
    body = doc['blocks'][1]
    doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {
                    'type': 'cite',
                    'target': body['id'],
                    'start': 0,
                    'end': 4,
                    'source': source['id'],
                    'kind': 'paraphrase',
                    'locator': {'chunk': aria['chunk_id']},
                },
                {
                    'type': 'add_reference',
                    'id': 'unused',
                    'values': {'id': 'unused', 'title': 'Unused', 'verification': 'manual'},
                },
            ]
        },
    )
    report = excerpts.where_used(doc)
    assert report['total'] == 1 and report['unused'] == ['unused']
    row = report['sources'][0]
    assert row['source'] == source['id'] and row['counts'] == {'citations': 1, 'excerpts': 1}
    assert row['citations'][0]['heading'] == 'Intro' and row['citations'][0]['text'] == 'Body'
    assert row['excerpts'][0]['heading'] == 'Intro' and row['excerpts'][0]['state'] == 'anchored'
    assert excerpts.where_used(doc, 'unused')['sources'][0]['counts'] == {'citations': 0, 'excerpts': 0}
    assert [r['source'] for r in excerpts.where_used(doc, include_unused=True)['sources']] == [
        'unused',
        source['id'],
    ] or (len(excerpts.where_used(doc, include_unused=True)['sources']) == 2)
    assert excerpts.where_used(doc, 'nobody')['sources'] == []


def test_deleting_a_source_leaves_its_excerpts_unanchored_and_never_erases_them(library):
    doc, source, aria = document(library)
    doc, planned = add_excerpt(doc, library, source, aria)
    block_id = planned['excerpt']['block_id']
    text = next(b for b in doc['blocks'] if b['id'] == block_id)['text']
    doc, result = engine.apply(doc, {'operations': [{'type': 'remove_reference', 'id': source['id']}]})
    assert block_id in result['changed_ids']
    kept = next(b for b in doc['blocks'] if b['id'] == block_id)
    assert kept['text'] == text  # the text survives
    mark = mark_of(doc)
    assert mark['value'] == source['id'] and mark['anchor']['state'] == 'unanchored'
    assert mark['anchor']['quote'] == text and mark['unanchored_reason'] == 'source_removed'
    model.validate_document(doc)
    used = excerpts.where_used(doc)
    assert used['removed_sources_with_excerpts'] == [source['id']]
    assert (
        used['sources'][0]['in_references'] is False
        and used['sources'][0]['excerpts'][0]['state'] == 'source_removed'
    )
    report = excerpts.check(doc)
    assert report['excerpts'][0]['state'] == 'source_removed' and report['summary']['source_removed'] == 1
    assert report['operations'] == []  # already unanchored: nothing to propose
    # Putting the source back lets the excerpt find its place again.
    again = copy.deepcopy(source)
    doc, _ = engine.apply(
        doc, {'operations': [{'type': 'add_reference', 'id': source['id'], 'values': again}]}
    )
    recheck = excerpts.check(doc)
    assert recheck['excerpts'][0]['state'] == 'current'
    fixed, _ = engine.apply(doc, {'operations': recheck['operations']})
    assert mark_of(fixed)['anchor']['state'] == 'anchored' and 'unanchored_reason' not in mark_of(fixed)


def test_check_reports_current_edited_changed_and_lost_excerpts(library):
    root, _registry, definition, cache = library
    doc, source, aria = document(library)
    doc, planned = add_excerpt(doc, library, source, aria)
    assert excerpts.check(doc)['excerpts'][0]['state'] == 'current'
    assert excerpts.check(doc)['operations'] == [] and excerpts.check(doc)['review'] == 'not_required'
    # You edited your copy.
    edited, _ = engine.apply(
        doc,
        {
            'operations': [
                {
                    'type': 'replace_text',
                    'target': planned['excerpt']['block_id'],
                    'match': 'captain',
                    'replacement': 'skipper',
                }
            ]
        },
    )
    assert excerpts.check(edited)['excerpts'][0]['state'] == 'edited'
    # The source changed around the excerpt: found again, nothing to rewrite.
    (root / 'notes.txt').write_text('Preface.\n\n' + NOTES, encoding='utf-8')
    LibraryIndex(definition, cache).refresh()
    moved = excerpts.check(doc)
    assert moved['excerpts'][0]['state'] == 'source_changed' and moved['review'] == 'required'
    assert [op['type'] for op in moved['operations']] == ['reanchor_excerpt']
    assert [op['type'] for op in moved['source_operations']] == ['add_reference']
    fixed, _ = engine.apply(doc, {'operations': moved['source_operations']})
    fixed, _ = engine.apply(fixed, {'operations': moved['operations']})
    assert (
        mark_of(fixed)['anchor']['state'] == 'anchored'
        and mark_of(fixed)['anchor']['content_hash'] != mark_of(doc)['anchor']['content_hash']
    )
    assert excerpts.check(fixed)['excerpts'][0]['state'] == 'current'
    # The source's wording changed slightly: a refresh is proposed, never applied.
    (root / 'notes.txt').write_text(
        NOTES.replace('Aria is the captain.', 'Aria is the captain!'), encoding='utf-8'
    )
    LibraryIndex(definition, cache).refresh()
    changed = excerpts.check(doc)
    item = changed['excerpts'][0]
    assert item['state'] == 'source_changed' and 'different wording' in item['message']
    types = [op['type'] for op in changed['operations']]
    assert types == ['replace_text', 'reanchor_excerpt']
    refreshed, _ = engine.apply(doc, {'operations': changed['source_operations']})
    refreshed, _ = engine.apply(refreshed, {'operations': changed['operations']})
    assert (
        next(b for b in refreshed['blocks'] if b['id'] == planned['excerpt']['block_id'])['text']
        == item['source_text']
    )
    assert mark_of(refreshed)['anchor']['state'] == 'anchored'
    # The passage was removed from the source: unanchored, the text stays.
    (root / 'notes.txt').write_text('Cora keeps the lighthouse lamp.', encoding='utf-8')
    LibraryIndex(definition, cache).refresh()
    lost = excerpts.check(doc)
    assert lost['excerpts'][0]['state'] == 'unanchored'
    flagged, _ = engine.apply(doc, {'operations': lost['operations']})
    assert mark_of(flagged)['anchor']['state'] == 'unanchored'
    assert (
        next(b for b in flagged['blocks'] if b['id'] == planned['excerpt']['block_id'])['text']
        == 'Aria is the captain.'
    )


def test_an_excerpt_whose_source_has_no_library_is_unchecked_not_flagged(library):
    doc, _source, _aria = document(library)
    doc['references']['manual'] = {
        'id': 'manual',
        'title': 'Letter',
        'excerpt': 'x',
        'verification': 'manual',
    }
    block = doc['blocks'][1]
    doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {'type': 'excerpt', 'target': block['id'], 'start': 0, 'end': 4, 'source': 'manual'}
            ]
        },
    )
    report = excerpts.check(doc)
    assert report['excerpts'][0]['state'] == 'unchecked' and report['operations'] == []


def test_excerpt_marks_are_validated_and_survive_edits_around_them(library):
    doc, source, aria = document(library)
    doc, planned = add_excerpt(doc, library, source, aria)
    block_id = planned['excerpt']['block_id']
    with pytest.raises(ByaxError):
        engine.apply(
            doc,
            {'operations': [{'type': 'excerpt', 'target': block_id, 'start': 0, 'end': 3, 'source': 'nope'}]},
        )
    with pytest.raises(ByaxError):
        engine.apply(
            doc,
            {
                'operations': [
                    {
                        'type': 'excerpt',
                        'target': block_id,
                        'start': 0,
                        'end': 3,
                        'source': source['id'],
                        'anchor': {'quote': 'x', 'state': 'floating'},
                    }
                ]
            },
        )
    with pytest.raises(ByaxError):
        engine.apply(
            doc,
            {
                'operations': [
                    {
                        'type': 'reanchor_excerpt',
                        'target': block_id,
                        'start': 0,
                        'end': 3,
                        'source': source['id'],
                        'anchor': {'quote': 'x'},
                    }
                ]
            },
        )
    broken = copy.deepcopy(doc)
    next(b for b in broken['blocks'] if b['id'] == block_id)['marks'][0]['value'] = 7
    with pytest.raises(ByaxError):
        model.validate_document(broken)
    typed, _ = engine.apply(
        doc, {'operations': [{'type': 'insert_text', 'target': block_id, 'offset': 0, 'text': 'Note: '}]}
    )
    shifted = mark_of(typed)
    assert (shifted['start'], shifted['end']) == (6, 6 + len('Aria is the captain.'))


def test_a_schema_two_document_with_an_excerpt_construct_is_not_rewritten():
    doc = model.new_document(text='Plain')
    migrated, report = migrate_document(doc)
    assert migrated is doc and report is None
    old = model.new_document(text='Quoted text')
    old['schema_version'] = 2
    old['dependencies'] = {}
    old['blocks'][0]['marks'] = [{'type': 'excerpt', 'value': 'someone', 'start': 0, 'end': 6}]
    with pytest.raises(ByaxError) as caught:
        migrate_document(old)
    assert caught.value.code == 'unsupported_feature' and caught.value.details['field'] == 'marks.excerpt'
    clean = model.new_document(text='Clean')
    clean['schema_version'] = 2
    clean['dependencies'] = {}
    upgraded, report = migrate_document(clean)
    assert upgraded['schema_version'] == 3
    fields = {t['field']: t['count'] for t in report['steps'][0]['transformations']}
    assert fields['marks.excerpt'] == 0
