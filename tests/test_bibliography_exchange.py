"""RIS import and export, BibTeX fields, citation keys and additional CSL styles."""

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from byax import csl_styles, model
from byax.citations import (
    citation_keys,
    export_sources,
    import_sources,
    render_bibliography,
    render_citation,
)
from byax.errors import ByaxError

FIXTURES = Path(__file__).parent / 'fixtures'
IEEE = (FIXTURES / 'csl' / 'ieee.csl').read_bytes()
BOOK = """TY  - BOOK
ID  - b1
TI  - A History
 of Things
A1  - Smith, Jane
A1  - World Health Organization,
A2  - Doe, John, Jr.
PY  - 2019///Spring
DA  - 2019/03/05/
PB  - Oxford University Press
CY  - Oxford
SN  - 978-0-19-852663-6
AB  - An abstract
 spanning lines.
KW  - history
KW  - things
N1  - Remember
ET  - 2nd
LA  - en
XX  - unknown
ER  -
TY  - JOUR
TI  - Paper
JO  - Journal of X
SN  - 1234-5678
SP  - 5
EP  - 9
VL  - 3
IS  - 2
DO  - 10.1/abc
ER  -
"""


@pytest.fixture(autouse=True)
def styles_home(tmp_path, monkeypatch):
    monkeypatch.setenv('BYAX_BIBLIOGRAPHY_DIR', str(tmp_path / 'bib'))


def csl_of(text, fmt='ris'):
    return {s['csl'].get('id', s['csl']['title']): s['csl'] for s in import_sources(text, fmt)}


def test_ris_import_reads_isbn_abstract_editors_and_the_common_tags():
    book = csl_of(BOOK)['b1']
    assert book['type'] == 'book' and book['title'] == 'A History of Things'
    assert book['ISBN'] == '978-0-19-852663-6' and 'ISSN' not in book
    assert book['abstract'] == 'An abstract spanning lines.'
    assert book['editor'] == [{'family': 'Doe', 'given': 'John', 'suffix': 'Jr.'}]
    assert book['author'][1] == {'literal': 'World Health Organization'}
    assert book['issued']['date-parts'] == [[2019, 3, 5]]
    assert book['publisher'] == 'Oxford University Press' and book['publisher-place'] == 'Oxford'
    assert book['edition'] == '2nd' and book['language'] == 'en'
    assert book['keyword'] == 'history; things' and book['note'] == 'Remember'
    # A tag without a CSL field is kept, not dropped.
    assert book['custom'] == {'ris': {'XX': ['unknown']}}


def test_ris_import_tells_an_issn_from_an_isbn_and_reads_the_page_range_and_issue():
    article = next(c for c in csl_of(BOOK).values() if c['type'] == 'article-journal')
    assert article['ISSN'] == '1234-5678' and 'ISBN' not in article
    assert article['page'] == '5-9' and article['issue'] == '2' and article['volume'] == '3'
    assert article['container-title'] == 'Journal of X' and article['DOI'] == '10.1/abc'


def test_ris_export_is_complete_and_reimports_to_the_same_metadata():
    sources = {s['id']: s for s in import_sources(BOOK, 'ris')}
    text = export_sources(sources, 'ris')
    assert text.count('ER  - ') == 2 and 'TY  - BOOK' in text and 'TY  - JOUR' in text
    for line in (
        'SN  - 978-0-19-852663-6',
        'AB  - An abstract spanning lines.',
        'A2  - Doe, John, Jr.',
        'XX  - unknown',
    ):
        assert line in text
    again = import_sources(text, 'ris')

    def bare(items):
        return sorted(
            json.dumps({k: v for k, v in s['csl'].items() if k != 'id'}, sort_keys=True) for s in items
        )

    assert bare(again) == bare(sources.values())


def test_ris_export_never_writes_library_paths_or_excerpts():
    source = import_sources(BOOK, 'ris')[0]
    source['library'] = {'library_id': 'x', 'path': '/secret/place'}
    source['excerpt'] = 'private passage'
    text = export_sources({source['id']: source}, 'ris')
    assert '/secret' not in text and 'private passage' not in text


