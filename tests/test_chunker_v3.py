"""Structure-aware chunker v3: merging, headings, parent pointers, offsets and stale-index handling."""

import json
import sqlite3

import pytest

from byax.context_assembly import estimate_tokens
from byax.embeddings import embedded_text
from byax.errors import ByaxError
from byax.libraries import CHUNKING, SCHEMA, LibraryIndex, LibraryRegistry, chunk_passages
from byax.quote_match import locate


def passage(text, ordinal, *path, **extra):
    return {'text': text, 'locator': {'ordinal': ordinal, 'heading_path': list(path), **extra}}


def chunks(passages, **kwargs):
    return list(chunk_passages(passages, 'doc_x', **kwargs))


def section_text(passages):
    return '\n\n'.join(p['text'] for p in passages)


def test_defaults_are_recorded_and_conservative():
    assert CHUNKING == {'version': 3, 'target_tokens': 400, 'overlap': 160}
    assert SCHEMA == 3


def test_short_paragraphs_merge_up_to_the_target_and_stored_text_is_unchanged():
    paragraphs = [passage(f'Paragraph number {i} says something short.', i) for i in range(30)]
    result = chunks(paragraphs, target_tokens=100)
    assert 1 < len(result) < 30
    assert all(estimate_tokens(c['text']) <= 100 for c in result)
    # Every paragraph appears exactly once, in order, joined by blank lines: nothing is rewritten.
    assert '\n\n'.join(c['text'] for c in result) == section_text(paragraphs)
    assert [c['locator']['ordinal'] for c in result] == sorted(c['locator']['ordinal'] for c in result)
    assert result[0]['locator']['ordinal_end'] > result[0]['locator']['ordinal']
    assert [c['ordinal'] for c in result] == list(range(len(result)))
    assert all(len(c['fingerprint']) == 64 and c['chunk_id'].startswith('chunk_') for c in result)


def test_a_quote_spanning_two_paragraphs_verifies_against_the_merged_chunk():
    paragraphs = [passage('The lamp went out.', 0), passage('Nobody spoke for a while.', 1)]
    (chunk,) = chunks(paragraphs)
    assert locate('The lamp went out. Nobody spoke for a while.', chunk['text'])['match'] != 'none'


def test_a_chunk_never_crosses_a_heading():
    path_a, path_b = ('Part', 'One'), ('Part', 'Two')
    paragraphs = [
        passage('One', 0, *path_a),
        passage('Alpha text.', 1, *path_a),
        passage('Two', 2, *path_b),
        passage('Beta text.', 3, *path_b),
    ]
    result = chunks(paragraphs)
    assert [c['text'] for c in result] == ['One\n\nAlpha text.', 'Two\n\nBeta text.']
    assert [c['heading'] for c in result] == ['Part > One', 'Part > Two']
    assert len({c['locator']['parent'] for c in result}) == 2


def test_a_heading_that_only_introduces_a_subsection_is_not_a_chunk_of_its_own():
    paragraphs = [
        passage('Part', 0, 'Part'),
        passage('Sub', 1, 'Part', 'Sub'),
        passage('Body of the subsection.', 2, 'Part', 'Sub'),
        passage('Empty leaf', 3, 'Leaf'),
    ]
    result = chunks(paragraphs)
    assert [c['text'] for c in result] == ['Sub\n\nBody of the subsection.', 'Empty leaf']
    assert result[0]['heading'] == 'Part > Sub'  # the introducing heading stays searchable through the path


def test_pdf_pages_are_never_merged():
    pages = [{'text': f'Page {n} text.', 'locator': {'page': n}} for n in (1, 2, 3)]
    result = chunks(pages)
    assert [c['locator']['page'] for c in result] == [1, 2, 3]
    assert all(c['locator']['start'] == 0 for c in result)


def test_an_overlong_paragraph_splits_at_sentences_with_overlap_and_offsets_map_back():
    sentences = [f'Sentence {i} describes the harbour in some detail.' for i in range(60)]
    long = ' '.join(sentences)
    head = passage('Short opener.', 0, 'H')
    body = passage(long, 1, 'H')
    tail = passage('Closing line.', 2, 'H')
    result = chunks([head, body, tail], target_tokens=100)
    section = section_text([head, body, tail])
    assert len(result) > 3
    for chunk in result:
        assert estimate_tokens(chunk['text']) <= 100
        # start/end are offsets within the section text, so the stored text can always be located.
        assert section[chunk['locator']['start'] : chunk['locator']['end']] == chunk['text']
    pieces = [c for c in result if c['text'].startswith('Sentence') or ' Sentence' in c['text']]
    assert all(p['text'].rstrip().endswith('.') for p in pieces[:-1]), 'splits land on sentence ends'
    # Consecutive pieces overlap, so a quote crossing a split still appears whole in one of them.
    quote = 'Sentence 20 describes the harbour in some detail. Sentence 21 describes'
    assert any(quote in c['text'] for c in result)
    assert result[0]['text'] == 'Short opener.' and result[-1]['text'] == 'Closing line.'


