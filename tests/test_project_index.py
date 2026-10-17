"""Project index: membership, incremental block-level indexing, find, saved searches and links."""

import copy
import json
import shutil
import threading
from urllib.request import Request, urlopen

import pytest

from byax import engine, project_index
from byax.cli import execute
from byax.errors import ByaxError
from byax.mcp import handle
from byax.model import new_document
from byax.project_index import ProjectIndex, document_chunks, links, members, run_saved, saved_searches
from byax.ranking import Hit
from byax.service import make_server
from byax.storage import load_document, save_document

TOKEN = 'project-index-token-with-32-characters'


def book(title, lines, headings=()):
    doc = new_document(title, '\n'.join(lines))
    for index in headings:
        doc['blocks'][index].update(type='heading', style='heading1', attrs={'level': 1})
    if headings:
        doc, _ = engine.apply(doc, {'operations': [{'type': 'project_init'}]})
    return doc


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(project_index, 'cache_root', lambda: tmp_path / 'cache')
    folder = tmp_path / 'novel'
    folder.mkdir()
    main = book(
        'Main',
        ['Chapter One', 'The lighthouse keeper walked to Harrow Point.', 'Chapter Two', 'Mara saw the lamp.'],
        headings=(0, 2),
    )
    main['settings']['bookmarks'] = {'harrow': {'target': main['blocks'][1]['id'], 'offset': 0}}
    notes = book(
        'Notes', ['Research', 'The lighthouse at Harrow Point was built in 1820.', 'mara is a name.'], (0,)
    )
    save_document(main, folder / 'main.byax')
    save_document(notes, folder / 'notes.byax')
    return folder, main, notes


def test_membership_skips_backups_hidden_files_and_copies_of_the_same_document(project):
    folder = project[0]
    shutil.copy(folder / 'main.byax', folder / 'main (conflicted copy 2026-10-16).byax')
    shutil.copy(folder / 'notes.byax', folder / 'notes.schema2-abcdef012345.byax')
    shutil.copy(folder / 'notes.byax', folder / '.hidden.byax')
    (folder / 'broken.byax').write_bytes(b'')
    listing = members(folder / 'main.byax')
    assert [d['relative'] for d in listing['documents']] == ['main.byax', 'notes.byax']
    reasons = {s['path']: s['reason'] for s in listing['skipped']}
    assert reasons['main (conflicted copy 2026-10-16).byax'] == 'duplicate_document'
    assert reasons['notes.schema2-abcdef012345.byax'] == 'migration_backup'
    assert reasons['.hidden.byax'] == 'hidden' and 'broken.byax' in reasons


def test_chunks_carry_stable_block_ids_and_binder_metadata(project):
    _, main, _ = project
    chunks = document_chunks(main)
    second = chunks[3]
    assert (
        second['block_id'] == main['blocks'][3]['id']
        and second['chunk_id'] == f'{main["id"]}:{second["target"]}'
    )
    assert (
        second['kind'] == 'chapter'
        and second['item_title'] == 'Chapter Two'
        and second['heading'] == 'Chapter Two'
    )
    assert second['chapter'] == second['item']


def test_find_across_documents_with_filters_pagination_and_suggestions(project):
    folder, main, _ = project
    index = ProjectIndex(folder / 'main.byax', doc=main)
    status = index.refresh(main)
    assert status['updated']['added'] == 7 and len(status['documents']) == 2 and not status['stale']
    found = index.search('lighthouse')
    assert [(r['path'], r['open']) for r in found['results']] == [('main.byax', True), ('notes.byax', False)]
    hit = found['results'][0]
    assert hit['block_id'] == main['blocks'][1]['id'] and hit['item'] and hit['kind'] == 'chapter'
    assert hit['text'][hit['start'] : hit['end']].casefold() == 'lighthouse' and hit['corpus'] == 'manuscript'
    assert Hit.from_result(hit).chunk_id == hit['chunk_id']  # fuses with library hits
    assert [r['path'] for r in index.search('lighthouse', documents=['open'])['results']] == ['main.byax']
    assert index.search('lighthouse', kind='note')['total'] == 0
    item = hit['item']
    assert index.search('lamp', item=item)['total'] == 0
    assert index.search('ighthou')['passes'] == ['porter', 'trigram']
    assert index.search('lighthuse')['suggestions']['lighthuse'][0]['term'] == 'lighthouse'
    assert index.search('lighthuse', expand=True)['total'] == 2
    page = index.search('lighthouse', limit=1)
    assert page['truncated'] and page['continuation'] == 1
    assert index.search('lighthouse', limit=1, offset=1)['results'][0]['path'] == 'notes.byax'
    assert index.search('"harrow point" -1820')['total'] == 1
    with pytest.raises(ByaxError):
        index.search('  ')