def test_bibtex_export_carries_isbn_abstract_editor_and_literal_authors():
    sources = {s['id']: s for s in import_sources(BOOK, 'ris')}
    bib = export_sources(sources, 'bibtex')
    assert 'isbn = {978-0-19-852663-6}' in bib and 'abstract = {An abstract spanning lines.}' in bib
    assert '{World Health Organization}' in bib and 'Doe, Jr., John' in bib
    back = import_sources(bib, 'bibtex')
    by_title = {s['csl']['title']: s['csl'] for s in back}
    assert by_title['A History of Things']['author'][1] == {'literal': 'World Health Organization'}
    assert by_title['A History of Things']['ISBN'] == '978-0-19-852663-6'


def test_citation_keys_prefer_a_stored_key_and_disambiguate_deterministically():
    def source(ident, family, year, **extra):
        return {
            'id': ident,
            'csl': {
                'type': 'book',
                'title': 'T',
                'author': [{'family': family}],
                'issued': {'date-parts': [[year]]},
            },
            **extra,
        }

    references = {
        'one': source('one', 'Müller', 2020),
        'two': source('two', 'Muller', 2020),
        'three': source('three', 'Kept', 1999, zotero={'citation_key': 'bbt:kept1999'}),
        'four': source('four', 'Other', 2001),
    }
    keys = citation_keys(references)
    assert keys == {'one': 'muller2020a', 'two': 'muller2020b', 'three': 'bbt:kept1999', 'four': 'other2001'}
    assert citation_keys(references) == keys
    bib = export_sources(references, 'bibtex', keys=keys)
    assert '@book{bbt:kept1999' in bib


# --- CSL styles ----------------------------------------------------------------------------


def repository(files=None, calls=None):
    files = files if files is not None else {'ieee': IEEE}

    def handler(request):
        if calls is not None:
            calls.append(str(request.url))
        name = request.url.path.rsplit('/', 1)[-1].removesuffix('.csl')
        assert request.url.host == 'raw.githubusercontent.com'
        assert request.url.path.startswith('/citation-style-language/styles/master/')
        assert 'ByAuthorX' in request.headers['user-agent']
        if name in files:
            return httpx.Response(200, content=files[name])
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def reference():
    return {
        'id': 's1',
        'csl': {
            'id': 's1',
            'type': 'article-journal',
            'title': 'Research Notes',
            'container-title': 'Journal of Notes',
            'author': [{'family': 'Smith', 'given': 'Jane'}],
            'issued': {'date-parts': [[2020]]},
            'volume': '4',
            'page': '1-9',
        },
    }


def test_preview_shows_url_size_and_hash_and_installs_nothing():
    shown = csl_styles.preview('ieee', transport=repository())
    assert shown['url'] == 'https://raw.githubusercontent.com/citation-style-language/styles/master/ieee.csl'
    assert shown['size'] == len(IEEE) and shown['sha256'] == hashlib.sha256(IEEE).hexdigest()
    assert (
        shown['license'] == 'http://creativecommons.org/licenses/by-sa/3.0/' and shown['installed'] is False
    )
    assert [r['name'] for r in csl_styles.list_styles()['styles'] if not r['built_in']] == []


def test_a_downloaded_style_is_verified_stored_and_renders():
    shown = csl_styles.preview('ieee', transport=repository())
    installed = csl_styles.add_from_repository('ieee', expect_sha256=shown['sha256'], transport=repository())
    assert installed['installed'] and installed['verified_against_preview'] and installed['size'] == len(IEEE)
    rows = {r['name']: r for r in csl_styles.list_styles()['styles']}
    assert rows['ieee']['built_in'] is False and rows['ieee']['citation_format'] == 'numeric'
    assert csl_styles.style_format('ieee') == 'numeric'
    record = reference()
    assert 'Smith' in render_bibliography({'s1': record}, 'ieee')[0]
    assert render_citation(record, 'ieee')  # a numeric style still renders a citation label
    # The built-in styles are unaffected.
    assert 'Smith' in render_citation(record, 'apa')


