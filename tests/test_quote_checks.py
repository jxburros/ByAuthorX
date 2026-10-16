"""Quote verification callers: normalized pass, advisory near match, and cite kind inference."""

import pytest

from byax import cli, engine
from byax.checks import run_checks
from byax.citations import STATUSES, source_from_hit, verify_source, verify_source_detail
from byax.libraries import LibraryIndex, LibraryRegistry
from byax.model import new_document
from byax.storage import load_document, save_document

SOFT = chr(0xAD)
# Extracted PDF text: a line-break hyphen, a ligature, a soft hyphen and a curly apostrophe.
PDF_TEXT = (
    f'The committee’s report found that the ﬁnancial ben-\ne{SOFT}fits of the programme were negligible.'
)
# One passage per page, as the PDF parser indexes it: a ligature, soft hyphen, curly apostrophe, extra space.
INDEXED = f'The committee\u2019s report found that the \ufb01nancial be{SOFT}nefits of the programme  were negligible.'
CLEAN = 'The committee’s report found that the financial benefits of the programme were negligible.'


def source(text=PDF_TEXT):
    record = source_from_hit(
        {'library_id': 'lib1', 'document_id': 'doc1', 'content_hash': 'hash1', 'path': 'r.pdf', 'text': text}
    )
    return record, lambda *args: {'content_hash': 'hash1', 'text': text, 'stale': False}


def test_verify_source_reports_normalized_pass_and_raw_pass():
    record, read = source()
    exact = verify_source_detail(record, read, quote='of the programme')
    assert exact['status'] == 'verified' and exact['quote']['raw_pass'] and exact['quote']['normalized_pass']
    normalized = verify_source_detail(record, read, quote='the financial benefits of the programme')
    assert normalized['status'] == 'verified'
    assert normalized['quote']['match'] == 'normalized'
    assert not normalized['quote']['raw_pass'] and normalized['quote']['normalized_pass']
    assert verify_source(record, read, quote="committee's report found") == 'verified'


def test_verify_source_near_match_is_advisory_and_never_verified():
    record, read = source(CLEAN)
    near = verify_source_detail(record, read, quote='the financial benefits of the programme were negligable')
    assert near['status'] == 'quote_near_match' and near['status'] in STATUSES
    assert near['quote']['match'] == 'near' and near['quote']['score'] >= 90
    assert not near['quote']['normalized_pass']
    assert near['quote']['differences'] and "'a'" in near['quote']['summary']
    assert verify_source(record, read, quote='an invented sentence that is not there') == 'unconfirmed'
    assert verify_source(record, read) == 'verified'


# --- document checks ---------------------------------------------------------------------------


@pytest.fixture
def cited(tmp_path, monkeypatch):
    root = tmp_path / 'sources'
    root.mkdir()
    (root / 'report.txt').write_text(INDEXED, encoding='utf-8')
    registry = LibraryRegistry(tmp_path / 'config')
    definition = registry.create('Research', [root])
    index = LibraryIndex(definition, tmp_path / 'cache')
    index.refresh()
    monkeypatch.setattr('byax.libraries.LibraryRegistry', lambda: registry)
    monkeypatch.setattr('byax.libraries.LibraryIndex', lambda library, cache_dir=None: index)
    hit = index.search('committee')['results'][0]
    record = source_from_hit(hit)
    doc = new_document(text='x')
    doc['references'][record['id']] = record
    doc['settings']['libraries'] = [
        {'library_id': definition['library_id'], 'name': 'Research', 'role': 'exclusive'}
    ]

    def quote(text):
        doc['blocks'][0]['text'] = text
        doc['blocks'][0]['marks'] = [
            {
                'type': 'citation',
                'value': record['id'],
                'start': 0,
                'end': len(text),
                'kind': 'quotation',
                'locator': {'chunk': hit['chunk_id']},
            }
        ]
        return run_checks(doc, 'factual')

    return quote


def library_findings(report):
    return [f for f in report['findings'] if f['rule'] == 'factual.library_citation']


def test_library_check_passes_a_quote_that_matches_only_after_normalization(cited):
    report = cited('“the financial benefits of the programme were negligible”')
    assert not library_findings(report)
    record = next(
        c['evidence']
        for c in report['coverage']
        if c.get('rule') == 'factual.library_citation' and c.get('evidence')
    )
    assert record['verification'] == 'verified'
    match = record['evidence'][0]['quote_match']
    assert match == {
        'match': 'normalized',
        'raw_pass': False,
        'normalized_pass': True,
        'span': match['span'],
    }


def test_library_check_near_match_is_an_advisory_finding_not_a_confirmation(cited):
    report = cited('“the financial benefits of the programme were negligable”')
    (finding,) = library_findings(report)
    assert finding['severity'] == 'advisory'
    assert finding['measured']['verification'] == 'quote_near_match'
    assert finding['measured']['score'] >= 90 and finding['measured']['differences']
    assert 'not confirmed' in finding['explanation'] and "'a'" in finding['explanation']
    assert finding['outcome'] == 'not_established'
    assert not any(
        c.get('evidence', {}).get('verification') == 'verified'
        for c in report['coverage']
        if c.get('rule') == 'factual.library_citation'
    )