def test_refresh_is_incremental_by_block_hash_and_tracks_unsaved_edits(project):
    folder, main, notes = project
    index = ProjectIndex(folder / 'main.byax', doc=main)
    index.refresh(main)
    again = index.refresh(main)
    assert again['updated']['documents_reindexed'] == 0
    edited, _ = engine.apply(
        main,
        {
            'operations': [
                {'type': 'insert_text', 'target': main['blocks'][3]['id'], 'offset': 0, 'text': 'Then '}
            ]
        },
    )
    assert index.status()['stale'] is False  # the index still knows the old revision ...
    index.doc = edited
    assert index.status()['stale'] is True  # ... and reports it once the editor moved on
    counts = index.update_document(edited)
    assert counts == {'added': 0, 'changed': 1, 'removed': 0, 'unchanged': 3}
    assert index.search('Then')['total'] == 1
    # Siblings are reparsed only when the file changed; deleted siblings leave the index.
    notes2, _ = engine.apply(
        notes,
        {
            'operations': [
                {
                    'type': 'insert_paragraph',
                    'text': 'A new lighthouse fact.',
                    'after': notes['blocks'][-1]['id'],
                }
            ]
        },
    )
    save_document(notes2, folder / 'notes.byax')
    refreshed = index.refresh(edited)
    assert refreshed['updated']['documents_reindexed'] == 1 and refreshed['updated']['added'] == 1
    (folder / 'notes.byax').unlink()
    gone = index.refresh(edited)
    assert gone['updated']['removed'] == 4 and [d['path'] for d in gone['documents']] == ['main.byax']
    rebuilt = index.refresh(edited, rebuild=True)
    assert rebuilt['updated']['added'] == 4


def test_index_lives_in_the_cache_never_in_the_project(project, tmp_path):
    folder, main, _ = project
    with pytest.raises(ByaxError, match='outside the project'):
        ProjectIndex(folder / 'main.byax', cache_dir=folder / 'cache')
    index = ProjectIndex(folder / 'main.byax', doc=main)
    assert index.path.parent == (tmp_path / 'cache').resolve()
    assert ProjectIndex.existing(folder / 'main.byax') is not None
    other = tmp_path / 'other'
    other.mkdir()
    assert ProjectIndex.existing(other / 'x.byax') is None
    with pytest.raises(ByaxError, match='Save the document'):
        ProjectIndex(None)


