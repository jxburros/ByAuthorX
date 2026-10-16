"""The optional sqlite-vec backend: binary candidates, exact float re-scoring, the same interface."""

import sys

import pytest

from byax import vector_store
from byax.errors import ByaxError
from byax.libraries import LibraryIndex, LibraryRegistry
from byax.vector_store import SQLiteBlobStore, open_store

np = pytest.importorskip('numpy')
pytest.importorskip('sqlite_vec')


def seed(tmp_path, count, *, backend=None):
    root = tmp_path / 'lib'
    root.mkdir(exist_ok=True)
    library = LibraryRegistry(tmp_path / 'cfg').create('V', [str(root)])
    if backend:
        library['embedding'] = {'provider': 'x', 'dimension': 8, 'vector_backend': backend}
    index = LibraryIndex(library, cache_dir=tmp_path / 'cache')
    with index._db() as db:
        db.execute("INSERT INTO documents VALUES ('d', 'h')")
        db.executemany(
            'INSERT INTO chunks (chunk_id, document_id, ordinal, locator, heading, text, fingerprint) '
            "VALUES (?, 'd', ?, '{}', '', 't', ?)",
            [(f'c{i:05d}', i, f'f{i}') for i in range(count)],
        )
    return index


def vectors(count, dimension, seed_value=3):
    rng = np.random.default_rng(seed_value)
    centers = rng.normal(size=(20, dimension))
    data = centers[rng.integers(0, 20, count)] + 0.6 * rng.normal(size=(count, dimension))
    return data / np.linalg.norm(data, axis=1, keepdims=True)


def load(store, data, signature='sig'):
    store.upsert(signature, [(f'c{i:05d}', f'f{i}', row.tolist()) for i, row in enumerate(data)])


def test_binary_candidates_are_rescored_with_exact_cosine_and_recall_is_high(tmp_path):
    data = vectors(2000, 256)
    index = seed(tmp_path, 2000)
    exact, binary = SQLiteBlobStore(index), open_store(index, 'sqlite-vec')
    assert binary.backend == 'sqlite-vec'
    load(exact, data)
    hits = 0
    for q in range(25):
        # A query close to a stored vector, as a search for a passage's own topic would be.
        rng = np.random.default_rng(100 + q)
        near = data[int(rng.integers(0, 2000))] + 0.05 * rng.normal(size=256) / 16
        query = (near / np.linalg.norm(near)).tolist()
        truth = exact.search('sig', query, limit=10)
        found = binary.search('sig', query, limit=10)
        hits += len({c for c, _ in truth} & {c for c, _ in found})
        exact_scores = dict(exact.search('sig', query, limit=2000))
        for chunk_id, similarity in found:
            assert similarity == pytest.approx(exact_scores[chunk_id], abs=1e-9)
    assert hits / 250 >= 0.95
    # Similarities returned are the exact float cosines, so the best exact hit has its true score.
    query = data[5].tolist()
    best = binary.search('sig', query, limit=3)[0]
    assert best[0] == 'c00005' and best[1] == pytest.approx(1.0, abs=1e-6)


def test_dimension_not_a_multiple_of_eight_is_padded_without_changing_ranking(tmp_path):
    data = vectors(300, 100)
    index = seed(tmp_path, 300)
    store = open_store(index, 'sqlite-vec')
    load(store, data)
    for row in (0, 17, 299):
        assert store.search('sig', data[row].tolist(), limit=1)[0][0] == f'c{row:05d}'


