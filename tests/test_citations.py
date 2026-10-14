import copy
import json

import pytest

from byax import engine
from byax.citations import (
    export_sources,
    import_sources,
    render_bibliography,
    render_citation,
    source_from_hit,
    validate_source,
    verify_source,
)
from byax.errors import ByaxError
from byax.model import new_document

CSL = [
    {
        'id': 'smith2020',
        'type': 'book',
        'title': 'Research Notes',
        'author': [{'family': 'Smith', 'given': 'Jane'}],
        'issued': {'date-parts': [[2020]]},
    }
]


def source():
    return import_sources(json.dumps(CSL), 'csl-json')[0]


def test_cite_transaction_and_missing_source_rejection():
    doc = new_document(text='The claim is supported.')
    record = source()
    operations = [
        {'type': 'add_reference', 'id': record['id'], 'values': record},
        {
            'type': 'cite',
            'target': doc['blocks'][0]['id'],
            'start': 0,
            'end': 9,
            'source': record['id'],
            'kind': 'quotation',
            'locator': {'page': '12'},
        },
    ]
    result, _ = engine.apply(doc, {'operations': operations})
    mark = result['blocks'][0]['marks'][0]
    assert mark['value'] == record['id']
    assert mark['quote'] == 'The claim'
    assert mark['locator'] == {'page': '12'}
    assert not doc['references']
    operations[1]['source'] = 'nonexistent'
    with pytest.raises(ByaxError, match='does not exist'):
        engine.apply(doc, {'operations': operations})
    assert not doc['references']


def test_legacy_references_remain_accepted_and_structured_fields_validate():
    assert validate_source({'excerpt': 'old source'}, 'old') == {'excerpt': 'old source'}
    with pytest.raises(ByaxError):
        validate_source({'csl': {'title': 'Missing type'}})
    with pytest.raises(ByaxError):
        validate_source({'csl': {'type': 'book'}, 'verification': 'invented'})


def test_better_bibtex_reimport_updates_same_identity():
    text = '@book{smith2020, title={Research {Notes}}, author={Smith, Jane and Jones, Bob}, year={2020}}'
    original = import_sources(text, 'bibtex')[0]
    updated = import_sources(
        text.replace('Research {Notes}', 'New title'), 'better-bibtex', {original['id']: original}
    )[0]
    assert original['id'] == updated['id']
    assert updated['csl']['title'] == 'New title'
    assert updated['csl']['author'][1]['family'] == 'Jones'
    assert updated['verification'] == 'unconfirmed'
    exported = export_sources({updated['id']: updated}, 'bibtex')
    assert import_sources(exported, 'bibtex')[0]['csl']['title'] == 'New title'


def test_ris_import_and_csl_roundtrip_do_not_export_private_data():
    record = import_sources(
        'TY  - JOUR\nID  - item1\nTI  - A title\nAU  - Smith, Jane\nPY  - 2024\n'
        'SP  - 5\nEP  - 8\nDO  - 10.1234/example\nER  -\n',
        'ris',
    )[0]
    assert record['csl']['page'] == '5-8'
    record['library'] = {'library_id': 'private', 'path': '/secret/location'}
    record['excerpt'] = 'private passage'
    exported = export_sources({record['id']: record})
    assert 'private' not in exported
    assert '/secret' not in exported
    assert json.loads(exported)[0]['DOI'] == '10.1234/example'


@pytest.mark.parametrize('style', ['apa', 'mla', 'chicago'])
def test_actual_csl_styles_render_without_mutating_data(style):
    record = source()
    before = copy.deepcopy(record)
    citation = render_citation(record, style, {'page': '12'})
    bibliography = render_bibliography({record['id']: record}, style)
    assert 'Smith' in citation and '12' in citation
    assert 'Research Notes' in bibliography[0] and '2020' in bibliography[0]
    assert record == before


