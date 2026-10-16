"""On-device embedder and rerankers, with tiny fake ONNX sessions and a real tokenizers vocabulary.

Real model files are exercised only when ``BYAX_REAL_MODELS_DIR`` points at verified downloads
(see ``test_real_models``); everything else here is deterministic and offline.
"""

import json
import os
import threading
from types import SimpleNamespace

import pytest

pytest.importorskip('onnxruntime')
pytest.importorskip('tokenizers')

import numpy as np
from tokenizers import Tokenizer, models, pre_tokenizers, processors

from byax import embeddings, local_inference, local_models
from byax.cli import execute
from byax.errors import ByaxError
from byax.libraries import LibraryIndex, LibraryRegistry
from byax.ranking import Hit
from byax.retrieval import resolve_reranker, search_library

WORDS = [
    'alpha',
    'beta',
    'gamma',
    'delta',
    'river',
    'bank',
    'money',
    'loan',
    'water',
    'fish',
    'sunrise',
    'mountain',
    'tea',
    'coffee',
    'garden',
    'stone',
    'lighthouse',
    'harbour',
    'storm',
    'sail',
    'anchor',
    'captain',
    'ship',
    'wave',
    'north',
    'south',
]
VOCAB = {'[PAD]': 0, '[CLS]': 1, '[SEP]': 2, '[UNK]': 3, '[Q]': 4, '[D]': 5, '.': 6}
VOCAB.update({word: 7 + i for i, word in enumerate(WORDS)})
DIM = len(VOCAB)


def make_tokenizer(template=False):
    tokenizer = Tokenizer(models.WordLevel(VOCAB, unk_token='[UNK]'))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    if template:
        tokenizer.post_processor = processors.TemplateProcessing(
            single='[CLS] $A [SEP]',
            pair='[CLS] $A [SEP] $B:1 [SEP]:1',
            special_tokens=[('[CLS]', 1), ('[SEP]', 2)],
        )
    return tokenizer


class FakeInput:
    def __init__(self, name, shape=None):
        self.name, self.shape = name, shape or ['b', 's']


class FakeCausalSession:
    """Mimics the Qwen export: past_key_values inputs, last_hidden_state out (fixed random table)."""

    def __init__(self, width=8):
        self.table = np.random.default_rng(7).normal(size=(DIM, width)).astype(np.float32)
        self.calls = []

    def get_inputs(self):
        return [
            FakeInput('input_ids'),
            FakeInput('attention_mask'),
            FakeInput('position_ids'),
            FakeInput('past_key_values.0.key', ['b', 2, 'p', 4]),
            FakeInput('past_key_values.0.value', ['b', 2, 'p', 4]),
        ]

    def run(self, names, feeds):
        assert names == ['last_hidden_state']
        past = feeds['past_key_values.0.key']
        assert past.shape[2] == 0 and past.shape[1:] == (2, 0, 4)
        assert feeds['position_ids'].shape == feeds['input_ids'].shape
        self.calls.append(feeds['input_ids'].copy())
        ids = feeds['input_ids']
        # A tiny causal-ish mix so the last token depends on the earlier ones, not on padding.
        hidden = self.table[ids]
        mixed = np.cumsum(hidden, axis=1) / np.arange(1, ids.shape[1] + 1)[None, :, None]
        return [mixed.astype(np.float32)]


def fake_model(session, template=False, parameters=None, config=None):
    return SimpleNamespace(
        session=session,
        tokenizer=make_tokenizer(template),
        lock=threading.Lock(),
        parameters=dict(parameters or {}),
        config=config or {},
    )


