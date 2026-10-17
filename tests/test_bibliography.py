"""Bibliography health against recorded HTTP fixtures; no test here reaches the network."""

import copy
import json
from datetime import date

import httpx
import pytest
from bibliography_fixtures import ReplayTransport, request_key

from byax import bibliography, citation_lookup
from byax.citation_lookup import (
    Lookup,
    RetractionIndex,
    doi_problem,
    import_retraction_watch,
    normalize_doi,
    private_address,
)
from byax.errors import ByaxError
from byax.model import new_document

TODAY = date(2026, 10, 16)
NATURE = '10.1038/nature14539'
LANCET = '10.1016/S0140-6736(97)11096-0'
ZENODO = '10.5281/zenodo.3233118'
MISSING = '10.1234/this-doi-does-not-exist-byax'


@pytest.fixture(autouse=True)
def bibliography_home(tmp_path, monkeypatch):
    monkeypatch.setenv('BYAX_BIBLIOGRAPHY_DIR', str(tmp_path / 'bib'))


def source(ident, title, families, year, doi=None, url=None, **extra):
    csl = {
        'id': ident,
        'type': 'article-journal',
        'title': title,
        'author': [{'family': f, 'given': 'X'} for f in families],
        'issued': {'date-parts': [[year]]},
    }
    if doi:
        csl['DOI'] = doi
    if url:
        csl['URL'] = url
    return {'id': ident, 'csl': csl, 'verification': 'unconfirmed', **extra}


def reference_doc(*sources):
    doc = new_document(text='Intro paragraph.')
    doc['references'] = {s['id']: s for s in sources}
    return doc


def replay(**extra):
    return ReplayTransport(extra=extra or None)


def online(transport=None, **kwargs):
    kwargs.setdefault('authorized', True)
    kwargs.setdefault('sleep', lambda _: None)
    return Lookup(transport=transport or replay(), **kwargs)


def real_sources():
    return [
        source('nature', 'Deep learning', ['LeCun', 'Bengio', 'Hinton'], 2015, NATURE),
        source(
            'lancet',
            'Ileal-lymphoid-nodular hyperplasia, non-specific colitis, and pervasive developmental disorder in children',
            ['Wakefield', 'Murch'],
            1998,
            LANCET,
        ),
        source('zenodo', 'nipy/nibabel: 2.4.1', ['Brett', 'Markiewicz'], 2019, ZENODO),
        source('ghost', 'A Study Nobody Has Seen', ['Nobody'], 2020, MISSING),
    ]


def by_source(report):
    return {entry['id']: entry for entry in report['sources']}


def status_of(report, ident, check):
    return [f for f in report['findings'] if f['source'] == ident and f['check'] == check]


# --- DOI syntax ------------------------------------------------------------------------------


def test_doi_normalization_and_syntax():
    assert normalize_doi(' https://doi.org/10.1038/NATURE14539 ') == NATURE
    assert normalize_doi('doi: 10.1/abc') == '10.1/abc' and normalize_doi('') is None
    assert doi_problem(NATURE) is None
    assert doi_problem('10.1234/with(parens)') is None
    assert doi_problem('not a doi')['reason'].startswith('A DOI starts with 10.')
    assert doi_problem('10.12/short')['suggestion'] is None
    assert doi_problem('10.1038/nature14539.')['suggestion'] == '10.1038/nature14539'
    assert doi_problem('10.1038/nature 14539')['suggestion'] == '10.1038/nature14539'


def test_invalid_dois_are_reported_without_any_lookup():
    doc = reference_doc(
        source('bad', 'T', ['A'], 2020, 'doi:10.12/x'), source('also', 'T', ['A'], 2021, '10.1038/x.')
    )
    report = bibliography.health(doc, today=TODAY)
    assert report['network']['mode'] == 'none' and report['network']['requests'] == 0
    messages = {f['source']: f for f in report['findings'] if f['check'] == 'doi'}
    assert messages['bad']['status'] == 'invalid' and messages['also']['detail']['suggestion'] == '10.1038/x'
    assert report['summary']['by_severity']['warning'] >= 2


# --- registry lookups against recorded answers --------------------------------------------------