def test_verification_uses_current_hash_and_quote_not_stored_status():
    record = source_from_hit(
        {
            'library_id': 'lib1',
            'document_id': 'doc1',
            'content_hash': 'hash1',
            'path': 'notes.txt',
            'text': 'Original evidence',
        }
    )
    assert record['verification'] == 'unverifiable'
    current = {'content_hash': 'hash1', 'text': 'Original evidence', 'stale': False}
    read = lambda *args: current
    assert verify_source(record, read, quote='Original evidence') == 'verified'
    assert verify_source(record, read, quote='Invented quote') == 'unconfirmed'
    current['content_hash'] = 'hash2'
    assert verify_source(record, read) == 'stale'

    def unavailable(*args):
        raise ByaxError('not_found', 'Unavailable')

    assert verify_source(record, unavailable) == 'unverifiable'
    assert record['excerpt'] == 'Original evidence'


def test_invalid_imports_and_grapheme_selection_rejected():
    for text, fmt in [('{}', 'csl-json'), ('not bibtex', 'bibtex'), ('TY  - BOOK\nTI  - incomplete', 'ris')]:
        with pytest.raises(ByaxError):
            import_sources(text, fmt)
    doc = new_document(text='e\u0301x')
    record = source()
    doc['references'][record['id']] = record
    with pytest.raises(ByaxError):
        engine.apply(
            doc,
            {
                'operations': [
                    {
                        'type': 'cite',
                        'target': doc['blocks'][0]['id'],
                        'start': 1,
                        'end': 2,
                        'source': record['id'],
                    }
                ]
            },
        )


def cited_document():
    doc = new_document(text='The claim is supported.')
    record = source()
    return engine.apply(
        doc,
        {
            'operations': [
                {'type': 'add_reference', 'id': record['id'], 'values': record},
                {
                    'type': 'cite',
                    'target': doc['blocks'][0]['id'],
                    'start': 0,
                    'end': 9,
                    'source': record['id'],
                    'locator': {'page': '12'},
                },
            ]
        },
    )[0]


@pytest.mark.parametrize('mode', ['in-text', 'footnotes', 'endnotes', 'numbered'])
def test_citation_output_projection_is_derived_and_idempotent(mode):
    from byax.citation_render import citation_view

    doc = cited_document()
    doc['settings']['citations'] = {'style': 'apa', 'presentation': mode, 'bibliography': True}
    before = copy.deepcopy(doc)
    view = citation_view(doc)
    assert doc == before
    assert citation_view(view) is view
    assert 'Research Notes' in '\n'.join(b.get('text', '') for b in view['blocks'])
    if mode == 'footnotes':
        note = next(b for b in view['blocks'] if b['type'] == 'footnote')
        assert note['attrs']['reference'] == doc['blocks'][0]['id']
        assert note['attrs']['offset'] == 9
    else:
        assert view['blocks'][0]['text'] != doc['blocks'][0]['text']


def test_citation_export_contains_rendered_labels_and_keeps_master(tmp_path):
    from byax.exchange import export_document

    doc = cited_document()
    before = copy.deepcopy(doc)
    destination = tmp_path / 'cited.txt'
    export_document(doc, destination)
    output = destination.read_text()
    assert '(Smith, 2020, p. 12)' in output
    assert 'Research Notes' in output
    assert doc == before


def test_source_proposal_cannot_claim_verified_and_cite_depends_on_source():
    doc = new_document(text='The claim')
    record = source()
    operations = [
        {'type': 'add_reference', 'id': record['id'], 'values': record},
        {'type': 'cite', 'target': doc['blocks'][0]['id'], 'start': 0, 'end': 9, 'source': record['id']},
    ]
    proposal_op = {'type': 'propose', 'values': {'operations': operations}}
    proposed, _ = engine.apply(doc, {'operations': [proposal_op]})
    proposal = proposed['review']['proposals'][0]
    assert proposal['edits'][1]['depends_on'] == ['edit_1']
    accepted, _ = engine.apply(
        proposed, {'operations': [{'type': 'accept_proposal', 'target': proposal['id']}]}
    )
    assert accepted['blocks'][0]['marks'][0]['value'] == record['id']
    for status in ('verified', 'manual'):
        record['verification'] = status
        with pytest.raises(ByaxError, match='cannot assert'):
            engine.apply(doc, {'operations': [proposal_op]})


