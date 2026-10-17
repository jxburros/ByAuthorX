#!/usr/bin/env python3
"""Time and profile keyword search on the kept scale index (see build_scale_index.py).

    uv run python scripts/profile_search.py artifacts/scale-work [--profile] [--queries 30]

Reports wall and CPU p50/p95 for `LibraryIndex.search` and `retrieval.retrieve_context`; with --profile it
prints the hottest functions of a cProfile run over the same queries.
"""

from __future__ import annotations

import argparse
import cProfile
import json
import pstats
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from byax import evaluation as ev  # noqa: E402
from byax.libraries import LibraryIndex, LibraryRegistry  # noqa: E402
from byax.model import new_document  # noqa: E402
from byax.retrieval import retrieve_context  # noqa: E402


def timed(function, queries):
    wall, cpu = [], []
    function(queries[0])  # warm-up
    for query in queries:
        started, cpu_started = time.perf_counter(), time.process_time()
        function(query)
        wall.append((time.perf_counter() - started) * 1000)
        cpu.append((time.process_time() - cpu_started) * 1000)
    return {
        'wall_p50_ms': round(ev.percentile(wall, 0.5), 2),
        'wall_p95_ms': round(ev.percentile(wall, 0.95), 2),
        'cpu_p50_ms': round(ev.percentile(cpu, 0.5), 2),
        'cpu_p95_ms': round(ev.percentile(cpu, 0.95), 2),
        'samples': len(wall),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('work', type=Path)
    parser.add_argument('--queries', type=int, default=50)
    parser.add_argument('--profile', action='store_true')
    args = parser.parse_args(argv)
    info = json.loads((args.work / 'scale-info.json').read_text(encoding='utf-8'))
    library = info['library']
    index = LibraryIndex(library, info['cache'])
    queries = [q['query'] for q in ev.load_queries_file(args.work / 'scale-source' / 'queries.jsonl')][
        : args.queries
    ]
    registry = LibraryRegistry(args.work / 'scale' / 'config')
    doc = new_document()
    doc['settings']['libraries'] = [{'library_id': library['library_id'], 'role': 'supplementary'}]
    runs = {
        'search': lambda q: index.search(q, limit=20),
        'retrieve_context': lambda q: retrieve_context(
            doc, q, registry=registry, cache_dir=info['cache'], limit=12, max_chars=10**7
        ),
    }
    report = {name: timed(function, queries) for name, function in runs.items()}
    print(json.dumps(report, indent=1))
    if args.profile:
        profiler = cProfile.Profile()
        profiler.enable()
        for query in queries[:20]:
            runs['retrieve_context'](query)
        profiler.disable()
        pstats.Stats(profiler).sort_stats('cumulative').print_stats(18)
    return 0


if __name__ == '__main__':
    sys.exit(main())
