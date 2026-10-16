"""Context engine phase 1 retrieval core: index schema 3, fusion, vectors, embeddings and absence."""

import json
import sqlite3

import pytest

from byax.errors import ByaxError
from byax.libraries import CHUNKING, SCHEMA, LibraryIndex, LibraryRegistry
from byax.model import new_document
from byax.ranking import Hit, IdentityReranker, dedupe, mmr, overlapping, rrf
from byax.retrieval import absence_claim, describe_absence, retrieve_context, search_library, verify_absence
from byax.vector_store import decode, encode, open_store


class Embedder:
    """Deterministic local embedder: one axis per theme word, plus a constant."""

    def __init__(self, identity='fixture-v1', max_batch=32, failures=None):
        self.model_identity = identity
        self.dimension = 3
        self.max_input_chars = 5000
        self.max_batch = max_batch
        self.calls = []
        self.failures = list(failures or [])

    def embed(self, texts):
        self.calls.append(list(texts))
        if self.failures:
            raise self.failures.pop(0)
        return [
            [
                float(sum(t.lower().count(w) for w in ('moon', 'lunar'))),
                float(sum(t.lower().count(w) for w in ('sea', 'ocean'))),
                1.0,
            ]
            for t in texts
        ]


@pytest.fixture
def make_library(tmp_path):
    registry = LibraryRegistry(tmp_path / 'config')
    cache = tmp_path / 'cache'

    def make(name, files, **options):
        root = tmp_path / name
        root.mkdir()
        for relative, text in files.items():
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_text(text, encoding='utf-8')
        library = registry.create(name, [str(root)], **options)
        index = LibraryIndex(library, cache_dir=cache)
        index.refresh()
        return library, index, root

    make.registry = registry
    make.cache = cache
    return make


def attach(doc, *libraries, role='supplementary'):
    doc['settings']['libraries'] = [
        {'library_id': lib['library_id'], 'name': lib['name'], 'role': role} for lib in libraries
    ]
    return doc


# Index schema 3 ---------------------------------------------------------------------------------


def test_schema_records_heading_fingerprint_and_ranks_headings_above_text(make_library):
    _, index, _ = make_library(
        'Notes',
        {
            'a.md': '# Lanterns\n\nThe hall was dark.',
            'b.md': '# Kitchen\n\nShe lit a lantern by the stove.',
        },
    )
    with index._db() as db:
        meta = dict(db.execute('SELECT key, value FROM meta').fetchall())
        rows = db.execute('SELECT heading, fingerprint FROM chunks ORDER BY rowid').fetchall()
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert meta['schema'] == str(SCHEMA) == '3'
    assert json.loads(meta['chunking']) == CHUNKING and CHUNKING['version'] == 3
    assert {'search', 'search_tri', 'vocab', 'chunks', 'contexts'} <= tables
    assert ('Lanterns',) in [(r[0],) for r in rows] and all(len(r[1]) == 64 for r in rows)
    result = index.search('lantern')
    # Heading match outranks a body match, and the body under the heading inherits it.
    # A heading's paragraphs are one chunk now (chunker v3), so the second hit is the body match.
    assert [h['path'] for h in result['results'][:2]] == ['a.md', 'b.md']
    assert result['results'][0]['heading'] == 'Lanterns'
    assert result['passes'] == ['porter', 'trigram'] and result['method'] == 'keyword'
    assert index.search('heading:kitchen')['results'][0]['path'] == 'b.md'


def test_trigram_pass_runs_only_when_porter_finds_fewer_than_k(make_library):
    _, index, _ = make_library('Mid', {'a.txt': 'A copperplate engraving.', 'b.txt': 'Copper wire.'})
    short = index.search('opper')
    assert [h['match'] for h in short['results']] == ['trigram', 'trigram']
    assert short['passes'] == ['porter', 'trigram']
    enough = index.search('copper', limit=1)
    assert enough['passes'] == ['porter'] and enough['results'][0]['match'] == 'porter'
    assert index.search('copperpl*')['results'][0]['path'] == 'a.txt'