def test_resolution_metadata_and_retraction_against_recorded_registry_answers():
    transport = replay()
    lookup = online(transport)
    report = bibliography.health(reference_doc(*real_sources()), lookup=lookup, today=TODAY)
    entries = by_source(report)
    assert entries['nature']['found_in'] == 'crossref' and entries['nature']['metadata']['status'] == 'match'
    assert entries['zenodo']['found_in'] == 'datacite'
    assert entries['zenodo']['metadata']['fields']['title']['status'] == 'match'
    assert (
        entries['zenodo']['metadata']['fields']['authors']['status'] == 'minor'
    )  # many more authors are recorded
    assert entries['lancet']['checks']['retractions'] == 'retracted'
    retraction = status_of(report, 'lancet', 'retractions')[0]
    assert retraction['severity'] == 'error' and retraction['status'] == 'retracted'
    notice = next(n for n in retraction['detail']['notices'] if n['type'] == 'retraction')
    assert notice['doi'] == '10.1016/s0140-6736(10)60175-4' and notice['date'] == '2010-02-06'
    # Crossref, DataCite and OpenAlex all said 404: unverified, never "fake".
    ghost = status_of(report, 'ghost', 'doi')[0]
    assert ghost['status'] == 'unverified' and ghost['severity'] == 'info'
    assert 'Unverified, please check' in ghost['message'] and 'fake' not in ghost['message'].lower()
    assert entries['ghost']['checks']['doi'] == 'unverified'
    assert report['summary']['retracted'] == 1 and report['summary']['unverified'] == 1
    # The order of the lookups: Crossref first, DataCite only for what Crossref does not know.
    assert request_key('GET', f'https://api.datacite.org/dois/{NATURE}') not in transport.requests
    assert request_key('GET', f'https://api.datacite.org/dois/{ZENODO}') in transport.requests
    assert report['network'] == {'mode': 'online', 'requests': lookup.requests_made, 'cache_hits': 0}


def test_metadata_mismatches_are_found_in_title_authors_and_year():
    wrong = source('nature', 'Shallow learning', ['Nobody'], 2001, NATURE)
    close = source('close', 'Deep Learning.', ['LeCun', 'Bengio', 'Hinton'], 2014, NATURE)
    report = bibliography.health(
        reference_doc(wrong, close), lookup=online(), checks=['doi', 'metadata'], today=TODAY
    )
    fields = by_source(report)['nature']['metadata']['fields']
    assert by_source(report)['nature']['metadata']['status'] == 'mismatch'
    assert fields['title']['status'] == 'mismatch' and fields['authors']['status'] == 'mismatch'
    assert fields['year']['status'] == 'mismatch'
    near = by_source(report)['close']['metadata']
    assert near['fields']['title']['status'] == 'match' and near['fields']['year']['status'] == 'minor'
    assert near['status'] == 'minor'
    finding = status_of(report, 'nature', 'metadata')[0]
    assert finding['severity'] == 'warning' and 'Deep learning' in finding['message']


def test_a_subtitle_and_a_truncated_author_list_are_not_errors():
    short = source('nature', 'Deep', ['LeCun'], 2015, NATURE)
    comparison = bibliography.compare_metadata(
        bibliography.source_fields(short),
        {
            'title': 'Deep: learning for everyone',
            'authors': [{'family': 'LeCun'}, {'family': 'Bengio'}],
            'year': 2015,
        },
    )
    assert comparison['fields']['title']['status'] == 'match'
    assert comparison['fields']['authors']['status'] == 'minor' and comparison['status'] == 'minor'