def test_real_library_hit_locator_can_be_cited(tmp_path, monkeypatch):
    from byax.libraries import LibraryIndex, LibraryRegistry

    root = tmp_path / 'research'
    root.mkdir()
    (root / 'notes.md').write_text('# Evidence\n\nThe claim is supported by research.')
    registry = LibraryRegistry(tmp_path / 'config')
    library = registry.create('Research', [str(root)])
    index = LibraryIndex(library, tmp_path / 'cache')
    index.refresh()
    hit = index.search('claim')['results'][0]
    record = source_from_hit(hit)
    doc = new_document(text='The claim is supported.')
    result, _ = engine.apply(
        doc,
        {
            'operations': [
                {'type': 'add_reference', 'id': record['id'], 'values': record},
                {
                    'type': 'cite',
                    'target': doc['blocks'][0]['id'],
                    'start': 0,
                    'end': 9,
                    'source': record['id'],
                    'locator': {**hit['locator'], 'chunk': hit['chunk_id']},
                },
            ]
        },
    )
    assert result['blocks'][0]['marks'][0]['locator']['heading_path'] == ['Evidence']


def test_citation_footnotes_reach_physical_layout_and_excluded_content_does_not():
    from byax.layout import resolve_pages, resolved_documents

    doc = cited_document()
    doc['settings']['citations'] = {'style': 'chicago', 'presentation': 'footnotes'}
    resolved = resolve_pages(doc)
    texts = '\n'.join(part.toPlainText() for part in resolved_documents(resolved))
    assert 'Smith' in texts and 'Research Notes' in texts
    assert any(
        fragment['kind'] == 'footnotes' for page in resolved['pages'] for fragment in page['fragments']
    )
    doc['blocks'][0]['attrs']['excluded'] = True
    hidden = resolve_pages(doc)
    texts = '\n'.join(part.toPlainText() for part in resolved_documents(hidden))
    assert 'Smith' not in texts
    assert 'Research Notes' not in texts


@pytest.mark.parametrize('format', ['txt', 'docx', 'md', 'html'])
def test_footnote_export_downgrade_is_a_located_loss(tmp_path, format):
    from byax.exchange import export_document

    doc = cited_document()
    doc['settings']['citations'] = {'style': 'chicago', 'presentation': 'footnotes'}
    for preview in (True, False):
        report = export_document(doc, tmp_path / ('citations.' + format), preview=preview)
        losses = [loss for loss in report['losses'] if loss['code'] == 'citation_footnotes_as_endnotes']
        assert len(losses) == 1
        assert losses[0]['target'] == doc['blocks'][0]['id']
        assert 'numbered endnotes' in losses[0]['message']
        assert report['review'] == 'required'
    doc['settings']['citations']['presentation'] = 'endnotes'
    report = export_document(doc, None, format=format, preview=True)
    assert not any(loss['code'] == 'citation_footnotes_as_endnotes' for loss in report['losses'])


def test_projection_generated_ids_do_not_collide_with_authored_ids():
    from byax.citation_render import citation_view

    doc = cited_document()
    doc['settings']['citations'] = {'style': 'apa', 'presentation': 'endnotes'}
    first = citation_view(doc)
    generated_ids = [block['id'] for block in first['blocks'][1:]]
    for ident in generated_ids:
        doc['blocks'].append(
            {
                'id': ident,
                'type': 'paragraph',
                'text': 'Authored content',
                'style': 'body',
                'attrs': {},
                'marks': [],
            }
        )
    before = copy.deepcopy(doc)
    view = citation_view(doc)
    ids = [block['id'] for block in view['blocks']]
    assert len(ids) == len(set(ids))
    assert all(ident in ids for ident in generated_ids)
    assert view == citation_view(doc)
    assert doc == before