def test_backend_interface_matches_the_default_store(tmp_path):
    data = vectors(120, 64)
    index = seed(tmp_path, 120)
    store = open_store(index, 'sqlite-vec')
    load(store, data)
    assert store.coverage('sig')['embedded'] == 120 and store.signatures() == {'sig': 120}
    assert len(store.fingerprints('sig')) == 120
    # Re-embedding 50 chunks under a new signature replaces their rows; the other 70 are stale and dropped.
    load(store, data[:50], 'other')
    assert store.search('sig', data[0].tolist(), limit=3)
    assert store.discard_other_signatures('other') == 70
    assert store.search('sig', data[0].tolist(), limit=3) == []
    got = store.search('other', data[2].tolist(), limit=3)
    assert got[0][0] == 'c00002'
    with index._db() as db:
        db.execute("DELETE FROM chunks WHERE chunk_id='c00002'")
    assert store.prune() == 1
    assert 'c00002' not in {c for c, _ in store.search('other', data[2].tolist(), limit=10)}


def test_switching_backends_indexes_existing_vectors_on_first_search(tmp_path):
    data = vectors(200, 64)
    index = seed(tmp_path, 200)
    load(SQLiteBlobStore(index), data)
    store = open_store(index, 'sqlite-vec')
    assert store.search('sig', data[9].tolist(), limit=1)[0][0] == 'c00009'
    again = open_store(index, 'sqlite-vec')
    assert again.search('sig', data[10].tolist(), limit=1)[0][0] == 'c00010'


def test_library_embedding_setting_selects_the_backend(tmp_path):
    index = seed(tmp_path, 10, backend='sqlite-vec')
    assert open_store(index).backend == 'sqlite-vec'
    (tmp_path / 'other').mkdir()
    assert open_store(seed(tmp_path / 'other', 10)).backend == 'sqlite-blob'
    from byax.embeddings import validate_config

    assert validate_config({'provider': 'p', 'dimension': 8, 'vector_backend': 'sqlite-vec'})
    with pytest.raises(ByaxError):
        validate_config({'provider': 'p', 'dimension': 8, 'vector_backend': 'lancedb'})


def test_missing_extra_is_reported_and_unknown_backends_are_refused(tmp_path, monkeypatch):
    index = seed(tmp_path, 5)
    monkeypatch.setitem(sys.modules, 'sqlite_vec', None)
    with pytest.raises(ByaxError) as caught:
        open_store(index, 'sqlite-vec')
    assert caught.value.code == 'missing_dependency' and 'byauthorx[sqlite-vec]' in caught.value.message
    assert vector_store.sqlite_vec_available() is False
    with pytest.raises(ByaxError):
        open_store(index, 'lancedb')
    # Brute force keeps working without the extension.
    assert open_store(index).backend == 'sqlite-blob'


def test_large_libraries_switch_to_sqlite_vec_automatically(tmp_path, monkeypatch):
    index = seed(tmp_path, 40)
    assert open_store(index).backend == 'sqlite-blob'
    monkeypatch.setattr(vector_store, 'BRUTE_FORCE_LIMIT', 30)
    assert open_store(index).backend == 'sqlite-vec'
    # An explicit choice always wins, in both directions.
    index.library['embedding'] = {'provider': 'x', 'dimension': 8, 'vector_backend': 'sqlite-blob'}
    assert open_store(index).backend == 'sqlite-blob'
    monkeypatch.setitem(sys.modules, 'sqlite_vec', None)
    vector_store._AVAILABLE.clear()
    index.library['embedding'] = None
    assert open_store(index).backend == 'sqlite-blob'
    vector_store._AVAILABLE.clear()


def test_numpy_and_pure_python_brute_force_agree(tmp_path, monkeypatch):
    data = vectors(150, 32)
    index = seed(tmp_path, 150)
    store = SQLiteBlobStore(index)
    load(store, data)
    query = vectors(1, 32, 55)[0].tolist()
    fast = store.search('sig', query, limit=8)
    monkeypatch.setitem(sys.modules, 'numpy', None)
    slow = store.search('sig', query, limit=8)
    assert [c for c, _ in fast] == [c for c, _ in slow]
    assert [s for _, s in fast] == pytest.approx([s for _, s in slow], abs=1e-6)
    assert len(fast) == 8