def test_openalex_is_the_fallback_and_its_retraction_flag_is_used():
    work = {
        'id': 'https://openalex.org/W1',
        'doi': 'https://doi.org/10.9999/openalex-only',
        'title': 'Only OpenAlex Knows',
        'publication_year': 2020,
        'is_retracted': True,
        'authorships': [{'author': {'display_name': 'Ana Silva'}}],
    }
    answers = {
        'GET https://api.crossref.org/works/10.9999/openalex-only': {
            'status': 404,
            'text': 'Resource not found.',
        },
        'GET https://api.datacite.org/dois/10.9999/openalex-only': {'status': 404, 'json': {'errors': []}},
        'GET https://api.openalex.org/works/https://doi.org/10.9999/openalex-only': {
            'status': 200,
            'json': work,
        },
    }
    doc = reference_doc(source('oa', 'Only OpenAlex Knows', ['Silva'], 2020, '10.9999/openalex-only'))
    report = bibliography.health(doc, lookup=online(replay(**answers)), today=TODAY)
    entry = by_source(report)['oa']
    assert entry['found_in'] == 'openalex' and entry['metadata']['status'] == 'match'
    assert entry['checks']['retractions'] == 'retracted'


def test_a_registry_that_is_down_is_an_error_not_an_unverified_verdict():
    def failing(request):
        raise httpx.ConnectError('no route', request=request)

    doc = reference_doc(source('nature', 'Deep learning', ['LeCun'], 2015, NATURE))
    report = bibliography.health(doc, lookup=online(httpx.MockTransport(failing)), today=TODAY)
    assert by_source(report)['nature']['checks']['doi'] == 'error'
    assert status_of(report, 'nature', 'doi')[0]['status'] == 'error'


# --- consent, etiquette, cache -------------------------------------------------------------------


def test_nothing_is_requested_without_citation_lookup_consent(tmp_path):
    transport = replay()
    lookup = Lookup(transport=transport, config_dir=tmp_path / 'cfg')
    with pytest.raises(ByaxError) as caught:
        bibliography.health(reference_doc(*real_sources()), lookup=lookup, today=TODAY)
    assert caught.value.code == 'authorization_required' and caught.value.details['services'] == ['crossref']
    assert transport.requests == []
    lookup.grant(['crossref', 'datacite', 'openalex'])
    from byax.retrieval import ConsentRegistry

    assert ConsentRegistry(tmp_path / 'cfg').allows('bibliography:crossref', 'citation_lookup')
    report = bibliography.health(reference_doc(*real_sources()), lookup=lookup, today=TODAY)
    assert report['summary']['retracted'] == 1 and transport.requests
    lookup.grant(['datacite'], allowed=False)
    assert not lookup.allowed('datacite')


def test_requests_identify_the_application_and_send_a_contact_address_only_when_configured(tmp_path):
    seen = []

    def spy(request):
        seen.append((str(request.url), request.headers['user-agent']))
        return httpx.Response(404, text='nope')

    doc = reference_doc(source('x', 'T', ['A'], 2020, '10.1234/abc'))
    bibliography.health(doc, lookup=online(httpx.MockTransport(spy), cache=tmp_path / 'c1'), today=TODAY)
    assert seen and all(
        'github.com/jxburros/ByAuthorX' in agent and 'mailto' not in url for url, agent in seen
    )
    seen.clear()
    citation_lookup.save_settings({'mailto': 'me@example.org'})
    bibliography.health(doc, lookup=online(httpx.MockTransport(spy), cache=tmp_path / 'c2'), today=TODAY)
    urls = {url.split('/')[2]: url for url, _ in seen}
    assert (
        'mailto=me%40example.org' in urls['api.crossref.org']
        and 'mailto=me%40example.org' in urls['api.openalex.org']
    )
    assert 'mailto' not in urls['api.datacite.org']
    with pytest.raises(ByaxError):
        citation_lookup.save_settings({'mailto': 'not an address'})
    assert citation_lookup.save_settings({'mailto': ''}) == {}


def test_requests_to_one_host_are_spaced_and_a_429_stops_that_host():
    clock = {'now': 0.0}
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        clock['now'] += seconds

    def limited(request):
        return httpx.Response(429, headers={'retry-after': '120'})

    sources = [source(f's{i}', 'T', ['A'], 2020, f'10.1234/a{i}') for i in range(4)]
    transport = httpx.MockTransport(limited)
    lookup = Lookup(transport=transport, authorized=True, sleep=sleep, clock=lambda: clock['now'])
    report = bibliography.health(reference_doc(*sources), lookup=lookup, checks=['doi'], today=TODAY)
    # The first 429 blocks Crossref for the run; later DOIs are not sent there, and the other
    # registries are not asked in its place (their silence would prove nothing).
    assert lookup.requests_made == 1 and all(e['checks']['doi'] == 'error' for e in report['sources'])
    clock['now'] = 0.0
    slept.clear()
    ok = Lookup(
        transport=httpx.MockTransport(lambda r: httpx.Response(404, text='x')),
        authorized=True,
        sleep=sleep,
        clock=lambda: clock['now'],
    )
    ok.get_json('crossref', 'https://api.crossref.org/works/10.1/a')
    ok.get_json('crossref', 'https://api.crossref.org/works/10.1/b')
    assert slept and slept[0] == pytest.approx(0.2)