def test_saved_searches_are_settings_and_results_are_live(project):
    folder, main, _ = project
    saved, _ = engine.apply(
        main,
        {
            'operations': [
                {'type': 'saved_search_set', 'name': 'Lights', 'query': 'lighthouse', 'scope': 'document'}
            ]
        },
    )
    record = saved['settings']['saved_searches']['Lights']
    assert record['query'] == 'lighthouse' and record['created'] == record['updated']
    index = ProjectIndex(folder / 'main.byax', doc=saved)
    index.refresh(saved)
    assert run_saved(index, saved, 'Lights')['total'] == 1
    edited, _ = engine.apply(
        saved,
        {
            'operations': [
                {
                    'type': 'insert_paragraph',
                    'text': 'Another lighthouse.',
                    'after': saved['blocks'][-1]['id'],
                }
            ]
        },
    )
    index.update_document(edited)
    assert run_saved(index, edited, 'Lights')['total'] == 2
    renamed, _ = engine.apply(
        edited, {'operations': [{'type': 'saved_search_set', 'name': 'Lights', 'query': 'lamp'}]}
    )
    assert renamed['settings']['saved_searches']['Lights']['created'] == record['created']
    assert saved_searches(renamed)[0]['scope'] == 'project'
    removed, _ = engine.apply(renamed, {'operations': [{'type': 'saved_search_remove', 'name': 'Lights'}]})
    assert 'saved_searches' not in removed['settings']
    with pytest.raises(ByaxError):
        engine.apply(removed, {'operations': [{'type': 'saved_search_remove', 'name': 'Lights'}]})
    with pytest.raises(ByaxError):
        engine.apply(
            main,
            {'operations': [{'type': 'set_settings', 'values': {'saved_searches': {'x': {'scope': 'all'}}}}]},
        )


def test_backlinks_and_unlinked_mentions(project):
    folder, main, _ = project
    heading = main['blocks'][2]['id']
    linked, _ = engine.apply(
        main,
        {
            'operations': [
                {
                    'type': 'insert_paragraph',
                    'text': 'See  and read Chapter Two again.',
                    'after': main['blocks'][-1]['id'],
                },
            ]
        },
    )
    paragraph = linked['blocks'][-1]['id']
    linked, _ = engine.apply(
        linked,
        {
            'operations': [
                {'type': 'insert_cross_reference', 'target': paragraph, 'offset': 4, 'ref': heading}
            ]
        },
    )
    linked, _ = engine.apply(
        linked,
        {
            'operations': [
                {
                    'type': 'codex_add',
                    'id': 'cx_mara',
                    'values': {'kind': 'character', 'name': 'Mara', 'aliases': ['M. Vey']},
                }
            ]
        },
    )
    index = ProjectIndex(folder / 'main.byax', doc=linked)
    index.refresh(linked)
    result = links(linked, target=heading, index=index)
    assert result['subject'] == {
        'type': 'heading',
        'id': heading,
        'label': 'Chapter Two',
        'terms': ['Chapter Two'],
    }
    assert [b['target'] for b in result['backlinks']['items']] == [paragraph]
    assert [u['surface'] for u in result['unlinked_mentions']['items']] == ['Chapter Two']
    bookmark = links(linked, bookmark='harrow', index=index)
    assert {u['path'] for u in bookmark['unlinked_mentions']['items']} == {'main.byax', 'notes.byax'}
    entry = links(linked, target='cx_mara', index=index)
    assert [(b['path'], b['surface']) for b in entry['backlinks']['items']] == [('main.byax', 'Mara')]
    assert [(u['path'], u['surface'], u['reason']) for u in entry['unlinked_mentions']['items']] == [
        ('notes.byax', 'mara', 'case')
    ]
    with pytest.raises(ByaxError):
        links(linked, target=linked['blocks'][1]['id'], index=index)


class FakeEmbedder:
    model_identity = 'fake-bag-of-words'
    dimension = 4
    max_input_chars = 1000

    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += len(texts)
        words = ('lighthouse', 'lamp', 'mara', 'point')
        return [[float(t.casefold().count(w)) + 0.01 for w in words] for t in texts]


def test_optional_vectors_are_built_only_with_an_explicit_provider(project):
    folder, main, _ = project
    index = ProjectIndex(folder / 'main.byax', doc=main)
    index.refresh(main)
    assert 'notice' in index.search('beacon', method='hybrid')
    provider = FakeEmbedder()
    built = index.rebuild_vectors(provider)
    assert built['embedded'] == built['total'] == 7
    calls = provider.calls
    assert index.rebuild_vectors(provider)['done'] == 0 and provider.calls == calls  # unchanged rows skipped
    hybrid = index.search('lamp', method='hybrid', provider=provider)
    assert hybrid['method'] == 'hybrid' and hybrid['results'][0]['text'] == 'Mara saw the lamp.'
    assert 'ranks' in hybrid['results'][0]


