"""Retrieval evaluation harness: metric maths, fixture integrity and the CI regression gate.

The keyword and fake-embedder configurations run on every push against floors stored in
`tests/fixtures/eval/thresholds.json`. A metric that falls more than the tolerance below its floor fails
the build; improving a metric never does (regenerate the floors with
`scripts/eval_retrieval.py --sample --configs keyword hybrid-fake --update-thresholds`).
Model-backed configurations belong under the `eval_models` marker.
"""

import hashlib
import importlib.util
import json
import math
import re
from pathlib import Path

import pytest
from eval_support import sample_corpus, workdir

from byax import evaluation as ev
from byax.errors import ByaxError

ROOT = ev.fixtures_root()
THRESHOLDS = json.loads((ROOT / 'thresholds.json').read_text(encoding='utf-8'))
MANIFEST = json.loads((ROOT / 'MANIFEST.json').read_text(encoding='utf-8'))
_SPEC = importlib.util.spec_from_file_location(
    'byax_scale_fixtures', Path(__file__).resolve().parents[1] / 'scripts' / 'generate_fixtures.py'
)
_GENERATOR = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_GENERATOR)


# --- metric maths --------------------------------------------------------------------------------------


def span(path, start, end):
    return ('span', path, start, end)


def test_scores_for_hand_computed_rankings():
    locators = [{'path': 'a', 'start': 0, 'end': 10}, {'path': 'a', 'start': 100, 'end': 120}]
    hits = [[span('b', 0, 5)], [span('a', 5, 15)], [span('a', 0, 3)], [], [span('a', 110, 112)]]
    scores = ev.score_hits(hits, locators)
    assert scores['recall@5'] == 1.0
    assert scores['mrr'] == 0.5  # first relevant hit is rank 2
    # gains at ranks 2 and 5 (rank 3 re-covers the first locator); ideal gains at ranks 1 and 2
    dcg = 1 / math.log2(3) + 1 / math.log2(6)
    assert scores['ndcg@10'] == pytest.approx(dcg / (1 + 1 / math.log2(3)))
    assert ev.score_hits([[span('b', 0, 5)]] * 3, locators) == {
        'recall@5': 0.0, 'recall@20': 0.0, 'ndcg@10': 0.0, 'mrr': 0.0,
    }  # fmt: skip
    late = [[span('b', 0, 1)]] * 7 + [[span('a', 0, 10)]]
    assert ev.score_hits(late, locators[:1])['recall@5'] == 0.0
    assert ev.score_hits(late, locators[:1])['recall@20'] == 1.0


def test_page_and_offset_locators_overlap_by_path():
    assert ev.overlaps(('page', 'p.pdf', 3), {'path': 'p.pdf', 'page': 3})
    assert not ev.overlaps(('page', 'p.pdf', 3), {'path': 'p.pdf', 'page': 4})
    assert not ev.overlaps(('page', 'p.pdf', 3), {'path': 'q.pdf', 'page': 3})
    assert ev.overlaps(('span', 't', 10, 20), {'path': 't', 'start': 19, 'end': 30})
    assert not ev.overlaps(('span', 't', 10, 20), {'path': 't', 'start': 20, 'end': 30})
    assert not ev.overlaps(('span', 't', 10, 20), {'path': 't', 'page': 1})


def test_percentiles_use_nearest_rank():
    values = [float(n) for n in range(1, 101)]
    assert ev.percentile(values, 0.5) == 50.0
    assert ev.percentile(values, 0.95) == 95.0
    assert ev.percentile([], 0.95) == 0.0


def test_shadow_maps_collapsed_text_back_to_source_offsets():
    text = 'Alpha  beta\n  gamma\n\ndelta'
    shadow = ev.Shadow(text)
    start, end = shadow.find('beta gamma')
    assert text[slice(*shadow.span(start, end))] == 'beta\n  gamma'
    assert shadow.find_all('DELTA', casefold=True) == [(text.index('delta'), len(text))]
    assert ev.squash('infor-\nmation, ﬁnal') == 'informationfinal'