def tiny_catalogue():
    files = [
        {
            'path': 'model.onnx',
            'role': 'model',
            'size': 5,
            'sha256': '0' * 64,
            'url': 'https://example.invalid/m',
            'mirror_asset': 'tiny-embedder__model.onnx',
        }
    ]
    models_ = [
        {
            'name': 'tiny-embedder',
            'kind': 'embedder',
            'version': '1',
            'licence': {'spdx': 'Apache-2.0'},
            'runtime': {
                'native_dimension': 8,
                'dimension': 6,
                'max_tokens': 16,
                'query_instruction': 'find words',
            },
            'files': files,
        },
        {
            'name': 'tiny-colbert',
            'kind': 'reranker',
            'version': '1',
            'licence': {'spdx': 'Apache-2.0'},
            'runtime': {
                'family': 'colbert',
                'max_query_tokens': 8,
                'max_document_tokens': 32,
                'query_prefix_id': 4,
                'document_prefix_id': 5,
            },
            'files': files,
        },
        {
            'name': 'tiny-cross',
            'kind': 'reranker',
            'version': '1',
            'licence': {'spdx': 'Apache-2.0'},
            'runtime': {'family': 'cross-encoder', 'max_tokens': 64, 'pad_token_id': 0},
            'files': files,
        },
    ]
    return local_models.validate_catalogue(
        {
            'schema': 1,
            'mirror': {'base_url': 'https://example.invalid', 'populated': False},
            'models': models_,
        }
    )


@pytest.fixture
def catalogue(monkeypatch, tmp_path):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setenv('BYAX_MODELS_DIR', str(tmp_path / 'models'))
    value = tiny_catalogue()
    monkeypatch.setattr(local_models, 'load_catalogue', lambda path=None: value)
    return value


@pytest.fixture
def fake_embedder(monkeypatch, catalogue):
    session = FakeCausalSession()
    model = fake_model(session, parameters={})
    monkeypatch.setattr(local_inference, 'load', lambda name, **kwargs: model)
    return session


def test_embedder_pools_the_last_real_token_normalizes_and_truncates(fake_embedder, catalogue):
    provider = local_inference.LocalEmbeddingProvider('tiny-embedder', catalogue=catalogue)
    assert provider.dimension == 6 and provider.remote is False
    assert json.loads(provider.model_identity) == ['local', 'tiny-embedder', '1', 6, 16]
    short = provider.embed(['river bank'])[0]
    batch = provider.embed(['river bank', 'alpha beta gamma delta river bank money loan'])
    assert len(short) == 6 and abs(sum(x * x for x in short) - 1) < 1e-5
    # Padding must not change a text's vector: batch row 0 equals the single-text result.
    assert np.allclose(short, batch[0], atol=1e-6)
    assert not np.allclose(batch[0], batch[1])
    embeddings.validate_vectors(batch, 2, 6)
    assert provider.embed([]) == []


def test_embedder_input_validation(fake_embedder, catalogue):
    provider = local_inference.LocalEmbeddingProvider('tiny-embedder', catalogue=catalogue)
    with pytest.raises(ByaxError) as caught:
        provider.embed(['x' * (provider.max_input_chars + 1)])
    assert caught.value.code == 'limit_exceeded'
    with pytest.raises(ByaxError):
        local_inference.LocalEmbeddingProvider('tiny-embedder', dimension=99, catalogue=catalogue)
    with pytest.raises(ByaxError):
        local_inference.LocalEmbeddingProvider('tiny-colbert', catalogue=catalogue)


def test_query_embedding_uses_the_instruction_prefix_and_caches(fake_embedder, catalogue):
    provider = local_inference.LocalEmbeddingProvider('tiny-embedder', catalogue=catalogue)
    first = provider.embed_query('river')
    assert provider.embed_query('river') == first
    # 'Instruct: find words\nQuery:river' is tokenized, so the query differs from the bare document.
    assert first != provider.embed(['river'])[0]
    assert len(fake_embedder.calls) == 2