def test_an_installed_style_drives_the_document_view_and_the_health_rules():
    from byax import bibliography, engine
    from byax.citation_render import citation_view

    csl_styles.add_from_repository('ieee', transport=repository())
    doc = model.new_document(text='Sleep matters.')
    doc['references']['s1'] = reference()
    doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {'type': 'cite', 'target': doc['blocks'][0]['id'], 'start': 0, 'end': 5, 'source': 's1'},
                {
                    'type': 'set_settings',
                    'values': {'citations': {'style': 'ieee', 'presentation': 'in-text'}},
                },
            ]
        },
    )
    shown = citation_view(doc)
    assert any('Smith' in block['text'] for block in shown['blocks'])
    assert bibliography.citation_format(doc['settings']['citations']) == 'numeric'
    doc['settings']['citations']['style'] = 'not-installed'
    with pytest.raises(ByaxError, match='not installed'):
        citation_view(doc)


def test_a_changed_download_is_refused_when_it_differs_from_the_preview():
    with pytest.raises(ByaxError) as caught:
        csl_styles.add_from_repository('ieee', expect_sha256='0' * 64, transport=repository())
    assert caught.value.code == 'conflict'
    assert not any(r['name'] == 'ieee' for r in csl_styles.list_styles()['styles'])


def test_an_installed_style_that_was_edited_is_refused_not_trusted():
    csl_styles.add_from_repository('ieee', transport=repository())
    path = Path(csl_styles.style_path('ieee'))
    path.write_bytes(IEEE + b'\n<!-- edited -->')
    with pytest.raises(ByaxError) as caught:
        render_citation(reference(), 'ieee')
    assert caught.value.code == 'conflict'


def test_names_unknown_styles_and_dependent_styles_are_refused():
    for bad in ('../etc/passwd', 'IEEE', 'a b', 'apa', ''):
        with pytest.raises(ByaxError):
            csl_styles.preview(bad, transport=repository())
    with pytest.raises(ByaxError) as missing:
        csl_styles.preview('no-such-style', transport=repository())
    assert missing.value.code == 'missing_target'
    dependent = (
        b'<?xml version="1.0"?><style xmlns="http://purl.org/net/xbiblio/csl" class="in-text" version="1.0">'
        b'<info><title>Child</title><id>x</id><link href="http://www.zotero.org/styles/ieee" rel="independent-parent"/>'
        b'</info></style>'
    )
    with pytest.raises(ByaxError, match='dependent'):
        csl_styles.add_from_repository('child', transport=repository({'child': dependent}))
    assert csl_styles.preview('child', transport=repository({'child': dependent}))['usable'] is False
    with pytest.raises(ByaxError, match='not installed'):
        render_citation(reference(), 'ieee')


def test_a_local_csl_file_can_be_imported_without_the_network(tmp_path):
    file = tmp_path / 'My Style.csl'
    file.write_bytes(IEEE)
    result = csl_styles.add_from_file(file)
    assert result['name'] == 'my-style' and result['origin'] == {'file': 'My Style.csl'}
    assert 'Smith' in render_bibliography({'s1': reference()}, 'my-style')[0]
    (tmp_path / 'broken.csl').write_text('<style>')
    with pytest.raises(ByaxError):
        csl_styles.add_from_file(tmp_path / 'broken.csl')
    (tmp_path / 'not-csl.csl').write_text('<?xml version="1.0"?><other/>')
    with pytest.raises(ByaxError, match='not a CSL style'):
        csl_styles.add_from_file(tmp_path / 'not-csl.csl')
    csl_styles.remove('my-style')
    assert not any(r['name'] == 'my-style' for r in csl_styles.list_styles()['styles'])
