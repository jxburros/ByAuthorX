import pytest

from byax.embeddings import ConfiguredEmbeddingProvider
from byax.errors import ByaxError
from byax.libraries import LibraryIndex, LibraryRegistry
from byax.providers import ProviderConfig, ProviderRegistry


class LocalFixture:
    model_identity = 'fixture-v1'
    dimension = 2
    max_input_chars = 1000

    def embed(self, texts):
        return [[1.0, 0.0] if 'moon' in t or 'lunar' in t else [0.0, 1.0] for t in texts]


def test_hybrid_retains_exact_and_semantic_matches_and_invalidates(tmp_path):
    root = tmp_path / 'books'
    root.mkdir()
    (root / 'one.txt').write_text('A copper moon hangs overhead.')
    (root / 'two.txt').write_text('Mercury is a silvery element.')
    registry = LibraryRegistry(tmp_path / 'config')
    library = registry.create('Book', [str(root)])
    index = LibraryIndex(library, cache_dir=tmp_path / 'cache')
    index.refresh()
    provider = LocalFixture()
    assert index.rebuild_vectors(provider=provider)['processed'] == 2
    result = index.hybrid_search('lunar', provider=provider)
    assert result['results'][0]['text'] == 'A copper moon hangs overhead.'
    assert any(
        'Mercury' in hit['text'] for hit in index.hybrid_search('Mercury', provider=provider)['results']
    )
    provider.model_identity = 'fixture-v2'
    # A changed model signature marks vectors stale; they are never compared with the new model.
    stale = index.hybrid_search('lunar', provider=provider)
    assert stale['embedding_state'] == 'stale' and stale['method'] == 'keyword'


def test_remote_embeddings_require_separate_consent_and_validate_shape(tmp_path, monkeypatch):
    registry = ProviderRegistry(tmp_path)
    registry.save(ProviderConfig('host', 'openai', 'embedding-model', allow_remote=True))
    settings = {'provider': 'host', 'dimension': 2}
    calls = []
    monkeypatch.setattr(
        registry,
        '_request',
        lambda *a, **k: calls.append(k) or {'data': [{'index': 0, 'embedding': [1.0, 0.0]}]},
    )
    provider = ConfiguredEmbeddingProvider(settings, registry=registry)
    with pytest.raises(ByaxError):
        provider.embed(['private notes'])
    assert not calls
    provider = ConfiguredEmbeddingProvider(settings, registry=registry, authorized=True, local_only=True)
    with pytest.raises(ByaxError):
        provider.embed(['private notes'])
    assert not calls
    provider = ConfiguredEmbeddingProvider(settings, registry=registry, authorized=True)
    assert provider.embed(['notes']) == [[1.0, 0.0]]
    monkeypatch.setattr(registry, '_request', lambda *a, **k: {'data': [{'index': 0, 'embedding': [1.0]}]})
    with pytest.raises(ByaxError):
        provider.embed(['notes'])


def test_hybrid_query_rejects_unbounded_scope_before_loading_provider():
    from types import SimpleNamespace

    import pytest

    from byax.embeddings import hybrid_search
    from byax.errors import ByaxError

    index = SimpleNamespace(limits={'results': 100})

    for limit, offset in ((101, 0), (0, 0), (10, -1)):
        with pytest.raises(ByaxError, match='limit or offset'):
            hybrid_search(index, 'query', limit=limit, offset=offset)
