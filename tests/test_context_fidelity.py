"""Q1+ fidelity cases from the context engine plan (section 4), run on the evaluation fixtures.

Jump fidelity, scope leaks, false absence, stability and concordance. A case that the 0.3.0 engine fails
today is `xfail(strict=True)` with the plan item that fixes it, so it flips to a failure the day the fix
lands and must then be un-marked. Corpora are the fast subsets from `byax.evaluation.SAMPLES`.
"""

import re

import pytest
from eval_support import sample_corpus, sample_queries, small_queries

from byax import evaluation as ev
from byax import quote_match
from byax.errors import ByaxError
from byax.libraries import LibraryIndex, LibraryRegistry
from byax.model import new_document
from byax.retrieval import authorize_library_context, retrieve_context, validate_citations

LIBRARIES = ev.FIXTURE_LIBRARIES

# Cases the engine fails today, keyed by query id, each with the plan item that fixes it. (The 0.3.0 gaps
# this table once held, phrase-over-hard-wrap false absence and hyphenated JATS quotes, were closed by
# the schema 3 chunker, verify_absence and quote_match.)
ABSENCE_XFAIL = {}


def collapse(text):
    return ' '.join(text.split())


def term_pattern(term):
    return re.compile(r'(?<![^\W_])' + re.escape(term).replace(r'\ ', r'\s+') + r'(?![^\W_])', re.IGNORECASE)


def marked(cases, table):
    """pytest params for (query id, ...) cases; ids in `table` are expected to fail, with their reason."""
    return [
        pytest.param(
            *case,
            id='-'.join(map(str, case)),
            marks=[pytest.mark.xfail(strict=True, reason=table[case[0]])] if case[0] in table else [],
        )
        for case in cases
    ]


def document_ids(corpus):
    return {item['path']: item['document_id'] for item in corpus.index.status()['files']}


# --- jump fidelity -----------------------------------------------------------------------------------

JUMP_CASES = [
    (q['id'], index)
    for q in sample_queries('papers')
    for index, locator in enumerate(q['relevant_chunk_locators'])
    if 'quote' in locator
]


def page_text(corpus, locator):
    result = corpus.index.read(
        document_ids(corpus)[locator['path']], locator={'page': locator['page']}, limit=100000
    )
    assert result['results']
    return '\n'.join(item['text'] for item in result['results'])


@pytest.mark.parametrize(('query_id', 'position'), JUMP_CASES, ids=lambda v: str(v))
def test_jump_lands_on_recorded_page_with_quote(query_id, position):
    """Following a recorded page locator through `LibraryIndex.read` yields text containing the cited quote
    (exactly or after quote_match normalisation: PDF extraction hyphenates lines and expands ligatures)."""
    corpus = sample_corpus('papers')
    record = next(q for q in sample_queries('papers') if q['id'] == query_id)
    locator = record['relevant_chunk_locators'][position]
    found = quote_match.locate(locator['quote'], page_text(corpus, locator))
    assert found['normalized_pass'], found


def test_normalisation_rescues_quotes_that_a_raw_comparison_misses():
    """The Phase 1 exit figure: raw versus normalised quote pass rate on genuine quotes (JATS wording located
    in extracted PDF pages). Raw is strictly lower; normalised is complete."""
    corpus = sample_corpus('papers')
    raw = normalised = total = 0
    for query_id, position in JUMP_CASES:
        record = next(q for q in sample_queries('papers') if q['id'] == query_id)
        locator = record['relevant_chunk_locators'][position]
        found = quote_match.locate(locator['quote'], page_text(corpus, locator))
        raw += found['raw_pass']
        normalised += found['normalized_pass']
        total += 1
    assert total >= 20 and normalised == total and raw < total


