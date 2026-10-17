"""Zotero local API import, annotations as linked notes and ``.bib`` export.

The JSON in ``tests/fixtures/zotero`` is written by hand to the Zotero Web API v3 item format that
Zotero 7's local API serves; it was not captured from a running Zotero (none is available to CI).
"""

import json
from pathlib import Path

import httpx
import pytest

from byax import engine, model, zotero
from byax.citations import validate_source
from byax.errors import ByaxError

FIXTURES = Path(__file__).parent / 'fixtures' / 'zotero'


def load(name):
    return json.loads((FIXTURES / f'{name}.json').read_text(encoding='utf-8'))


def local_api(requests=None):
    top, attachments, annotations, collections = (
        load(n) for n in ('items-top', 'attachments', 'annotations', 'collections')
    )

    def handler(request):
        if requests is not None:
            requests.append(request)
        assert request.method == 'GET' and request.url.host == 'localhost' and request.url.port == 23119
        assert request.headers['Zotero-API-Version'] == '3' and 'ByAuthorX' in request.headers['user-agent']
        query = dict(request.url.params)
        assert query.get('format') == 'json'
        path = request.url.path
        if path == '/api/users/0/items/top':
            rows = top
        elif path == '/api/users/0/collections/COLL0001/items/top':
            rows = [i for i in top if 'COLL0001' in i['data'].get('collections', [])]
        elif path == '/api/users/0/collections':
            rows = collections
        elif path == '/api/users/0/items' and query.get('itemType') == 'attachment':
            rows = attachments
        elif path == '/api/users/0/items' and query.get('itemType') == 'annotation':
            rows = annotations
        else:
            return httpx.Response(404)
        start, limit = int(query.get('start', 0)), int(query.get('limit', 25))
        return httpx.Response(
            200, json=rows[start : start + limit], headers={'Total-Results': str(len(rows))}
        )

    return httpx.MockTransport(handler)


def client(**kwargs):
    return zotero.ZoteroClient(transport=local_api(**kwargs))


def imported(existing=None, **kwargs):
    return zotero.import_from_zotero(client(), existing, **kwargs)


def by_key(result):
    return {s['zotero']['key']: s for s in result['sources']}


def test_regular_items_become_sources_and_attachments_and_notes_are_skipped():
    result = imported()
    assert result['counts'] == {'items': 3, 'annotations': 3, 'skipped': 2, 'new': 3, 'updated': 0}
    sources = by_key(result)
    assert set(sources) == {'ABCD2345', 'KUHN1962', 'WEBP0001'}
    article = sources['ABCD2345']
    assert article['id'].startswith('source_') and article['verification'] == 'unconfirmed'
    assert article['import'] == {'origin': 'zotero', 'key': 'ABCD2345'}
    assert article['csl']['id'] == 'ABCD2345' and article['csl']['DOI'] == '10.1038/nature14539'
    assert article['csl']['author'][0] == {'family': 'LeCun', 'given': 'Yann'}
    assert article['zotero']['select_uri'] == 'zotero://select/library/items/ABCD2345'


def test_items_without_zotero_csl_are_mapped_from_their_fields():
    sources = by_key(imported())
    book = sources['KUHN1962']['csl']
    assert book['type'] == 'book' and book['title'] == 'The Structure of Scientific Revolutions'
    assert book['author'] == [{'family': 'Kuhn', 'given': 'Thomas S.'}]
    assert book['editor'] == [{'family': 'Hacking', 'given': 'Ian'}]
    assert book['publisher'] == 'University of Chicago Press' and book['publisher-place'] == 'Chicago'
    assert book['ISBN'] == '978-0-226-45812-0' and book['edition'] == '4th'
    assert book['collection-title'] == 'Phoenix Books' and book['issued']['date-parts'] == [[2012]]
    page = sources['WEBP0001']['csl']
    assert page['type'] == 'webpage' and page['container-title'] == 'Maritime Archive'
    assert (
        page['author'] == [{'literal': 'Northern Lights Board'}]
        and page['URL'] == 'https://example.org/lighthouse'
    )
    assert page['accessed']['date-parts'] == [[2026, 2, 11]]