def test_did_you_mean_is_reported_and_expansion_is_opt_in(make_library):
    _, index, _ = make_library('Austen', {'p.txt': 'Elizabeth walked to Pemberley in the rain.'})
    plain = index.search('Elizabth')
    assert plain['results'] == []
    assert plain['suggestions']['elizabth'][0]['term'] == 'elizabeth'
    expanded = index.search('Elizabth', expand=True)
    assert expanded['results'] and expanded['query']['expanded'] == {'Elizabth': ['elizabeth']}


def test_schema_one_index_is_marked_stale_kept_searchable_and_rebuilt(make_library, tmp_path):
    library, index, root = make_library('Old', {'n.txt': 'A copper moon.'})
    path = index.path
    path.unlink()
    for suffix in ('-wal', '-shm'):
        (path.parent / (path.name + suffix)).unlink(missing_ok=True)
    # Recreate the 0.3.0 layout by hand: no schema record, no heading, plain FTS.
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE documents (document_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL);
        CREATE TABLE files (path TEXT PRIMARY KEY, root TEXT, relative_path TEXT, size INTEGER,
            mtime INTEGER, document_id TEXT, status TEXT, error TEXT, lease TEXT, lease_until REAL);
        CREATE TABLE chunks (chunk_id TEXT PRIMARY KEY, document_id TEXT, ordinal INTEGER,
            locator TEXT, text TEXT);
        CREATE VIRTUAL TABLE search USING fts5(chunk_id UNINDEXED, text);
        CREATE TABLE vectors (chunk_id TEXT PRIMARY KEY, model TEXT, vector TEXT);
    """)
    stat = (root / 'n.txt').stat()
    db.execute(
        "INSERT INTO meta VALUES ('chunking', ?)",
        (json.dumps({'version': 1, 'chars': 1800, 'overlap': 160}),),
    )
    db.execute("INSERT INTO documents VALUES ('doc_old', 'hash')")
    db.execute(
        "INSERT INTO files VALUES (?, ?, 'n.txt', ?, ?, 'doc_old', 'ready', NULL, NULL, NULL)",
        (str(root / 'n.txt'), str(root), stat.st_size, stat.st_mtime_ns),
    )
    db.execute(
        "INSERT INTO chunks VALUES ('chunk_old', 'doc_old', 0, ?, 'A copper moon.')",
        (json.dumps({'ordinal': 0, 'start': 0, 'end': 14}),),
    )
    db.execute("INSERT INTO search VALUES ('chunk_old', 'A copper moon.')")
    db.execute("INSERT INTO vectors VALUES ('chunk_old', 'old', '[1]')")
    db.commit()
    db.close()
    upgraded = LibraryIndex(library, cache_dir=make_library.cache)
    status = upgraded.status()
    assert status['schema'] == SCHEMA and status['stale']
    stale = upgraded.search('copper')['results']
    assert stale[0]['chunk_id'] == 'chunk_old' and stale[0]['stale']
    with upgraded._db() as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='vectors'").fetchone()
    upgraded.refresh()
    fresh = upgraded.search('copper')['results']
    assert len(fresh) == 1 and fresh[0]['chunk_id'] != 'chunk_old' and not fresh[0]['stale']
    assert fresh[0]['fingerprint']


# Ranking ----------------------------------------------------------------------------------------


def _hits(library, count, prefix='c'):
    return [{'chunk_id': f'{prefix}{i}', 'library_id': library, 'text': f'text {i}'} for i in range(count)]


def test_rrf_interleaves_libraries_and_records_ranks():
    fused = rrf([_hits('A', 5, 'a'), _hits('B', 2, 'b')], names=['A', 'B'])
    assert [h.chunk_id for h in fused[:4]] == ['a0', 'b0', 'a1', 'b1']
    assert fused[0].ranks == {'A': 1} and fused[0].score == pytest.approx(1 / 61)
    both = rrf(
        [[{'chunk_id': 'x', 'library_id': 'L'}], [{'chunk_id': 'y'}, {'chunk_id': 'x', 'library_id': 'L'}]]
    )
    assert both[0].chunk_id == 'x' and both[0].ranks == {'0': 1, '1': 2}
    with pytest.raises(ValueError):
        rrf([], k=0)


def test_dedupe_by_document_and_overlapping_locator():
    first = Hit('c1', 'A', 'd1', 'x', {'page': 2, 'start': 0, 'end': 100}, content_hash='h')
    copy_in_other_library = Hit('c9', 'B', 'd9', 'x', {'page': 2, 'start': 0, 'end': 100}, content_hash='h')
    split_overlap = Hit('c2', 'A', 'd1', 'y', {'page': 2, 'start': 90, 'end': 190}, content_hash='h')
    other_page = Hit('c3', 'A', 'd1', 'z', {'page': 3, 'start': 0, 'end': 100}, content_hash='h')
    assert overlapping(first, copy_in_other_library)
    assert not overlapping(first, split_overlap)
    kept, dropped = dedupe([first, copy_in_other_library, split_overlap, other_page])
    assert [h.chunk_id for h in kept] == ['c1', 'c2', 'c3']
    assert dropped == [
        {
            'chunk_id': 'c9',
            'library_id': 'B',
            'duplicate_of': 'c1',
            'reason': 'Duplicate passage from the same document and location',
        }
    ]


def test_mmr_prefers_diverse_passages_and_reranker_seam_is_identity():
    hits = [
        Hit('a', text='copper moon rises'),
        Hit('b', text='copper moon rises again'),
        Hit('c', text='the tide in the harbour'),
    ]
    assert [h.chunk_id for h in mmr(hits, 2, lambda_=0.5)] == ['a', 'c']
    assert [h.chunk_id for h in mmr(hits, 2, lambda_=1.0)] == ['a', 'b']
    assert IdentityReranker().rerank('q', hits) == hits


# Vector store -----------------------------------------------------------------------------------


def test_vector_store_float32_coverage_signatures_and_cosine(make_library):
    _, index, _ = make_library('Vec', {'a.txt': 'one', 'b.txt': 'two', 'c.txt': 'three'})
    assert decode(encode([0.5, -1.25])) == (0.5, -1.25)
    store = open_store(index)
    with index._db() as db:
        chunks = db.execute('SELECT chunk_id, fingerprint FROM chunks ORDER BY rowid').fetchall()
    store.upsert('sig', [(chunks[0][0], chunks[0][1], [1, 0]), (chunks[1][0], chunks[1][1], [0.6, 0.8])])
    store.upsert('old', [(chunks[2][0], chunks[2][1], [1, 0])])
    assert store.coverage('sig') == {'embedded': 2, 'total': 3, 'ratio': 0.6667, 'stale_vectors': 1}
    assert [c for c, _ in store.search('sig', [1, 0], limit=5)] == [chunks[0][0], chunks[1][0]]
    assert store.search('sig', [1, 0, 0], limit=5) == []  # dimensions never mix
    assert store.discard_other_signatures('sig') == 1 and store.signatures() == {'sig': 2}
    with pytest.raises(ByaxError):
        open_store(index, backend='lancedb')


# Embeddings -------------------------------------------------------------------------------------


def test_rebuild_is_incremental_batched_and_reports_counts(make_library):
    _, index, root = make_library(
        'Inc', {'a1.txt': 'moon', 'a2.txt': 'sea', 'a3.txt': 'moon sea', 'b.txt': 'ocean'}
    )
    provider = Embedder(max_batch=2)
    first = index.rebuild_vectors(provider=provider)
    assert (first['done'], first['skipped'], first['failed'], first['total']) == (4, 0, 0, 4)
    assert [len(batch) for batch in provider.calls] == [2, 2]
    assert first['embedding_state'] == 'ready' and first['coverage'] == 1.0
    again = index.rebuild_vectors(provider=provider)
    assert (again['done'], again['skipped']) == (0, 4) and len(provider.calls) == 2
    (root / 'b.txt').write_text('lunar ocean', encoding='utf-8')
    index.refresh()
    changed = index.rebuild_vectors(provider=provider)
    assert (changed['done'], changed['skipped']) == (1, 3)
    assert provider.calls[-1] == ['lunar ocean']


def test_rebuild_retries_unsent_requests_counts_failures_and_cancels(make_library):
    _, index, _ = make_library('Retry', {'a.txt': 'moon', 'b.txt': 'sea'})
    unsent = ByaxError('provider_failure', 'not sent', request_sent=False)
    sleeps = []
    provider = Embedder(max_batch=1, failures=[unsent])
    from byax.embeddings import rebuild_vectors

    result = rebuild_vectors(index, provider=provider, sleep=sleeps.append)
    assert result['done'] == 2 and result['failed'] == 0 and sleeps
    _, index2, _ = make_library('Uncertain', {'a.txt': 'moon', 'b.txt': 'sea'})
    uncertain = ByaxError('uncertain_external_outcome', 'timed out')
    provider = Embedder(max_batch=1, failures=[uncertain])
    result = rebuild_vectors(index2, provider=provider, sleep=sleeps.append)
    # An uncertain send is never repeated automatically; the batch is counted as failed.
    assert (result['done'], result['failed']) == (1, 1) and result['embedding_state'] == 'partial'
    assert result['errors'][0]['code'] == 'uncertain_external_outcome'

    class Cancel:
        def is_set(self):
            return len(provider.calls) >= 1

    _, index3, _ = make_library('Cancel', {'a.txt': 'moon', 'b.txt': 'sea', 'c.txt': 'ocean'})
    provider = Embedder(max_batch=1)
    result = rebuild_vectors(index3, provider=provider, cancel_event=Cancel())
    assert result['cancelled'] and result['done'] == 1


def test_changed_signature_marks_vectors_stale_and_never_mixes(make_library):
    _, index, _ = make_library('Sig', {'a.txt': 'moon', 'b.txt': 'sea'})
    old = Embedder('model-a')
    index.rebuild_vectors(provider=old)
    new = Embedder('model-b')
    result = index.hybrid_search('lunar', provider=new)
    assert result['embedding_state'] == 'stale' and result['method'] == 'keyword' and result['notice']
    assert not new.calls  # the query is not embedded against incompatible vectors
    rebuilt = index.rebuild_vectors(provider=new)
    assert rebuilt['discarded_stale'] == 2 and rebuilt['done'] == 2
    assert open_store(index).signatures() == {rebuilt['model_fingerprint']: 2}


def test_partial_vectors_are_used_with_a_visible_notice(make_library):
    _, index, root = make_library('Part', {'a.txt': 'A lunar eclipse.'})
    provider = Embedder()
    index.rebuild_vectors(provider=provider)
    (root / 'b.txt').write_text('The open sea.', encoding='utf-8')
    index.refresh()
    result = index.hybrid_search('moon', provider=provider)
    assert result['method'] == 'hybrid' and result['embedding_state'] == 'partial'
    assert result['coverage'] == 0.5 and '1 of 2 passages' in result['notice']
    assert result['results'][0]['text'] == 'A lunar eclipse.'
    assert result['results'][0]['ranks'] == {'dense': 1}
    assert index.status()['embedding_coverage'] == {}  # status uses the configured provider only


# retrieve_context -------------------------------------------------------------------------------


def test_global_fusion_fixes_first_library_starvation(make_library):
    first, _, _ = make_library('Big', {f'{i}.txt': f'The copper key number {i}.' for i in range(12)})
    second, _, _ = make_library('Small', {'only.txt': 'A copper key in the drawer.'})
    doc = attach(new_document(), first, second)
    kwargs = {'registry': make_library.registry, 'cache_dir': make_library.cache}
    result = retrieve_context(doc, 'copper key', limit=3, **kwargs)
    libraries = [p['library_id'] for p in result['included']]
    assert second['library_id'] in libraries, 'the second library must not be starved'
    assert result['fusion'] == {'method': 'rrf', 'k': 60, 'libraries': 2}
    assert [p['fused_rank'] for p in result['included']] == [1, 2, 3]
    assert {lib['name']: lib['method'] for lib in result['libraries']} == {
        'Big': 'keyword',
        'Small': 'keyword',
    }


def test_method_auto_reports_hybrid_or_keyword_per_library(make_library):
    embedded, index, _ = make_library('Embedded', {'a.txt': 'A lunar eclipse.', 'tides.txt': 'Tides.'})
    plain, _, _ = make_library('Plain', {'b.txt': 'The moon is copper.'})
    provider = Embedder()
    index.rebuild_vectors(provider=provider)
    doc = attach(new_document(), embedded, plain)
    kwargs = {'registry': make_library.registry, 'cache_dir': make_library.cache}
    result = retrieve_context(doc, 'moon', embedding_provider={embedded['library_id']: provider}, **kwargs)
    report = {lib['name']: (lib['method'], lib['embedding_state']) for lib in result['libraries']}
    assert report == {'Embedded': ('hybrid', 'ready'), 'Plain': ('keyword', 'disabled')}
    assert 'A lunar eclipse.' in [p['text'] for p in result['included']]
    keyword = retrieve_context(doc, 'moon', method='keyword', embedding_provider=provider, **kwargs)
    assert {lib['method'] for lib in keyword['libraries']} == {'keyword'}
    assert 'A lunar eclipse.' not in [p['text'] for p in keyword['included']]
    with pytest.raises(ByaxError):
        retrieve_context(doc, 'moon', method='semantic', **kwargs)


def test_auto_never_escalates_embedding_consent(make_library, tmp_path, monkeypatch):
    from byax.providers import ProviderConfig, ProviderRegistry

    providers = ProviderRegistry(tmp_path / 'config')
    providers.save(ProviderConfig('host', 'openai', 'embed-model'))
    monkeypatch.setattr('byax.providers.ProviderRegistry', lambda *a, **k: providers)
    calls = []
    monkeypatch.setattr(providers, '_request', lambda *a, **k: calls.append(k) or {})
    library, index, _ = make_library('Remote', {'a.txt': 'A copper moon.'})
    make_library.registry.configure_embedding(library['library_id'], {'provider': 'host', 'dimension': 3})
    library = make_library.registry.get(library['library_id'])
    index = LibraryIndex(library, cache_dir=make_library.cache)
    # Pretend a consented rebuild happened earlier, with this provider's signature.
    from byax.embeddings import ConfiguredEmbeddingProvider, fingerprint

    signature = fingerprint(ConfiguredEmbeddingProvider(library['embedding'], registry=providers), CHUNKING)
    with index._db() as db:
        chunks = db.execute('SELECT chunk_id, fingerprint FROM chunks').fetchall()
    open_store(index).upsert(signature, [(c[0], c[1], [1, 0, 0]) for c in chunks])
    result = search_library(index, 'copper', method='auto')
    assert result['method'] == 'keyword' and 'consent' in result['notice']
    assert calls == []  # no query was sent to the remote embedder without consent
    assert result['results'][0]['text'] == 'A copper moon.'


def test_pinned_items_are_excluded_and_reported_as_the_pinned_tier(make_library):
    library, index, _ = make_library('Pin', {'a.txt': 'Copper one.', 'b.txt': 'Copper two.'})
    hits = index.search('copper')['results']
    doc = attach(new_document(), library)
    kwargs = {'registry': make_library.registry, 'cache_dir': make_library.cache}
    result = retrieve_context(doc, 'copper', pinned=[hits[0]['document_id']], **kwargs)
    assert [p['chunk_id'] for p in result['included']] == [hits[1]['chunk_id']]
    assert result['pinned'] == [
        {
            'id': hits[0]['document_id'],
            'tier': 'pinned',
            'reason': 'Attached in full to this request; excluded from retrieved passages',
            'excluded_chunks': [hits[0]['chunk_id']],
        }
    ]
    assert hits[0]['chunk_id'] not in result['allowed_citation_ids']
    with pytest.raises(ByaxError):
        retrieve_context(doc, 'copper', pinned=[3], **kwargs)


def test_policy_checks_and_duplicates_survive_fusion(make_library):
    shared = {'same.txt': 'The copper moon rose.'}
    first, _, _ = make_library('One', shared, retrieval_policy='local_only')
    second, _, _ = make_library('Two', shared)
    doc = attach(new_document(), first, second)
    kwargs = {'registry': make_library.registry, 'cache_dir': make_library.cache}
    local = retrieve_context(doc, 'copper', **kwargs)
    assert len(local['included']) == 1
    assert any(o.get('duplicate_of') for o in local['omitted'])
    remote = retrieve_context(doc, 'copper', remote=True, **kwargs)
    assert {p['library_id'] for p in remote['included']} == {second['library_id']}
    assert {'library_id': first['library_id'], 'reason': 'Library is local_only'} in remote['omitted']


# Verified absence -------------------------------------------------------------------------------


def test_verify_absence_searches_every_library_and_the_manuscript(make_library):
    first, _, _ = make_library('Letters', {'l.txt': 'Mr. Wickham wrote from Brighton.'})
    second, _, _ = make_library(
        'Notes', {'n.md': '# Places\n\nRosings Park is grand.'}, retrieval_policy='local_only'
    )
    doc = attach(new_document(text='Chapter one mentions Netherfield.'), first, second)
    report = verify_absence(
        ['Wickham', 'ings Pa', 'Netherfield', 'Pemberley'],
        manuscript=doc,
        registry=make_library.registry,
        cache_dir=make_library.cache,
    )
    assert report['found'] == ['Wickham', 'ings Pa', 'Netherfield'] and report['absent'] == ['Pemberley']
    assert report['exhaustive'] and report['method'] == 'lexical'
    assert [lib['name'] for lib in report['searched']['libraries']] == ['Letters', 'Notes']
    assert report['searched']['libraries'][0]['passes'] == ['porter', 'trigram']
    assert report['searched']['manuscript'] == {'document_id': doc['id'], 'objects': 1}
    mid_word = next(m for m in report['matches'] if m['term'] == 'ings Pa')
    assert mid_word['passes'] == ['trigram'] and mid_word['retrieval_policy'] == 'local_only'
    assert next(m for m in report['matches'] if m['term'] == 'Netherfield')['scope'] == 'manuscript'
    text = describe_absence(report)
    assert 'Literal matches exist for: Wickham' in text and 'No literal match for: Pemberley' in text


def test_verify_absence_respects_profile_and_reports_unreadable_libraries(make_library):
    open_library, _, _ = make_library('Open', {'o.txt': 'Pemberley'})
    doc = attach(new_document(), open_library, role='open')
    doc['settings']['libraries'].append({'library_id': 'lib_' + '0' * 32, 'name': 'Gone'})
    kwargs = {'registry': make_library.registry, 'cache_dir': make_library.cache}
    exclusive = verify_absence(
        'Pemberley', manuscript=doc, profile={'source_authority': 'exclusive'}, **kwargs
    )
    assert exclusive['absent'] == ['Pemberley'] and not exclusive['exhaustive']
    assert [o['library_id'] for o in exclusive['omitted']] == [open_library['library_id'], 'lib_' + '0' * 32]
    with pytest.raises(ByaxError):
        verify_absence([], manuscript=doc, **kwargs)


def test_absence_claims_are_recognised():
    assert absence_claim('That name is not mentioned in your sources.')
    assert absence_claim("I couldn't find any reference to Pemberley.")
    assert absence_claim('There is no mention of a lantern.')
    assert not absence_claim('Elizabeth visits Pemberley in chapter 43.')