def test_provider_settings_route_local_aliases(fake_embedder, catalogue):
    settings = embeddings.validate_config({'provider': 'local:tiny-embedder', 'dimension': 6})
    provider = embeddings.provider_for_settings(settings)
    assert isinstance(provider, local_inference.LocalEmbeddingProvider)
    with pytest.raises(ByaxError):
        embeddings.validate_config({'provider': 'local:tiny-colbert', 'dimension': 6})
    with pytest.raises(ByaxError):
        embeddings.validate_config({'provider': 'local:nope', 'dimension': 6})
    info = embeddings.embedder_locality({'embedding': settings})
    assert info['locality'] == 'local' and 'stays on this computer' in info['label']


def write_library(tmp_path, documents):
    root = tmp_path / 'lib'
    root.mkdir()
    for name, text in documents.items():
        (root / name).write_text(text, encoding='utf-8')
    return root


def test_local_library_gets_semantic_search_without_ollama(fake_embedder, catalogue, tmp_path):
    root = write_library(
        tmp_path,
        {
            'water.txt': 'river water fish\n\nstorm wave sail ship',
            'tea.txt': 'tea coffee garden stone',
            'harbour.txt': 'lighthouse harbour anchor captain',
        },
    )
    registry = LibraryRegistry(tmp_path / 'cfg')
    library = registry.create('Local', [str(root)], retrieval_policy='local_only')
    registry.configure_embedding(library['library_id'], {'provider': 'local:tiny-embedder', 'dimension': 6})
    library = registry.get(library['library_id'])
    index = LibraryIndex(library, cache_dir=tmp_path / 'cache')
    index.refresh()
    assert index.status()['embeddings'] == 'rebuild_required'
    preview = index.embedding_preview()
    assert preview['locality'] == 'local' and preview['remote'] is False
    result = index.rebuild_vectors()
    assert result['failed'] == 0 and result['done'] >= 3 and result['embedding_state'] == 'ready'
    found = search_library(index, 'ship wave', method='hybrid', limit=5, rerank='fast')
    assert found['method'] == 'hybrid' and found['results']
    assert found['results'][0]['path'] == 'water.txt'
    assert 'dense' in found['results'][0]['ranks']
    # A local_only library may use the on-device embedder: nothing is remote.
    assert library['retrieval_policy'] == 'local_only'


def install_fake_files(catalogue, name, directory):
    """Create a model directory that passes ready_path (catalogue hashes are fixtures)."""
    target = directory / name
    target.mkdir(parents=True)
    entry = local_models.get_model(name, catalogue)
    body = b'12345'
    import hashlib

    for item in entry['files']:
        item['size'], item['sha256'] = len(body), hashlib.sha256(body).hexdigest()
        (target / item['path']).write_bytes(body)
    local_models._write_manifest(entry, target, 'upstream')


def test_semantic_enable_requires_confirmation_and_an_installed_model(
    fake_embedder, catalogue, tmp_path, monkeypatch
):
    import platformdirs

    from byax import libraries
    from byax.model import new_document
    from byax.storage import save_document

    monkeypatch.setattr(libraries, '_default_dir', lambda: tmp_path / 'libconfig')
    monkeypatch.setattr(platformdirs, 'user_cache_path', lambda *a, **k: tmp_path / 'cache')
    monkeypatch.setattr(platformdirs, 'user_state_path', lambda *a, **k: tmp_path / 'state')
    root = write_library(tmp_path, {'a.txt': 'alpha beta gamma delta', 'b.txt': 'river bank money loan'})
    document = tmp_path / 'doc.byax'
    save_document(new_document(), document)
    execute(
        'library', {'path': str(document), 'action': 'create', 'input': {'name': 'L', 'roots': [str(root)]}}
    )
    library_id = LibraryRegistry().list()[0]['library_id']
    execute('library', {'path': str(document), 'action': 'refresh', 'library_id': library_id})
    pending = execute(
        'library',
        {
            'path': str(document),
            'action': 'semantic-enable',
            'library_id': library_id,
            'model': 'tiny-embedder',
        },
    )
    assert pending['confirmation_required'] is True
    assert pending['prompt'] == 'Enable on-device semantic search? Nothing leaves this computer.'
    assert LibraryRegistry().get(library_id)['embedding'] is None
    with pytest.raises(ByaxError) as caught:
        execute(
            'library',
            {
                'path': str(document),
                'action': 'semantic-enable',
                'library_id': library_id,
                'model': 'tiny-embedder',
                'confirm': True,
            },
        )
    assert caught.value.code == 'model_missing'
    assert LibraryRegistry().get(library_id)['embedding'] is None
    install_fake_files(catalogue, 'tiny-embedder', tmp_path / 'models')
    enabled = execute(
        'library',
        {
            'path': str(document),
            'action': 'semantic-enable',
            'library_id': library_id,
            'model': 'tiny-embedder',
            'confirm': True,
        },
    )
    assert enabled['library']['embedding'] == {
        'provider': 'local:tiny-embedder',
        'dimension': 6,
        'max_input_chars': 8000,
        'max_batch': 32,
    }
    assert enabled['rebuild']['done'] >= 2 and enabled['rebuild']['embedding_state'] == 'ready'
    status = execute('library', {'path': str(document), 'action': 'status', 'library_id': library_id})
    assert status['embedder']['locality'] == 'local' and status['embeddings'] == 'ready'
    disabled = execute(
        'library', {'path': str(document), 'action': 'semantic-disable', 'library_id': library_id}
    )
    assert disabled['library']['embedding'] is None and disabled['vectors_removed'] >= 2