def test_answers_are_cached_and_offline_mode_uses_only_the_cache(tmp_path):
    cache = tmp_path / 'cache'
    doc = reference_doc(*real_sources())
    first_transport = replay()
    first = online(first_transport, cache=cache)
    first_report = bibliography.health(doc, lookup=first, today=TODAY)
    assert first.requests_made > 0
    second_transport = replay()
    second = online(second_transport, cache=cache)
    second_report = bibliography.health(doc, lookup=second, today=TODAY)
    assert second.requests_made == 0 and second_transport.requests == [] and second.cache_hits > 0
    assert second_report['findings'] == first_report['findings']
    offline = Lookup(offline=True, cache=cache)
    offline_report = bibliography.health(doc, lookup=offline, today=TODAY)
    assert offline_report['network']['mode'] == 'cache only' and offline_report['summary']['retracted'] == 1
    empty = Lookup(offline=True, cache=tmp_path / 'empty')
    cold = bibliography.health(doc, lookup=empty, today=TODAY)
    assert {e['checks'].get('doi') for e in cold['sources']} == {'not_checked'} and cold['summary'][
        'not_checked'
    ] >= 4
    assert not any(f['check'] == 'doi' for f in cold['findings'])
    assert Lookup(cache=cache).clear_cache() > 0


# --- Retraction Watch ------------------------------------------------------------------------------

RETRACTION_WATCH = (
    'Record ID,Title,Subject,Institution,Journal,Publisher,Country,Author,URLS,ArticleType,RetractionDate,'
    'RetractionDOI,RetractionPubMedID,OriginalPaperDate,OriginalPaperDOI,OriginalPaperPubMedID,RetractionNature,'
    'Reason,Paywalled,Notes\n'
    '4036,Ileal-lymphoid-nodular hyperplasia,(BLS) Biology,Royal Free,The Lancet,Elsevier,UK,AJ Wakefield,,'
    'Research Article,2/6/2010 0:00,10.1016/S0140-6736(10)60175-4,20137807,2/28/1998 0:00,'
    '10.1016/S0140-6736(97)11096-0,9500320,Retraction,+Falsification/Fabrication of Data;+Misconduct;,No,\n'
    '1,Another,(HSC),X,Y,Z,US,B,,Research Article,1/1/2020,,,1/1/2019,10.5555/concern,0,Expression of concern,+Error;,No,\n'
    '2,No DOI,(HSC),X,Y,Z,US,B,,Research Article,1/1/2020,,,1/1/2019,unavailable,0,Retraction,+Error;,No,\n'
)


def test_a_retraction_watch_csv_is_imported_and_used_offline(tmp_path):
    file = tmp_path / 'retraction_watch.csv'
    file.write_text(RETRACTION_WATCH, encoding='utf-8')
    summary = import_retraction_watch(file)
    assert summary['imported'] == 2 and summary['skipped'] == 1 and len(summary['sha256']) == 64
    index = RetractionIndex()
    assert (
        index.info()['rows'] == '2' and index.lookup('10.5555/CONCERN')[0]['type'] == 'expression_of_concern'
    )
    doc = reference_doc(
        source('lancet', 'T', ['Wakefield'], 1998, LANCET), source('c', 'T', ['B'], 2019, '10.5555/concern')
    )
    report = bibliography.health(doc, checks=['retractions'], today=TODAY)
    assert report['network']['mode'] == 'none' and report['network']['requests'] == 0
    retraction = status_of(report, 'lancet', 'retractions')[0]
    assert (
        retraction['status'] == 'retracted'
        and 'Falsification' in retraction['detail']['notices'][0]['reason']
    )
    assert 'imported file' in retraction['detail']['notices'][0]['source']
    assert status_of(report, 'c', 'retractions')[0]['status'] == 'concern'
    assert report['retraction_index']['available'] is True