def test_wide_scripts_are_counted_per_character_so_cjk_text_still_splits():
    text = '潮が満ちる。' * 400
    result = chunks([passage(text, 0)], target_tokens=200)
    assert len(result) > 5 and all(estimate_tokens(c['text']) <= 200 for c in result)


def test_target_validation():
    with pytest.raises(ByaxError):
        chunks([passage('x', 0)], target_tokens=1)
    with pytest.raises(ByaxError):
        chunks([passage('x', 0)], target_tokens=100000)


# The index -----------------------------------------------------------------------------------------


@pytest.fixture
def library(tmp_path):
    root = tmp_path / 'sources'
    root.mkdir()
    registry = LibraryRegistry(tmp_path / 'config')
    return root, registry, registry.create('Notes', [str(root)]), tmp_path / 'cache'


def test_headings_paths_parent_pointers_and_section_expansion(library):
    root, _registry, definition, cache = library
    filler = ' '.join(['The harbour was quiet that evening.'] * 40)
    (root / 'log.md').write_text(
        f'# Ship\n\n## Captain\n\nAria commands the vessel.\n\n{filler}\n\n## Navigator\n\nBren plots the course.',
        encoding='utf-8',
    )
    index = LibraryIndex(definition, cache_dir=cache)
    index.refresh()
    captain = index.search('Aria')['results'][0]
    assert captain['heading'] == 'Ship > Captain' and captain['locator']['heading_path'] == [
        'Ship',
        'Captain',
    ]
    assert captain['text'].startswith('Captain')  # the heading path is not prepended to the stored text
    assert 'Ship > Captain' not in captain['text']
    section = index.read_section(captain['document_id'], captain['chunk_id'])
    assert section['section'] == captain['locator']['parent']
    assert section['anchor_chunk_id'] == captain['chunk_id']
    assert len(section['results']) > 1
    assert all(r['locator']['parent'] == captain['locator']['parent'] for r in section['results'])
    assert all('Bren' not in r['text'] for r in section['results'])
    navigator = index.search('Bren')['results'][0]
    assert navigator['locator']['parent'] != captain['locator']['parent']
    with pytest.raises(ByaxError):
        index.read_section(captain['document_id'], 'chunk_missing')
    # Searching by a heading word finds the section even where the body never says it.
    assert any(h['chunk_id'] == navigator['chunk_id'] for h in index.search('heading:navigator')['results'])


def test_embedded_text_adds_the_heading_path_once(library):
    assert embedded_text('Ship > Captain', 'Captain\n\nAria commands.') == 'Ship\n\nCaptain\n\nAria commands.'
    assert embedded_text('Ship > Captain', 'Later chunk of the section.') == (
        'Ship > Captain\n\nLater chunk of the section.'
    )
    assert embedded_text('', 'Plain text.') == 'Plain text.'
    assert embedded_text('Ship', 'Plain text.', 'A note.') == 'Ship\n\nA note.\n\nPlain text.'
    root, _registry, definition, cache = library
    (root / 'a.md').write_text(
        '# Ship\n\nAria commands the vessel.\n\n## Crew\n\nBren plots.', encoding='utf-8'
    )
    index = LibraryIndex(definition, cache_dir=cache)
    index.refresh()
    from byax.embeddings import _passages

    texts = {p['embedded_text'] for p in _passages(index)}
    assert 'Ship\n\nAria commands the vessel.' in texts
    assert any(t.endswith('Crew\n\nBren plots.') for t in texts)
    # The stored text a quote is verified against never changed.
    with index._db() as db:
        stored = {r['text'] for r in db.execute('SELECT text FROM chunks')}
    assert 'Ship\n\nAria commands the vessel.' in stored