def test_annotations_become_linked_notes_with_the_page_as_a_locator():
    sources = by_key(imported())
    notes = {n['id']: n for n in sources['ABCD2345']['notes']}
    assert list(notes) == ['zotero:ANNO0001', 'zotero:ANNO0002']  # reading order
    highlight = notes['zotero:ANNO0001']
    assert highlight['kind'] == 'highlight' and highlight['origin'] == 'zotero'
    assert highlight['text'].startswith('Deep-learning methods are representation-learning')
    assert highlight['comment'] == 'Key definition; quote this one.'
    assert highlight['locator'] == {'page': '436'} and highlight['color'] == '#ffd400'
    assert highlight['attachment'] == {
        'key': 'PDFA0001',
        'title': 'Full Text PDF',
        'content_type': 'application/pdf',
    }
    assert highlight['tags'] == ['definition']
    # No page label: the page index recorded by the reader becomes page 5.
    assert notes['zotero:ANNO0002']['locator'] == {'page': '5'} and notes['zotero:ANNO0002']['text'] == ''
    assert (
        sources['KUHN1962']['notes'][0]['locator'] == {'page': 'x'}
        and sources['KUHN1962']['notes'][0]['kind'] == 'underline'
    )
    assert 'notes' not in sources['WEBP0001']
    assert not any('not imported' in n['text'] for s in sources.values() for n in s.get('notes', []))


def test_annotations_can_be_left_out_and_a_collection_can_be_chosen():
    plain = imported(annotations=False)
    assert plain['counts']['annotations'] == 0 and not any('notes' in s for s in plain['sources'])
    transport_requests = []
    zc = client(requests=transport_requests)
    chosen = zotero.import_from_zotero(zc, collection='COLL0001')
    assert set(by_key(chosen)) == {'ABCD2345', 'WEBP0001'} and chosen['counts']['annotations'] == 2
    assert any(r.url.path == '/api/users/0/collections/COLL0001/items/top' for r in transport_requests)
    assert zc.collections() == [{'key': 'COLL0001', 'name': 'Reading list', 'parent': None}]


def test_listings_are_paged_until_zoteros_total_is_reached(monkeypatch):
    monkeypatch.setattr(zotero, 'PAGE', 2)
    requests = []
    result = zotero.import_from_zotero(client(requests=requests))
    top = [r for r in requests if r.url.path.endswith('/items/top')]
    assert [r.url.params['start'] for r in top] == ['0', '2', '4'] and result['counts']['items'] == 3
    capped = zotero.import_from_zotero(client(), limit=2)
    assert capped['counts']['items'] <= 2


def test_better_bibtex_keys_are_kept_and_other_keys_are_generated():
    result = imported()
    sources = by_key(result)
    assert sources['ABCD2345']['zotero']['citation_key'] == 'lecun2015deep'  # from extra
    assert sources['KUHN1962']['zotero']['citation_key'] == 'kuhn_structure_2012'  # the citationKey field
    assert 'citation_key' not in sources['WEBP0001']['zotero']
    exported = zotero.export_bib({s['id']: s for s in result['sources']})
    assert exported['count'] == 3 and exported['from_zotero'] == 2
    assert sorted(exported['keys'].values()) == [
        'kuhn_structure_2012',
        'lecun2015deep',
        'northernlightsboard1894',
    ]
    assert '@article{lecun2015deep,' in exported['text'] and '@book{kuhn_structure_2012,' in exported['text']
    assert (
        'isbn = {978-0-226-45812-0}' in exported['text']
        and '@misc{northernlightsboard1894,' in exported['text']
    )
    assert zotero.export_bib({s['id']: s for s in result['sources']}, only_zotero=True)['count'] == 3
    other = {
        'id': 'other',
        'csl': {'type': 'book', 'title': 'Mine', 'author': [{'family': 'Smith'}]},
        'verification': 'manual',
    }
    mixed = {**{s['id']: s for s in result['sources']}, 'other': other}
    assert zotero.export_bib(mixed, only_zotero=True)['count'] == 3 and zotero.export_bib(mixed)['count'] == 4
    assert zotero.export_bib(mixed)['keys']['other'] == 'smith'


def test_generated_keys_do_not_collide_with_kept_keys():
    clash = {
        'a': {
            'id': 'a',
            'csl': {
                'type': 'book',
                'title': 'T',
                'author': [{'family': 'Kuhn'}],
                'issued': {'date-parts': [[1962]]},
            },
        },
        'b': {
            'id': 'b',
            'csl': {
                'type': 'book',
                'title': 'T2',
                'author': [{'family': 'Kuhn'}],
                'issued': {'date-parts': [[1962]]},
            },
            'zotero': {'citation_key': 'kuhn1962'},
        },
    }
    keys = zotero.export_bib(clash)['keys']
    assert keys['b'] == 'kuhn1962' and keys['a'] == 'kuhn1962a'