def test_a_csv_that_is_not_a_retraction_watch_export_is_rejected(tmp_path):
    with pytest.raises(ByaxError) as caught:
        import_retraction_watch('Name,Value\nx,1\n')
    assert caught.value.code == 'invalid_input' and 'OriginalPaperDOI' in caught.value.details['missing']
    assert not RetractionIndex().available()
    assert RetractionIndex().lookup(LANCET) == []


def test_without_an_import_or_a_lookup_retractions_are_not_checked():
    report = bibliography.health(reference_doc(*real_sources()), checks=['retractions'], today=TODAY)
    assert report['findings'] == []
    assert {e['checks'].get('retractions') for e in report['sources']} == {'not_checked'}


# --- the reference list, density, age and punctuation -----------------------------------------------


def cited_doc():
    doc = new_document(text='Intro without citations. ' + 'word ' * 4)
    doc['references'] = {
        'smith': source('smith', 'Smith Paper', ['Smith'], 2020),
        'jones': source('jones', 'Jones Paper', ['Jones'], 1999),
        'unused': source('unused', 'Unused Paper', ['Unused'], 2010),
    }
    intro = doc['blocks'][0]
    heading = {
        'id': 'h1',
        'type': 'heading',
        'text': 'Methods',
        'style': 'heading1',
        'attrs': {'level': 1},
        'marks': [],
    }
    body = {
        'id': 'p2',
        'type': 'paragraph',
        'text': 'Methods matter a great deal here (Smith, 2020). Prior work by Jones (1999) agrees. '
        'Others (Brown, 2018) disagree. Cited via mark. Ghost claim.',
        'style': 'body',
        'attrs': {},
        'marks': [
            {
                'type': 'citation',
                'value': 'smith',
                'start': 0,
                'end': 7,
                'kind': 'paraphrase',
                'quote': 'Methods',
            },
            {
                'type': 'citation',
                'value': 'ghost-id',
                'start': 107,
                'end': 118,
                'kind': 'paraphrase',
                'quote': 'Ghost claim',
            },
        ],
    }
    long_body = {
        'id': 'p3',
        'type': 'paragraph',
        'text': 'filler ' * 320,
        'style': 'body',
        'attrs': {},
        'marks': [],
    }
    doc['blocks'] = [
        intro,
        heading,
        body,
        {
            'id': 'h2',
            'type': 'heading',
            'text': 'Discussion',
            'style': 'heading1',
            'attrs': {'level': 1},
            'marks': [],
        },
        long_body,
    ]
    return doc


def test_citations_are_compared_with_the_reference_list():
    report = bibliography.health(cited_doc(), checks=['citations'], today=TODAY)
    listing = report['citation_list']
    assert [i['source'] for i in listing['cited_not_listed']] == ['ghost-id']
    assert [i['source'] for i in listing['listed_not_cited']] == ['unused']
    # "(Brown, 2018)" has no reference; "(Smith, 2020)" and "Jones (1999)" match one and so count as cited.
    assert [i['text'] for i in listing['possibly_not_listed']] == ['Brown, 2018']
    assert listing['cited_sources'] == 2 and listing['listed_sources'] == 3
    statuses = {(f['source'], f['status']) for f in report['findings']}
    assert ('ghost-id', 'cited_not_listed') in statuses and ('unused', 'listed_not_cited') in statuses


def test_citation_density_is_reported_per_section():
    report = bibliography.health(cited_doc(), checks=['density'], today=TODAY)
    sections = {s['heading']: s for s in report['density']['sections']}
    assert sections['Methods']['citations'] == 5 and sections['Methods']['words'] > 0
    assert sections['Discussion']['citations'] == 0 and sections['Discussion']['flag'] == 'no_citations'
    assert sections[None]['citations'] == 0 and sections[None]['flag'] is None
    assert sections['Methods']['per_1000_words'] > 0
    assert any(f['status'] == 'no_citations' and f['detail']['block_id'] == 'h2' for f in report['findings'])