def _rest(tmp_path):
    server = make_server([tmp_path], token=TOKEN, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f'http://127.0.0.1:{server.server_port}/v1/execute'


def test_cli_mcp_and_rest_return_the_same_project_results(project, tmp_path):
    folder, _, _ = project
    path = str(folder / 'main.byax')
    arguments = {'path': path, 'query': 'lighthouse'}
    local = execute('project-search', arguments)
    assert local['total'] == 2 and local['index']['documents'] == 2
    response = handle(
        {
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'tools/call',
            'params': {'name': 'byax_project_search', 'arguments': arguments},
        }
    )['result']
    assert not response['isError'] and response['structuredContent']['results'] == local['results']
    server, thread, url = _rest(tmp_path)
    try:
        request = Request(
            url,
            data=json.dumps({'command': 'project-search', 'arguments': arguments}).encode(),
            method='POST',
            headers={'Authorization': 'Bearer ' + TOKEN},
        )
        with urlopen(request, timeout=20) as reply:
            remote = json.load(reply)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert remote['results'] == local['results']
    entity = handle(
        {
            'jsonrpc': '2.0',
            'id': 2,
            'method': 'tools/call',
            'params': {'name': 'byax_entity', 'arguments': {'path': path, 'action': 'list'}},
        }
    )['result']['structuredContent']
    assert entity['entries'] == execute('entity', {'path': path, 'action': 'list'})['entries']


def test_cli_saved_search_round_trip_and_links(project):
    folder, _, _ = project
    path = str(folder / 'main.byax')
    added = execute(
        'project-search', {'path': path, 'action': 'saved-add', 'name': 'Harrow', 'query': '"Harrow Point"'}
    )
    assert added['saved_searches'][0]['name'] == 'Harrow'
    assert (
        execute('project-search', {'path': path, 'action': 'saved-list'})['saved_searches'][0]['query']
        == '"Harrow Point"'
    )
    run = execute('project-search', {'path': path, 'action': 'saved-run', 'name': 'Harrow'})
    assert run['total'] == 2 and run['saved_search']['name'] == 'Harrow'
    doc = load_document(path)
    heading = doc['blocks'][0]['id']
    unlinked = execute('project-search', {'path': path, 'action': 'unlinked-mentions', 'target': heading})
    assert unlinked['unlinked_mentions']['total'] == 0 and unlinked['subject']['label'] == 'Chapter One'
    backlinks = execute('project-search', {'path': path, 'action': 'backlinks', 'bookmark': 'harrow'})
    assert backlinks['backlinks']['total'] == 0
    assert execute('project-search', {'path': path, 'action': 'status'})['documents']
    execute('project-search', {'path': path, 'action': 'saved-remove', 'name': 'Harrow'})
    assert 'saved_searches' not in load_document(path)['settings']
    with pytest.raises(ByaxError):
        execute('project-search', {'path': path, 'action': 'bogus'})
    with pytest.raises(ByaxError):
        execute('project-search', {'path': path, 'action': 'find'})


def test_series_documents_join_the_project(project, tmp_path):
    from byax import series

    folder, main, _ = project
    other = tmp_path / 'book-two'
    other.mkdir()
    save_document(book('Two', ['A lighthouse in book two.']), other / 'two.byax')
    series.save(tmp_path / 'saga.byaxseries', series.new_series('Saga', documents=['book-two/two.byax']))
    linked = copy.deepcopy(main)
    linked['settings']['series'] = series.reference_for(folder / 'main.byax', tmp_path / 'saga.byaxseries')
    save_document(linked, folder / 'main.byax')
    index = ProjectIndex(folder / 'main.byax', doc=load_document(folder / 'main.byax'))
    index.refresh()
    sources = {d['path']: d['source'] for d in index.status()['documents']}
    assert sources == {'main.byax': 'open', 'notes.byax': 'folder', '../book-two/two.byax': 'series'}
    assert index.search('lighthouse')['total'] == 3