def test_importing_again_updates_the_same_sources_and_keeps_the_writers_own_notes():
    first = imported()
    doc = model.new_document(text='x')
    operations = [{'type': 'add_reference', 'id': s['id'], 'values': s} for s in first['sources']]
    doc, _ = engine.apply(doc, {'operations': operations})
    article_id = by_key(first)['ABCD2345']['id']
    mine = dict(doc['references'][article_id])
    mine['notes'] = mine['notes'] + [
        {'id': 'mine', 'text': 'my own', 'comment': '', 'locator': {'page': '3'}}
    ]
    mine['verification'] = 'manual'
    doc, _ = engine.apply(doc, {'operations': [{'type': 'add_reference', 'id': article_id, 'values': mine}]})
    second = imported(doc['references'])
    assert second['counts']['new'] == 0 and second['counts']['updated'] == 3
    assert {s['id'] for s in second['sources']} == {s['id'] for s in first['sources']}
    updated = next(s for s in second['sources'] if s['id'] == article_id)
    assert [n['id'] for n in updated['notes']] == ['mine', 'zotero:ANNO0001', 'zotero:ANNO0002']
    assert updated['verification'] == 'manual'


def test_a_linked_note_can_be_cited_with_its_quotation_and_page():
    source = by_key(imported())['ABCD2345']
    doc = model.new_document(text='Representation learning matters.')
    doc, _ = engine.apply(
        doc, {'operations': [{'type': 'add_reference', 'id': source['id'], 'values': source}]}
    )
    fields = zotero.citation_for_note(source, 'zotero:ANNO0001')
    assert fields['locator'] == {'page': '436'} and fields['kind'] == 'quotation'
    block = doc['blocks'][0]
    doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {
                    'type': 'cite',
                    'target': block['id'],
                    'start': 0,
                    'end': 8,
                    'source': source['id'],
                    **fields,
                }
            ]
        },
    )
    mark = doc['blocks'][0]['marks'][0]
    assert mark['locator'] == {'page': '436'} and mark['quote'].startswith('Deep-learning methods')
    with pytest.raises(ByaxError, match='no highlighted text'):
        zotero.citation_for_note(source, 'zotero:ANNO0002')
    with pytest.raises(ByaxError):
        zotero.citation_for_note(source, 'zotero:missing')


def test_source_notes_are_validated():
    good = by_key(imported())['ABCD2345']
    assert validate_source(good, good['id'])['notes']
    for notes in ('text', [{'text': 'no id'}], [{'id': 'a', 'locator': 'p3'}], [{'id': 'a'}, {'id': 'a'}]):
        with pytest.raises(ByaxError):
            validate_source({**good, 'notes': notes}, good['id'])
    with pytest.raises(ByaxError):
        validate_source({**good, 'zotero': 'yes'}, good['id'])


# --- reaching Zotero ------------------------------------------------------------------------------------------


def test_only_a_loopback_address_is_accepted():
    for base in (
        'http://example.com:23119',
        'http://192.168.1.4:23119',
        'https://localhost:23119',
        'ftp://localhost',
    ):
        with pytest.raises(ByaxError):
            zotero.ZoteroClient(base)
    assert zotero.ZoteroClient('http://127.0.0.1:23119') and zotero.ZoteroClient('http://localhost:23119')


def test_a_missing_or_locked_zotero_is_explained():
    def refuse(request):
        raise httpx.ConnectError('refused', request=request)

    with pytest.raises(ByaxError, match='Zotero is not running') as down:
        zotero.import_from_zotero(zotero.ZoteroClient(transport=httpx.MockTransport(refuse)))
    assert down.value.code == 'provider_failure' and 'Allow other applications' in down.value.message
    with pytest.raises(ByaxError, match='Allow other applications'):
        zotero.import_from_zotero(
            zotero.ZoteroClient(transport=httpx.MockTransport(lambda r: httpx.Response(403)))
        )
    with pytest.raises(ByaxError, match='unreadable'):
        zotero.import_from_zotero(
            zotero.ZoteroClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text='<html>')))
        )
    with pytest.raises(ByaxError, match='answered 500'):
        zotero.import_from_zotero(
            zotero.ZoteroClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
        )


def test_the_client_has_no_way_to_write_to_zotero():
    methods = {name for name in dir(zotero.ZoteroClient) if not name.startswith('_')}
    assert methods == {'get', 'pages', 'collections'}
    seen = []
    zotero.import_from_zotero(client(requests=seen))
    assert {r.method for r in seen} == {'GET'}


def test_a_collection_key_cannot_reach_other_paths():
    for bad in ('../..', 'A/B', ''):
        with pytest.raises(ByaxError, match='key'):
            zotero.collect(client(), collection=bad)
