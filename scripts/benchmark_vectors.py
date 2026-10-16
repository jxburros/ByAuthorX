"""Measure vector search latency and recall: brute force (pure Python and numpy) versus sqlite-vec.

    uv sync --extra local-models --extra sqlite-vec
    uv run python scripts/benchmark_vectors.py --sizes 10000 50000 100000 --dimension 512

Synthetic clustered unit vectors stand in for real embeddings; queries are perturbed stored vectors.
The numbers decide ``byax.vector_store.BRUTE_FORCE_LIMIT`` (brute force stays the default below the
size where its p95 exceeds the budget). Nothing here touches the network or a user library.
"""

import argparse
import json
import os
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from byax import vector_store  # noqa: E402
from byax.libraries import LibraryIndex, LibraryRegistry  # noqa: E402
from byax.vector_store import SQLiteBlobStore, SqliteVecStore  # noqa: E402


def make_vectors(count, dimension, seed=1):
    rng = np.random.default_rng(seed)
    centers = rng.normal(size=(max(20, count // 200), dimension))
    data = centers[rng.integers(0, len(centers), count)] + 0.8 * rng.normal(size=(count, dimension))
    return (data / np.linalg.norm(data, axis=1, keepdims=True)).astype(np.float32)


def seed_index(directory, count):
    root = Path(directory) / 'lib'
    root.mkdir()
    library = LibraryRegistry(Path(directory) / 'cfg').create('bench', [str(root)])
    index = LibraryIndex(library, cache_dir=Path(directory) / 'cache')
    with index._db() as db:
        db.execute("INSERT INTO documents VALUES ('d', 'h')")
        db.executemany(
            "INSERT INTO chunks (chunk_id, document_id, ordinal, locator, heading, text, fingerprint) VALUES (?, 'd', ?, '{}', '', 't', ?)",
            [(f'c{i:07d}', i, f'f{i}') for i in range(count)],
        )
    return index


def percentile(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


def timed(function, queries):
    times = []
    results = []
    for query in queries:
        start = time.perf_counter()
        results.append(function(query))
        times.append((time.perf_counter() - start) * 1000)
    return times, results


def run(size, dimension, query_count, pure_python_limit):
    data = make_vectors(size, dimension)
    rng = np.random.default_rng(7)
    picks = rng.integers(0, size, query_count)
    queries = []
    for pick in picks:
        near = data[pick] + 0.05 * rng.normal(size=dimension).astype(np.float32) / np.sqrt(dimension)
        queries.append((near / np.linalg.norm(near)).tolist())
    row = {'vectors': size, 'dimension': dimension}
    with tempfile.TemporaryDirectory() as directory:
        index = seed_index(directory, size)
        exact = SQLiteBlobStore(index)
        start = time.perf_counter()
        for offset in range(0, size, 5000):
            exact.upsert(
                'sig',
                [(f'c{i:07d}', f'f{i}', data[i].tolist()) for i in range(offset, min(size, offset + 5000))],
            )
        row['insert_seconds'] = round(time.perf_counter() - start, 1)
        times, truth = timed(lambda q: exact.search('sig', q, limit=20), queries)
        row['numpy_brute_ms'] = {
            'p50': round(statistics.median(times), 1),
            'p95': round(percentile(times, 0.95), 1),
        }
        if size <= pure_python_limit:
            sys.modules['numpy'], saved = None, sys.modules['numpy']
            try:
                times, _ = timed(lambda q: exact.search('sig', q, limit=20), queries[: min(10, query_count)])
            finally:
                sys.modules['numpy'] = saved
            row['python_brute_ms'] = {
                'p50': round(statistics.median(times), 1),
                'p95': round(percentile(times, 0.95), 1),
            }
        if vector_store.sqlite_vec_available():
            binary = SqliteVecStore(index)
            start = time.perf_counter()
            binary.search('sig', queries[0], limit=20)  # first search indexes the existing float vectors
            row['sqlite_vec_index_seconds'] = round(time.perf_counter() - start, 1)
            times, found = timed(lambda q: binary.search('sig', q, limit=20), queries)
            hits = sum(
                len({c for c, _ in t[:10]} & {c for c, _ in f[:10]})
                for t, f in zip(truth, found, strict=True)
            )
            row['sqlite_vec_ms'] = {
                'p50': round(statistics.median(times), 1),
                'p95': round(percentile(times, 0.95), 1),
            }
            row['sqlite_vec_recall_at_10'] = round(hits / (10 * len(queries)), 3)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sizes', type=int, nargs='+', default=[10_000, 50_000, 100_000])
    parser.add_argument('--dimension', type=int, default=512)
    parser.add_argument('--queries', type=int, default=30)
    parser.add_argument('--pure-python-limit', type=int, default=20_000)
    args = parser.parse_args()
    report = {
        'measured_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'machine': f'{platform.processor() or platform.machine()} / Python {platform.python_version()}',
        'logical_cpus': os.cpu_count(),
        'load_average_at_start': os.getloadavg()[0] if hasattr(os, 'getloadavg') else None,
        'budget_ms': {'hybrid_p95': 300},
        'rows': [run(size, args.dimension, args.queries, args.pure_python_limit) for size in args.sizes],
    }
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