def test_reference_age_distribution():
    report = bibliography.health(cited_doc(), checks=['age'], today=TODAY)
    listed = {b['label']: b['count'] for b in report['age']['listed']['buckets']}
    assert listed == {
        '0-2 years': 0,
        '3-5 years': 0,
        '6-10 years': 1,
        '11-20 years': 1,
        'over 20 years': 1,
        'no year': 0,
    }
    assert report['age']['listed']['median_age'] == 16 and report['age']['listed']['oldest_year'] == 1999
    assert report['age']['cited']['count'] == 2  # Smith by mark, Jones by author-year text


def marked(text, start, end, **block):
    doc = new_document(text=text)
    doc['references'] = {'s': source('s', 'T', ['Smith'], 2020)}
    doc['blocks'][0]['marks'] = [
        {
            'type': 'citation',
            'value': 's',
            'start': start,
            'end': end,
            'kind': 'paraphrase',
            'quote': text[start:end],
        }
    ]
    doc['blocks'][0].update(block)
    return doc


def rules(doc):
    return [f['status'] for f in bibliography.health(doc, checks=['punctuation'], today=TODAY)['findings']]


def test_period_placement_in_author_date_styles():
    # The marker is inserted at the end of the mark: before the period is right, after it is wrong.
    assert rules(marked('Sleep matters.', 0, 13)) == []
    assert rules(marked('Sleep matters.', 0, 14)) == ['period_after_citation']
    assert rules(marked('Sleep matters. Next sentence.', 0, 14)) == ['period_after_citation']
    # "et al." before the marker is not the end of a sentence.
    assert rules(marked('Smith et al. found it.', 0, 11)) == []
    # A block quotation puts the period first in every style.
    assert rules(marked('Sleep matters.', 0, 14, style='quotation')) == []


def test_note_marks_belong_after_the_punctuation():
    doc = marked('Sleep matters.', 0, 13)
    doc['settings']['citations'] = {'style': 'chicago', 'presentation': 'footnotes'}
    assert rules(doc) == ['note_after_punctuation']
    doc2 = marked('Sleep matters.', 0, 14)
    doc2['settings']['citations'] = {'style': 'chicago', 'presentation': 'footnotes'}
    assert rules(doc2) == []
    quote = marked('He said “sleep matters.”', 0, 23)
    quote['settings']['citations'] = {'style': 'chicago', 'presentation': 'endnotes'}
    assert rules(quote) == ['note_after_punctuation']
    numbered = marked('Sleep matters.', 0, 14)
    numbered['settings']['citations'] = {'style': 'apa', 'presentation': 'numbered'}
    assert rules(numbered) == ['period_after_citation']


def test_health_does_not_modify_the_document():
    doc = cited_doc()
    before = copy.deepcopy(doc)
    bibliography.health(doc, lookup=online(), today=TODAY)
    assert doc == before
    with pytest.raises(ByaxError):
        bibliography.health(doc, checks=['astrology'])


# --- links --------------------------------------------------------------------------------------------


def test_dead_links_and_redirects_with_an_optional_archived_copy():
    doc = reference_doc(
        source('alive', 'A', ['A'], 2020, url='https://www.crossref.org/'),
        source('moved', 'M', ['A'], 2020, url='https://doi.org/10.1038/nature14539'),
        source('gone', 'G', ['A'], 2020, url='https://www.crossref.org/this-page-does-not-exist-byax'),
    )
    lookup = online()
    report = bibliography.health(doc, lookup=lookup, checks=['links'], archive=True, today=TODAY)
    entries = by_source(report)
    assert entries['alive']['checks']['links'] == 'ok' and entries['moved']['checks']['links'] == 'ok'
    assert entries['gone']['checks']['links'] == 'dead'
    finding = status_of(report, 'gone', 'links')[0]
    assert finding['status'] == 'dead' and finding['detail']['http_status'] == 404
    assert 'archived_copy' not in finding['detail']  # the recorded Wayback answer has no snapshot for it
    assert entries['gone']['archived_copy'] == {'status': 'ok', 'available': False}
    archived = lookup.archived_copy('https://example.com/')
    assert archived['available'] and archived['url'].startswith('https://web.archive.org/web/')