def test_mcp_cannot_enable_semantic_search():
    from byax.transport_policy import check_provider_admin

    for action in ('semantic-enable', 'semantic-disable', 'embed', 'embedding-configure'):
        with pytest.raises(ByaxError):
            check_provider_admin('library', {'action': action})


class FakeColbertSession:
    """One-hot token embeddings: MaxSim counts query tokens (after the [Q] marker) found in the passage."""

    def get_inputs(self):
        return [FakeInput('input_ids'), FakeInput('attention_mask')]

    def run(self, names, feeds):
        ids = feeds['input_ids']
        out = np.zeros((*ids.shape, DIM), dtype=np.float32)
        for row in range(ids.shape[0]):
            for column in range(ids.shape[1]):
                out[row, column, ids[row, column]] = 1.0
        return [out]


class FakeCrossSession:
    """Logit = number of query tokens that also occur in the passage."""

    def get_inputs(self):
        return [FakeInput('input_ids'), FakeInput('attention_mask'), FakeInput('token_type_ids')]

    def run(self, names, feeds):
        ids, types = feeds['input_ids'], feeds['token_type_ids']
        logits = []
        for row in range(ids.shape[0]):
            query = {t for t, k in zip(ids[row], types[row], strict=True) if k == 0 and t > 2}
            passage = {t for t, k in zip(ids[row], types[row], strict=True) if k == 1 and t > 2}
            logits.append([float(len(query & passage))])
        return [np.array(logits, dtype=np.float32)]


def hits_for(*texts):
    return [
        Hit(chunk_id=f'c{i}', text=text, payload={'chunk_id': f'c{i}', 'text': text})
        for i, text in enumerate(texts)
    ]


def test_colbert_reranker_orders_by_maxsim_and_records_scores(monkeypatch, catalogue):
    model = fake_model(
        FakeColbertSession(),
        template=True,
        parameters=catalogue['models'][1]['runtime'],
        config={'skiplist_words': ['.']},
    )
    monkeypatch.setattr(local_inference, 'load', lambda name, **kwargs: model)
    reranker = local_inference.load_reranker('tiny-colbert', catalogue=catalogue)
    hits = hits_for('tea coffee garden', 'river bank money loan', 'river water fish bank', 'stone . .')
    ranked = reranker.rerank('river bank loan', hits)
    assert [h.chunk_id for h in ranked][:2] == ['c1', 'c2']
    assert ranked[0].payload['rerank_score'] > ranked[1].payload['rerank_score']
    assert ranked[0].ranks['rerank'] == 1 and ranked[0].payload['reranker'] == 'local:tiny-colbert'
    # Equal scores keep the incoming order (stable), and the originals are not mutated.
    ranked = reranker.rerank('lighthouse', hits)
    assert [h.chunk_id for h in ranked] == ['c0', 'c1', 'c2', 'c3']
    assert 'rerank' not in hits[0].ranks
    assert reranker.rerank('river', hits[:1])[0].chunk_id == 'c0'