def test_html_text_keeps_chrome_but_not_scripts():
    html = '<html><head><title>T - Wikipedia</title><script>var x=1</script></head><body><nav><a>Main menu</a></nav><h2>History</h2><p>One <b>two</b>.</p></body></html>'
    assert ev.html_text(html) == 'T - Wikipedia\n\nMain menu\n\nHistory\n\nOne two.\n'


def test_thinning_is_deterministic_type_balanced_and_bounded():
    queries = [{'id': f'{kind}-{n:02d}', 'type': kind} for kind in ('a', 'b', 'c') for n in range(20)]
    chosen = ev.thin_queries(queries, 9)
    assert chosen == ev.thin_queries(queries, 9)
    assert len(chosen) == 9 and {q['type'] for q in chosen} == {'a', 'b', 'c'}
    assert ev.thin_queries(queries, 100) == queries


def test_query_schema_rejects_bad_records():
    good = {
        'id': 'x',
        'type': 'thematic',
        'query': 'q',
        'relevant_chunk_locators': [{'path': 'p', 'start': 0, 'end': 4}],
    }
    assert ev.validate_query(good) is good
    for broken in (
        {**good, 'type': 'trivia'},
        {**good, 'relevant_chunk_locators': []},
        {**good, 'expected_absent': True},
        {**good, 'relevant_chunk_locators': [{'path': 'p'}]},
        {**good, 'query': ' '},
    ):
        with pytest.raises(ValueError):
            ev.validate_query(broken)
    absent = {
        'id': 'y',
        'type': 'absence_probe',
        'query': 'zzz',
        'relevant_chunk_locators': [],
        'expected_absent': True,
    }
    assert ev.validate_query(absent)


def test_configurations_resolve_from_names_dicts_objects_and_callables():
    assert ev.resolve_config('keyword').name == 'keyword'
    assert ev.resolve_config({'name': 'hybrid-fake', 'dimension': 32}).embedder.dimension == 32
    config = ev.KeywordConfig()
    assert ev.resolve_config(config) is config
    assert ev.resolve_config(lambda corpus, query, limit: []).description.startswith('caller-supplied')
    with pytest.raises(ByaxError):
        ev.resolve_config('no-such-config')
    ev.register_config('unit-test-config', ev.KeywordConfig)
    try:
        assert isinstance(ev.resolve_config('unit-test-config'), ev.KeywordConfig)
    finally:
        del ev.CONFIGS['unit-test-config']


def test_fake_embedder_is_deterministic_and_normalised():
    embedder = ev.FakeEmbedder(32)
    first = embedder.embed(['The copper moon', 'copper moons'])
    assert first == embedder.embed(['The copper moon', 'copper moons'])
    assert all(abs(math.sqrt(sum(x * x for x in v)) - 1) < 1e-3 for v in first)
    assert ev.FakeEmbedder(32).model_identity != ev.FakeEmbedder(64).model_identity


def test_a_callable_configuration_is_scored_like_any_other():
    corpus = sample_corpus('papers')
    records = [
        q
        for q in ev.load_queries('papers')
        if q['relevant_chunk_locators'] and all(corpus.covers(x) for x in q['relevant_chunk_locators'])
    ][:4]
    empty = ev.evaluate(corpus, records, lambda c, query, limit: [])
    assert empty['metrics'] == {'recall@5': 0.0, 'recall@20': 0.0, 'ndcg@10': 0.0, 'mrr': 0.0}
    assert empty['config']['name'] == '<lambda>'
    echo = ev.evaluate(corpus, records, lambda c, query, limit: c.index.search(query, limit=limit)['results'])
    assert echo['metrics']['recall@20'] > 0.5