def test_every_pdf_hit_locator_round_trips_through_read():
    corpus = sample_corpus('papers')
    ids = document_ids(corpus)
    checked = 0
    for record in sample_queries('papers'):
        for hit in corpus.index.search(record['query'], limit=5)['results']:
            again = corpus.index.read(hit['document_id'], hit['chunk_id'])['results'][0]
            assert (again['locator'], again['text']) == (hit['locator'], hit['text'])
            page = corpus.index.read(ids[hit['path']], locator={'page': hit['locator']['page']})
            assert hit['text'] in [item['text'] for item in page['results']]
            checked += 1
    assert checked > 50


def test_every_stored_chunk_aligns_to_the_source_in_order():
    """The harness's offset mapping covers all chunks (including ones the read budget truncates)."""
    corpus = sample_corpus('humanities')
    (item,) = corpus.index.status()['files']
    spans = corpus._map_document(item['document_id'], item['path'])
    assert len(spans) > 30 and None not in spans.values()
    starts = [start for start, _ in spans.values()]
    assert starts == sorted(starts)
    text = corpus.text(item['path'])
    sample = list(spans.items())[:: len(spans) // 40]
    for chunk_id, (start, end) in sample:
        stored = corpus.index.read(item['document_id'], chunk_id)['results'][0]['text']
        assert collapse(stored) == collapse(text[start:end])


@pytest.mark.parametrize('library', ['novel', 'series', 'humanities', 'webclips'])
def test_text_hit_spans_match_source_text(library):
    """Hits map back to source offsets, and the chunk text is a substring of the text at those offsets."""
    corpus = sample_corpus(library)
    checked = 0
    for record in sample_queries(library)[:5]:
        for hit in corpus.index.search(record['query'], limit=3)['results']:
            spans = corpus.hit_spans(hit)
            assert spans, hit['locator']
            _, path, start, end = spans[0]
            assert collapse(hit['text']) in collapse(corpus.text(path)[start:end])
            checked += 1
    assert checked
    assert corpus.alignment['unmapped'] == 0


# --- scope leaks ---------------------------------------------------------------------------------------


SCOPE_SPECS = {
    'papers': ev.SAMPLES['papers'],
    'series': {'files': ['adventures/01-a-scandal-in-bohemia.txt']},
    'webclips': {'every': 12},
}


@pytest.fixture(scope='module')
def scope(tmp_path_factory):
    """Three libraries: open (attached), local_only (attached) and a web clip library (attached only when a test says so)."""
    base = tmp_path_factory.mktemp('scope')
    registry = LibraryRegistry(base / 'config')
    cache = base / 'cache'
    plan = {
        'open': ('papers', 'ask'),
        'local': ('series', 'local_only'),
        'wild': ('webclips', 'ask'),
    }
    libraries, queries = {}, {}
    for name, (source, policy) in plan.items():
        corpus = ev.build_fixture_corpus(source, base / 'corpora', sample=SCOPE_SPECS[source], refresh=False)
        libraries[name] = registry.create(name, [corpus.root], retrieval_policy=policy)
        LibraryIndex(libraries[name], cache_dir=cache).refresh()
        queries[source] = [
            q['query']
            for q in small_queries(source, SCOPE_SPECS[source])
            if q['relevant_chunk_locators'] and q['type'] != 'absence_probe'
        ]
        assert queries[source], source
    return {'registry': registry, 'cache': cache, 'libraries': libraries, 'queries': queries}


def attach(scope, roles):
    doc = new_document()
    doc['settings']['libraries'] = [
        {'library_id': scope['libraries'][name]['library_id'], 'role': role} for name, role in roles
    ]
    return doc


def route(scope, doc, query, **kwargs):
    return retrieve_context(doc, query, registry=scope['registry'], cache_dir=scope['cache'], **kwargs)


def library_queries(scope, library, count=6):
    return scope['queries'][library][:count]


def test_unattached_and_local_only_libraries_never_reach_a_remote_route(scope):
    doc = attach(scope, [('open', 'supplementary'), ('local', 'supplementary')])
    ids = {name: library['library_id'] for name, library in scope['libraries'].items()}
    for library in ('papers', 'series', 'webclips'):
        for query in library_queries(scope, library):
            result = route(scope, doc, query, remote=True)
            used = {passage['library_id'] for passage in result['included']}
            assert used <= {ids['open']}
            assert ids['local'] not in used
            assert ids['wild'] not in used
            assert all(
                p['remote_bound'] and p['retrieval_policy'] != 'local_only' for p in result['included']
            )
    reasons = [
        o['reason'] for o in route(scope, doc, library_queries(scope, 'series')[0], remote=True)['omitted']
    ]
    assert 'Library is local_only' in reasons
    # The same attachment is searched on the local route.
    local = route(scope, doc, library_queries(scope, 'series')[0], remote=False)
    assert {p['library_id'] for p in local['included']} >= {ids['local']}


def test_open_sources_are_excluded_under_exclusive_authority_and_globs_hide_files(scope, tmp_path):
    doc = attach(scope, [('wild', 'open')])
    profile = {'source_authority': 'exclusive'}
    for query in library_queries(scope, 'webclips'):
        result = route(scope, doc, query, profile=profile)
        assert result['included'] == []
        assert result['omitted'][0]['reason'] == 'Active profile excludes open sources'
    assert route(scope, doc, library_queries(scope, 'webclips')[0])[
        'included'
    ]  # not excluded without the profile
    # An exclusion glob keeps a file out of the index altogether, so no route can return it.
    root = scope['libraries']['wild']['roots'][0]
    own = next(
        q for q in small_queries('webclips', SCOPE_SPECS['webclips']) if q['type'] == 'known_item_quote'
    )
    hidden = own['relevant_chunk_locators'][0]['path'] + '.txt'
    registry = LibraryRegistry(tmp_path / 'config')
    visible = LibraryIndex(registry.create('plain', [root]), cache_dir=tmp_path / 'cache')
    hiding = LibraryIndex(registry.create('globbed', [root], exclude=[hidden]), cache_dir=tmp_path / 'cache')
    for index in (visible, hiding):
        index.refresh()
    assert hidden in {p for hit in visible.search(own['query'], limit=100)['results'] for p in hit['paths']}
    assert hidden not in {
        p for hit in hiding.search(own['query'], limit=100)['results'] for p in hit['paths']
    }


def test_local_only_passages_cannot_be_authorised_for_a_remote_provider(scope, tmp_path):
    doc = attach(scope, [('local', 'supplementary')])
    context = {
        'library_passages': route(scope, doc, library_queries(scope, 'series')[0], remote=False)['included']
    }
    assert context['library_passages']
    with pytest.raises(ByaxError):
        authorize_library_context(context, remote=True, provider='host', config_dir=tmp_path, authorized=True)


@pytest.mark.parametrize('order', [('wild', 'open'), ('open', 'wild')])
def test_a_library_attached_later_is_not_starved_by_the_first(scope, order):
    """Cross-library starvation (CE §5.1): concatenating per-library results in attachment order would fill the
    budget with the first library. Global fusion must put the library that actually holds the quote in the
    top results whichever way round the two are attached."""
    doc = attach(scope, [(name, 'supplementary') for name in order])
    ids = {name: scope['libraries'][name]['library_id'] for name in order}
    for library, name in (('papers', 'open'), ('webclips', 'wild')):
        for query in library_queries(scope, library, 3):
            result = route(scope, doc, query, limit=12)
            top = [p['library_id'] for p in result['included'][:3]]
            assert ids[name] in top, (library, query)


# --- false absence -------------------------------------------------------------------------------------

ABSENCE_CASES = [
    (q['id'], library)
    for library in LIBRARIES
    for q in sample_queries(library)
    if q['type'] == 'absence_probe' and not q.get('expected_absent')
]


@pytest.mark.parametrize(('query_id', 'library'), marked(ABSENCE_CASES, ABSENCE_XFAIL))
def test_literal_terms_are_found_by_an_exhaustive_lexical_pass(query_id, library):
    """Page through every result of a plain search: some passage must contain the whole literal term."""
    corpus = sample_corpus(library)
    record = next(q for q in sample_queries(library) if q['id'] == query_id)
    term = record['terms'][0]
    pattern = term_pattern(term)
    offset = 0
    while True:
        page = corpus.index.search(term, limit=100, offset=offset)
        if any(pattern.search(hit['text']) for hit in page['results']):
            return
        if page['continuation'] is None:
            break
        offset = page['continuation']
    pytest.fail(f'{term!r} occurs in the corpus but no passage containing it was returned')


@pytest.mark.parametrize('library', LIBRARIES)
def test_terms_absent_from_the_corpus_are_never_claimed_present(library):
    corpus = sample_corpus(library)
    probes = [q for q in sample_queries(library) if q.get('absence_kind') == 'term']
    assert probes
    for record in probes:
        pattern = term_pattern(record['query'])
        for hit in corpus.index.search(record['query'], limit=100)['results']:
            assert not pattern.search(hit['text'])


# --- stability and concordance ---------------------------------------------------------------------------


@pytest.mark.parametrize('library', ['novel', 'series', 'webclips', 'papers'])
def test_identical_queries_give_identical_rankings(library):
    corpus = sample_corpus(library)
    for record in sample_queries(library)[:6]:
        first = [h['chunk_id'] for h in corpus.index.search(record['query'], limit=20)['results']]
        second = [h['chunk_id'] for h in corpus.index.search(record['query'], limit=20)['results']]
        assert first == second


def test_hybrid_rankings_are_stable_with_a_deterministic_embedder():
    corpus = sample_corpus('papers')
    config = ev.HybridConfig()
    config.prepare(corpus)
    for record in sample_queries('papers')[:10]:
        first = [h['chunk_id'] for h in config.search(corpus, record['query'], 20)]
        second = [h['chunk_id'] for h in config.search(corpus, record['query'], 20)]
        assert first == second


def test_cited_ids_are_a_subset_of_retrieved_ids(scope):
    doc = attach(scope, [('open', 'supplementary'), ('local', 'supplementary')])
    for library in ('papers', 'series'):
        for query in library_queries(scope, library, 4):
            result = route(scope, doc, query)
            retrieved = [p['chunk_id'] for p in result['included']]
            assert set(result['allowed_citation_ids']) == set(retrieved)
            context = {'library_passages': result['included'], 'references': {}}
            cited = ' '.join(f'Claim [[cite:{c}]].' for c in retrieved)
            assert validate_citations(cited, context) == {'text': cited, 'invalid': []}
            stripped = validate_citations(cited + ' Invented [[cite:chunk_not_retrieved]].', context)
            assert stripped['invalid'] == ['chunk_not_retrieved']
            assert 'chunk_not_retrieved' not in stripped['text']
            for passage in result['included']:
                index = LibraryIndex(
                    next(
                        lib
                        for lib in scope['libraries'].values()
                        if lib['library_id'] == passage['library_id']
                    ),
                    cache_dir=scope['cache'],
                )
                assert (
                    index.read(passage['document_id'], passage['chunk_id'])['results'][0]['text']
                    == passage['text']
                )


@pytest.mark.parametrize('library', LIBRARIES)
def test_the_exhaustive_absence_pass_never_misses_a_literal_term(library):
    """`retrieval.verify_absence` (CE §5.6) backs every "not found": zero false absence on the probes whose
    term occurs literally. Stemmed variants and substrings of longer words are reported by design."""
    report = ev.verified_absence(sample_corpus(library), sample_queries(library))
    assert report['literal_probes'] >= 8
    assert report['false_absence'] == 0, report['false_absence_ids']
    assert (
        report['substring_matches_reported'] + report['stem_variants_reported']
        <= report['absent_term_probes']
    )