def test_cross_encoder_reranker_scores_pairs(monkeypatch, catalogue):
    model = fake_model(FakeCrossSession(), template=True, parameters=catalogue['models'][2]['runtime'])
    monkeypatch.setattr(local_inference, 'load', lambda name, **kwargs: model)
    reranker = local_inference.load_reranker('tiny-cross', catalogue=catalogue)
    assert isinstance(reranker, local_inference.CrossEncoderReranker)
    hits = hits_for('tea coffee', 'ship storm sail', 'storm wave', 'harbour')
    ranked = reranker.rerank('storm ship wave', hits)
    assert [h.chunk_id for h in ranked][:2] == ['c1', 'c2']
    assert ranked[0].payload['rerank_score'] == 2.0


def test_fast_is_the_default_and_accurate_falls_back_with_a_notice(catalogue):
    reranker, info = resolve_reranker()
    assert reranker is None and info['mode'] == 'fast' and info['applied'] is False
    reranker, info = resolve_reranker('accurate')
    assert reranker is None and 'unavailable' in info['notice'] and info['mode'] == 'accurate'
    with pytest.raises(ByaxError):
        resolve_reranker('turbo')


def test_accurate_search_reranks_and_reports_what_moved(monkeypatch, catalogue, tmp_path):
    root = write_library(
        tmp_path,
        {
            'one.txt': 'storm storm storm harbour',
            'two.txt': 'storm ship wave sail harbour',
            'three.txt': 'tea garden',
        },
    )
    library = LibraryRegistry(tmp_path / 'cfg').create('K', [str(root)])
    index = LibraryIndex(library, cache_dir=tmp_path / 'cache')
    index.refresh()
    fast = search_library(index, 'storm', limit=5, rerank='fast')
    assert next(r['path'] for r in fast['results']) == 'one.txt' and 'rerank' not in fast

    class Prefer:
        identity = 'test:prefers-ship'

        def rerank(self, query, hits):
            ordered = sorted(hits, key=lambda h: 'ship' not in h.text)
            return [
                Hit(
                    **{
                        **h.__dict__,
                        'ranks': {**h.ranks, 'rerank': i},
                        'payload': {**h.payload, 'rerank_score': -i},
                    }
                )
                for i, h in enumerate(ordered, 1)
            ]

    accurate = search_library(index, 'storm', limit=5, reranker=Prefer())
    assert accurate['results'][0]['path'] == 'two.txt'
    assert accurate['results'][0]['prior_rank'] == 2 and accurate['results'][0]['rerank_rank'] == 1
    assert accurate['rerank']['applied'] and accurate['rerank']['model'] == 'test:prefers-ship'
    # Pagination applies after reranking.
    second = search_library(index, 'storm', limit=1, offset=1, reranker=Prefer())
    assert second['results'][0]['path'] == 'one.txt' and second['offset'] == 1