def test_model_backed_configuration_plumbing_runs_without_a_model(tmp_path, monkeypatch):
    from byax.providers import ProviderConfig, ProviderRegistry

    registry = ProviderRegistry(tmp_path)
    registry.save(ProviderConfig('local', 'ollama', 'stand-in-embedder'))
    stand_in = ev.FakeEmbedder(8)
    monkeypatch.setattr(
        registry, '_request', lambda *a, **k: {'embeddings': stand_in.embed(k['json']['input'])}
    )
    config = ev.model_hybrid_config({'provider': 'local', 'dimension': 8}, registry=registry)
    corpus = ev.build_fixture_corpus('papers', tmp_path / 'work', sample={'files': ['pcbi.1004150.pdf']})
    queries = [
        q
        for q in ev.load_queries('papers')
        if q['relevant_chunk_locators'] and corpus.covers(q['relevant_chunk_locators'][0])
    ]
    report = ev.evaluate(corpus, queries[:5], config)
    assert (
        report['config']['name'] == 'hybrid-model'
        and 'configured embedding provider' in report['config']['description']
    )
    assert report['metrics']['recall@20'] > 0 and report['index']['prepare_seconds'] > 0
    with pytest.raises(ByaxError):
        ev.resolve_config({'name': 'hybrid-model'})


# --- fixture integrity --------------------------------------------------------------------------------


def committed_entries():
    for name, library in MANIFEST['libraries'].items():
        for entry in library.get('files', []):
            yield name, entry
        for entry in library.get('pinned', []):
            if entry['committed']:
                for kind in ('pdf', 'xml'):
                    yield name, {'path': f'papers/corpus/{entry["name"]}.{kind}', 'sha256': entry[f'{kind}_sha256'],
                                 'bytes': entry[f'{kind}_bytes'], 'license': entry['license'], 'source_url': entry[f'{kind}_url'],
                                 'attribution': entry['attribution']}  # fmt: skip


def test_every_committed_fixture_matches_the_manifest():
    entries = list(committed_entries())
    assert len(entries) >= 60
    for _library, entry in entries:
        data = (ROOT / entry['path']).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry['sha256'], entry['path']
        assert len(data) == entry['bytes']
        assert entry['license'] and entry['source_url'].startswith('https://') and entry['attribution']
    on_disk = {p.relative_to(ROOT).as_posix() for p in ROOT.rglob('*/corpus/**/*') if p.is_file()}
    assert on_disk == {entry['path'] for _, entry in entries}, 'files without a manifest entry'
    assert sum((ROOT / entry['path']).stat().st_size for _, entry in entries) < 12 * 1024**2


def test_pinned_papers_cover_the_full_thirty_with_hashes_and_cc_by():
    pinned = MANIFEST['libraries']['papers']['pinned']
    assert len(pinned) == 30 and sum(1 for p in pinned if p['committed']) == 3
    assert len({p['doi'] for p in pinned}) == 30
    for paper in pinned:
        assert paper['license'] == 'CC BY 4.0' and paper['license_url'].endswith('/licenses/by/4.0/')
        assert len(paper['pdf_sha256']) == 64 and len(paper['xml_sha256']) == 64
        assert paper['pdf_url'].startswith('https://journals.plos.org/')


def test_gutenberg_header_footer_and_trademark_are_stripped():
    for library in ('novel', 'series', 'humanities'):
        for path in (ROOT / library / 'corpus').rglob('*.txt'):
            text = path.read_text(encoding='utf-8')
            assert 'gutenberg' not in text.lower() and '\r' not in text and not text.startswith('﻿')


def test_drafts_distract_with_renamed_characters_and_changed_details():
    novel = (ROOT / 'novel/corpus/pride-and-prejudice.txt').read_text(encoding='utf-8')
    for draft, name, number in (('draft-first-impressions-1.txt', 'Mrs. Lang', 'three or four thousand'),
                                ('draft-first-impressions-2.txt', 'Mrs. Lyle', 'five or six thousand')):  # fmt: skip
        text = (ROOT / 'novel/corpus/drafts' / draft).read_text(encoding='utf-8')
        flat = ' '.join(text.split())
        assert name in flat and number in flat and 'Mrs. Long' not in flat
        assert text.count('CHAPTER') == 6 and len(text) < len(novel) / 10