def test_library_check_still_flags_an_invented_quote(cited):
    (finding,) = library_findings(cited('“a completely different sentence about sailing ships”'))
    assert finding['severity'] == 'warning' and finding['measured']['verification'] == 'unconfirmed'


def evidence_doc(quotation):
    doc = new_document('Q', f'She wrote {quotation} today.')
    target = doc['blocks'][0]['id']
    doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {
                    'type': 'add_reference',
                    'id': 'diary',
                    'values': {
                        'title': 'Diary',
                        'text': 'The ﬁrst test of the committee’s plan failed badly.',
                        'target': target,
                    },
                },
                {
                    'type': 'format_text',
                    'target': target,
                    'start': 10,
                    'end': 10 + len(quotation),
                    'marks': {'citation': 'diary'},
                },
            ]
        },
    )
    return doc


def test_stored_excerpt_quotation_normalizes_and_near_is_advisory():
    report = run_checks(evidence_doc('"The first test of the committee\'s plan failed badly"'), ['factual'])
    assert report['evidence'][0]['outcome'] == 'supported'
    assert report['evidence'][0]['evidence'][0]['quote_match']['match'] == 'normalized'
    assert not [f for f in report['findings'] if f['rule'] == 'factual.quotation']

    report = run_checks(evidence_doc('"The first test of the committee\'s plan failed badlyy"'), ['factual'])
    assert not report['evidence']
    (finding,) = [f for f in report['findings'] if f['rule'] == 'factual.quotation']
    assert finding['severity'] == 'advisory' and finding['measured']['verification'] == 'quote_near_match'
    assert finding['outcome'] == 'not_established' and finding['evidence'] == []

    report = run_checks(evidence_doc('"nothing like the diary text at all"'), ['factual'])
    (finding,) = [f for f in report['findings'] if f['rule'] == 'factual.quotation']
    assert finding['severity'] == 'warning' and 'verification' not in finding['measured']


# --- cite kind inference through the CLI ---------------------------------------------------------


@pytest.fixture
def library_doc(tmp_path, monkeypatch):
    import platformdirs

    from byax import libraries

    monkeypatch.setattr(libraries, '_default_dir', lambda: tmp_path / 'config')
    monkeypatch.setattr(platformdirs, 'user_cache_path', lambda *a, **k: tmp_path / 'cache')
    monkeypatch.setattr(platformdirs, 'user_state_path', lambda *a, **k: tmp_path / 'state')
    root = tmp_path / 'research'
    root.mkdir()
    (root / 'facts.md').write_text('# Mars\n\nMars has two moons: Phobos and Deimos.', encoding='utf-8')
    path = tmp_path / 'draft.byax'
    text = 'Mars has two moons: Phobos and Deimos. The red planet has a pair of satellites. “Quoted.”'
    save_document(new_document(text=text), path)
    cli.execute(
        'library',
        {'path': str(path), 'action': 'create', 'input': {'name': 'Research', 'roots': [str(root)]}},
    )
    identity = load_document(path)['settings']['libraries'][0]['library_id']
    refresh = cli.execute('library', {'path': str(path), 'action': 'refresh', 'library_id': identity})
    assert refresh['job']['state'] == 'completed', refresh
    hit = cli.execute('library-search', {'path': str(path), 'library_id': identity, 'query': 'moons'})[
        'results'
    ][0]
    record = source_from_hit(hit)
    result = cli.execute(
        'apply',
        {
            'path': str(path),
            'input': {'operations': [{'type': 'add_reference', 'id': record['id'], 'values': record}]},
        },
    )
    assert result['execution'] == 'completed', result
    return str(path), record['id'], hit


def cite(path, source_id, hit, start, end, **extra):
    doc = load_document(path)
    result = cli.execute(
        'cite',
        {
            'path': path,
            'target': doc['blocks'][0]['id'],
            'start': start,
            'end': end,
            'source': source_id,
            'input': {'locator': {'chunk': hit['chunk_id']}},
            **extra,
        },
    )
    assert result['execution'] == 'completed', result
    marks = load_document(path)['blocks'][0]['marks']
    return result, marks[-1]


def test_cli_cite_infers_quotation_from_the_source_and_paraphrase_otherwise(library_doc):
    path, source_id, hit = library_doc
    text = load_document(path)['blocks'][0]['text']
    result, mark = cite(path, source_id, hit, 0, text.index(' The red'))
    assert mark['kind'] == 'quotation'
    assert result['citation_kind']['inferred'] and result['citation_kind']['kind'] == 'quotation'
    start = text.index('The red')
    result, mark = cite(path, source_id, hit, start, text.index(' “Quoted'))
    assert mark['kind'] == 'paraphrase' and result['citation_kind']['kind'] == 'paraphrase'
    start = text.index('“Quoted.”')
    result, mark = cite(path, source_id, hit, start, start + len('“Quoted.”'))
    assert mark['kind'] == 'quotation' and 'quotation marks' in result['citation_kind']['reason']


def test_cli_cite_keeps_an_explicit_kind(library_doc):
    path, source_id, hit = library_doc
    result, mark = cite(path, source_id, hit, 0, 10, kind='inference')
    assert mark['kind'] == 'inference' and 'citation_kind' not in result