def test_chunk_size_is_configurable_and_a_change_marks_the_index_stale(library):
    root, registry, definition, cache = library
    filler = ' '.join(['The harbour was quiet that evening.'] * 30)
    (root / 'a.md').write_text('\n\n'.join(f'{filler} {i}' for i in range(6)), encoding='utf-8')
    default = LibraryIndex(definition, cache_dir=cache)
    default.refresh()
    baseline = default.status()['chunks']
    assert default.status()['chunking']['target_tokens'] == 400
    small = registry.configure_chunking(definition['library_id'], 100)
    index = LibraryIndex(small, cache_dir=cache)
    assert index.status()['stale'] and index.status()['files'][0]['status'] == 'stale'
    index.refresh()
    assert index.status()['chunks'] > baseline and not index.status()['stale']
    assert index.status()['chunking'] == {**CHUNKING, 'target_tokens': 100}
    for bad in (5, 'x', 100000):
        with pytest.raises(ByaxError):
            registry.configure_chunking(definition['library_id'], bad)
    cleared = registry.configure_chunking(definition['library_id'], None)
    assert LibraryIndex(cleared, cache_dir=cache).status()['chunking']['target_tokens'] == 400
    with pytest.raises(ByaxError):
        LibraryIndex({**small, 'chunk_tokens': 3}, cache_dir=cache)
    assert LibraryIndex(small, cache_dir=cache, chunk_tokens=200).chunking['target_tokens'] == 200


def test_an_index_made_by_the_previous_chunker_is_stale_but_searchable_until_refreshed(library):
    root, _registry, definition, cache = library
    (root / 'a.txt').write_text('Aria is the captain.\n\nBren is the navigator.', encoding='utf-8')
    index = LibraryIndex(definition, cache_dir=cache)
    index.refresh()
    with index._db() as db:
        db.execute(
            "INSERT OR REPLACE INTO meta VALUES ('chunking', ?)",
            (json.dumps({'version': 2, 'chars': 1800, 'overlap': 160}),),
        )
    reopened = LibraryIndex(definition, cache_dir=cache)
    status = reopened.status()
    assert status['stale'] and status['files'][0]['status'] == 'stale'
    assert reopened.search('Aria')['results'][0]['stale']
    refreshed = reopened.refresh()
    assert not refreshed['stale'] and not reopened.search('Aria')['results'][0]['stale']


def test_schema_two_index_upgrades_in_place_and_keeps_old_text_searchable(library):
    root, _registry, definition, cache = library
    (root / 'a.txt').write_text('Aria is the captain.', encoding='utf-8')
    index = LibraryIndex(definition, cache_dir=cache)
    index.refresh()
    db = sqlite3.connect(index.path)
    db.executescript("""
        DROP TABLE search; DROP TABLE vocab; DROP TABLE contexts;
        CREATE VIRTUAL TABLE search USING fts5(chunk_id UNINDEXED, heading, text,
            tokenize="porter unicode61 remove_diacritics 2", prefix='2 3');
        INSERT INTO search(rowid, chunk_id, heading, text) SELECT rowid, chunk_id, heading, text FROM chunks;
        UPDATE meta SET value='2' WHERE key='schema';
    """)
    db.commit()
    db.close()
    upgraded = LibraryIndex(definition, cache_dir=cache)
    assert upgraded.status()['schema'] == 3 and upgraded.status()['stale']
    old = upgraded.search('Aria')['results']
    assert old and old[0]['stale']
    upgraded.refresh()
    assert not upgraded.search('Aria')['results'][0]['stale']
    assert upgraded.status()['contexts'] == {'notes': 0, 'chunks': 1}


def test_identical_files_in_two_libraries_still_deduplicate_with_parent_pointers(library):
    from byax.model import new_document
    from byax.retrieval import retrieve_context

    root, registry, first, cache = library
    (root / 'same.txt').write_text('The copper moon rose.', encoding='utf-8')
    other_root = root.parent / 'other'
    other_root.mkdir()
    (other_root / 'same.txt').write_text('The copper moon rose.', encoding='utf-8')
    second = registry.create('Copy', [str(other_root)])
    for definition in (first, second):
        LibraryIndex(definition, cache_dir=cache).refresh()
    doc = new_document()
    doc['settings']['libraries'] = [
        {'library_id': d['library_id'], 'name': d['name'], 'role': 'supplementary'} for d in (first, second)
    ]
    result = retrieve_context(doc, 'copper', registry=registry, cache_dir=cache)
    assert len(result['included']) == 1 and any(o.get('duplicate_of') for o in result['omitted'])