def test_links_to_this_computer_or_a_private_network_are_never_requested():
    assert private_address('localhost') and private_address('192.168.1.5') and private_address('127.0.0.1')
    assert private_address('intranet') and private_address('printer.local') and private_address('::1')
    assert not private_address('www.crossref.org') and not private_address('8.8.8.8')
    transport = replay()
    lookup = online(transport)
    for url in (
        'http://localhost:8000/x',
        'http://10.0.0.5/admin',
        'ftp://example.com/file',
        'file:///etc/passwd',
    ):
        assert lookup.check_url(url)['status'] == 'blocked'
    assert transport.requests == []


def test_a_redirect_to_a_private_address_is_stopped():
    def redirect(request):
        return httpx.Response(302, headers={'location': 'http://169.254.169.254/latest'})

    lookup = online(httpx.MockTransport(redirect))
    result = lookup.check_url('https://example.com/start')
    assert result['status'] == 'blocked' and lookup.requests_made == 1


def test_a_site_that_refuses_automatic_checks_is_unverified_not_dead():
    lookup = online(httpx.MockTransport(lambda request: httpx.Response(403)))
    result = lookup.check_url('https://publisher.example/paper')
    assert result['status'] == 'unverified' and '403' in result['message']
    timeout = online(
        httpx.MockTransport(lambda request: (_ for _ in ()).throw(httpx.ReadTimeout('slow', request=request)))
    )
    assert timeout.check_url('https://slow.example/')['status'] == 'unverified'


def test_wayback_snapshots_are_only_offered_from_the_archive_itself():
    answer = {
        'archived_snapshots': {
            'closest': {'available': True, 'url': 'http://evil.example/web/1/x', 'timestamp': '1'}
        }
    }
    lookup = online(httpx.MockTransport(lambda request: httpx.Response(200, json=answer)))
    assert lookup.archived_copy('https://x.example/')['available'] is False


# --- report shape -----------------------------------------------------------------------------------


def test_the_report_is_deterministic_and_orders_serious_findings_first():
    doc = reference_doc(*real_sources())
    one = bibliography.health(doc, lookup=online(), today=TODAY)
    two = bibliography.health(doc, lookup=online(cache=None), today=TODAY)
    assert json.dumps(one['findings'], sort_keys=True) == json.dumps(two['findings'], sort_keys=True)
    severities = [f['severity'] for f in one['findings']]
    assert severities == sorted(severities, key=bibliography.SEVERITIES.index)
    assert one['findings'][0]['status'] == 'retracted'
    text = bibliography.describe(one)
    assert 'unverified' in ' '.join(text).lower() or one['summary']['unverified'] == 0


def test_a_registry_answer_in_an_unexpected_shape_is_an_error_not_a_verdict():
    answers = {
        'GET https://api.crossref.org/works/10.1234/odd': {
            'status': 200,
            'json': {'message': {'updated-by': 5, 'title': 7}},
        },
        'GET https://api.datacite.org/dois/10.1234/odd': {'status': 200, 'json': ['not', 'an', 'object']},
        'GET https://api.openalex.org/works/https://doi.org/10.1234/odd': {'status': 404, 'text': 'nope'},
    }
    doc = reference_doc(source('odd', 'Odd', ['A'], 2020, '10.1234/odd'))
    report = bibliography.health(
        doc, lookup=online(replay(**answers)), checks=['doi', 'metadata'], today=TODAY
    )
    assert by_source(report)['odd']['checks']['doi'] == 'error'
    assert 'Unverified' not in ' '.join(f['message'] for f in report['findings'])


def test_author_year_detection_stays_fast_on_long_runs_of_names():
    import time

    text = ', '.join(f'Name{chr(65 + i % 26)}x' for i in range(3000)) + ' (and nothing else here).'
    doc = new_document(text=text)
    started = time.perf_counter()
    report = bibliography.health(doc, checks=['citations', 'density'], today=TODAY)
    assert time.perf_counter() - started < 5
    assert report['citation_list']['possibly_not_listed'] == []