def test_retrieve_context_reranks_the_fused_hits_once_and_reports_it(catalogue, tmp_path):
    from byax.model import new_document
    from byax.retrieval import retrieve_context

    registry = LibraryRegistry(tmp_path / 'cfg')
    roots = []
    for name, text in (('a', 'storm harbour lamp'), ('b', 'storm ship wave sail')):
        root = tmp_path / name
        root.mkdir()
        (root / f'{name}.txt').write_text(text, encoding='utf-8')
        library = registry.create(name, [str(root)])
        LibraryIndex(library, cache_dir=tmp_path / 'cache').refresh()
        roots.append(library)
    doc = new_document()
    doc['settings']['libraries'] = [
        {'library_id': lib['library_id'], 'name': lib['name'], 'role': 'supplementary'} for lib in roots
    ]
    kwargs = {'registry': registry, 'cache_dir': tmp_path / 'cache', 'limit': 5}
    plain = retrieve_context(doc, 'storm', **kwargs)
    assert 'rerank' not in plain  # Fast is the default and says nothing

    class Ship:
        identity = 'test:ship-first'
        calls = 0

        def rerank(self, query, hits):
            Ship.calls += 1
            ordered = sorted(hits, key=lambda h: 'ship' not in h.text)
            return [
                Hit(
                    **{
                        **h.__dict__,
                        'ranks': {**h.ranks, 'rerank': i},
                        'payload': {**h.payload, 'rerank_score': -i},
                    }
                )
                for i, h in enumerate(ordered, 1)
            ]

    result = retrieve_context(doc, 'storm', reranker=Ship(), **kwargs)
    assert Ship.calls == 1, 'one global rerank, not one per library'
    assert result['included'][0]['text'] == 'storm ship wave sail'
    assert result['rerank']['applied'] and result['rerank']['candidates'] == 2
    assert result['included'][0]['rerank_score'] is not None


def test_reranker_failure_falls_back_to_fast_results(catalogue, tmp_path):
    root = write_library(tmp_path, {'one.txt': 'storm harbour', 'two.txt': 'storm ship'})
    library = LibraryRegistry(tmp_path / 'cfg').create('K', [str(root)])
    index = LibraryIndex(library, cache_dir=tmp_path / 'cache')
    index.refresh()

    class Broken:
        identity = 'test:broken'

        def rerank(self, query, hits):
            raise ByaxError('model_corrupt', 'bad weights')

    result = search_library(index, 'storm', reranker=Broken())
    assert (
        result['results']
        and result['rerank']['applied'] is False
        and 'bad weights' in result['rerank']['notice']
    )


def test_models_are_never_loaded_by_status_or_search_when_fast(catalogue, tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError('Fast search must not load a model')

    monkeypatch.setattr(local_inference, 'load', refuse)
    monkeypatch.setattr(local_inference, 'load_reranker', refuse)
    root = write_library(tmp_path, {'one.txt': 'storm harbour'})
    library = LibraryRegistry(tmp_path / 'cfg').create('K', [str(root)])
    index = LibraryIndex(library, cache_dir=tmp_path / 'cache')
    index.refresh()
    assert search_library(index, 'storm')['results']
    assert index.status()['embeddings'] == 'disabled'
    assert execute('models', {'action': 'list'})['settings']['rerank'] == 'fast'


@pytest.mark.skipif(
    not os.environ.get('BYAX_REAL_MODELS_DIR'), reason='set BYAX_REAL_MODELS_DIR to run the real-model checks'
)
def test_real_models(monkeypatch):
    """Semantic sanity on the verified catalogue models (maintainer check; not run in CI)."""
    monkeypatch.setenv('BYAX_MODELS_DIR', os.environ['BYAX_REAL_MODELS_DIR'])
    provider = local_inference.LocalEmbeddingProvider()
    query = provider.embed_query('What is the capital of China?')
    documents = provider.embed(['The capital of China is Beijing.', 'Gravity attracts bodies to each other.'])
    scores = [sum(a * b for a, b in zip(query, d, strict=True)) for d in documents]
    assert len(query) == 512 and scores[0] > scores[1] + 0.2
    hits = hits_for('Pandas eat bamboo.', 'The capital of China is Beijing.', 'Gravity attracts bodies.')
    for name in ('answerai-colbert-small-v1', 'bge-reranker-v2-m3-int8'):
        if local_models.model_status(name)['state'] != 'installed':
            continue
        ranked = local_inference.load_reranker(name).rerank('what is the capital of china', hits)
        assert ranked[0].chunk_id == 'c1', name