@pytest.mark.parametrize('library', ev.FIXTURE_LIBRARIES)
def test_golden_queries_are_complete_valid_and_correctly_labelled(library):
    queries = ev.load_queries(library)
    assert len(queries) >= 100  # the full set: papers needs the fetched articles for the rest
    assert len({q['id'] for q in queries}) == len(queries)
    kinds = {q['type'] for q in queries}
    assert {'known_item_quote', 'factual_lookup', 'absence_probe'} <= kinds
    assert kinds >= ({'thematic'} | ({'continuity'} if library in {'novel', 'series'} else set()))
    probes = [q for q in queries if q['type'] == 'absence_probe']
    assert any(q.get('expected_absent') for q in probes) and any(not q.get('expected_absent') for q in probes)
    source = ROOT / library / 'corpus'
    cache_pages, cache_texts, problems = {}, {}, []
    for record in queries:
        if all((source / x['path']).exists() for x in record['relevant_chunk_locators']):
            problems += ev.label_problems(record, source, pages=cache_pages, texts=cache_texts)
    assert not problems, problems[:5]


def test_absence_probes_for_absent_terms_really_are_absent_from_the_committed_text():
    for library in ('novel', 'series', 'humanities', 'webclips'):
        corpus_text = ' '.join(
            (
                ev.html_text(p.read_text(encoding='utf-8'))
                if p.suffix == '.html'
                else p.read_text(encoding='utf-8')
            ).casefold()
            for p in (ROOT / library / 'corpus').rglob('*')
            if p.suffix in {'.txt', '.html'}
        )
        words = set(re.findall(r'[^\W_]+', corpus_text))
        for record in ev.load_queries(library):
            if record.get('absence_kind') == 'term':
                term = record['query'].casefold()
                assert (term not in corpus_text) if '-' in term else (term not in words), (library, term)


# --- the CI regression gate ------------------------------------------------------------------------------

GATED = [(config, library) for config, libraries in THRESHOLDS['floors'].items() for library in libraries]


@pytest.mark.parametrize(('config_name', 'library'), GATED)
def test_metrics_do_not_regress_beyond_tolerance(config_name, library):
    floor = THRESHOLDS['floors'][config_name][library]
    config = ev.resolve_config(config_name)
    spec = config.samples.get(library, ev.SAMPLES.get(library, {}))
    corpus = (
        sample_corpus(library)
        if config_name == 'keyword'
        else ev.build_fixture_corpus(library, workdir() / config_name, sample=spec)
    )
    report = ev.evaluate(corpus, ev.load_queries(library), config, max_queries=ev.SAMPLE_QUERIES)
    assert report['queries']['evaluated'] == floor['queries'], (
        'the sampled query set changed: regenerate the floors'
    )
    for metric in ev.METRICS:
        assert report['metrics'][metric] >= floor[metric] - THRESHOLDS['tolerance'][metric], (
            f'{config_name}/{library} {metric}: {report["metrics"][metric]} < floor {floor[metric]}'
        )
    assert report['latency_ms']['p95'] <= THRESHOLDS['latency_p95_ms_max'][config_name]
    assert report['alignment']['unmapped'] == 0


def test_hybrid_fake_never_loses_exact_matches_that_keyword_finds():
    keyword = THRESHOLDS['floors']['keyword']['papers']
    hybrid = THRESHOLDS['floors']['hybrid-fake']['papers']
    assert hybrid['recall@20'] >= keyword['recall@20'] - THRESHOLDS['tolerance']['recall@20'] - 0.05


def test_synthetic_scale_corpus_builds_and_answers_needle_queries(tmp_path):
    info = _GENERATOR.make_eval_scale(tmp_path / 'scale', documents=12, queries=6)
    assert info['documents'] == 12
    corpus = ev.build_corpus('scale', tmp_path / 'scale' / 'corpus', tmp_path / 'work')
    assert corpus.refresh['documents'] == 12
    report = ev.evaluate(corpus, ev.load_queries_file(tmp_path / 'scale' / 'queries.jsonl'), 'keyword')
    assert report['metrics']['recall@5'] >= 0.9 and report['index']['bytes'] > 0
    again = _GENERATOR.make_eval_scale(tmp_path / 'again', documents=12, queries=6)
    assert (tmp_path / 'again' / 'queries.jsonl').read_text() == (
        tmp_path / 'scale' / 'queries.jsonl'
    ).read_text()
    assert again['queries'] == info['queries']
