"""Smoke benchmark for the on-device semantic tier: keyword vs hybrid vs hybrid + reranker.

    uv sync --extra local-models
    uv run python scripts/benchmark_semantic.py --corpus DIR --queries scripts/data/pride_queries.json \\
        --models-dir MODELS --chunk-tokens 200 400 800 --out docs/benchmarks/0.4-semantic-tier.json

It uses the real models from ``--models-dir`` (install them first with ``byax models``), builds a
library from the plain-text files in ``--corpus`` in a throw-away work directory, embeds it on this
computer and measures, per chunk size, retrieval quality (hit, MRR and nDCG at 10, hit at 20) and the
latency of each pipeline (p50 and p95 of wall-clock milliseconds, query embedding included).

A query's relevant chunks are those whose text matches every regular expression listed for it, so the
judgement is automatic and repeatable, but small. It stands in for the Phase 0 harness
(``evaluation.py``) until that lands; it is not a replacement for its fixture libraries.
Nothing here touches the network or a user's library.
"""

import argparse
import json
import math
import os
import platform
import re
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def percentile(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


def summarize(times):
    return {'p50': round(statistics.median(times), 1), 'p95': round(percentile(times, 0.95), 1)}


def metrics(ranked, relevant):
    """hit@10, MRR@10, nDCG@10 and hit@20 with binary relevance."""
    gains = [1.0 if chunk_id in relevant else 0.0 for chunk_id in ranked]
    top = gains[:10]
    ideal = [1.0] * min(10, len(relevant))
    dcg = sum(g / math.log2(i + 2) for i, g in enumerate(top))
    idcg = sum(g / math.log2(i + 2) for i, g in enumerate(ideal))
    first = next((i for i, g in enumerate(top) if g), None)
    return {
        'hit@10': 1.0 if any(top) else 0.0,
        'mrr@10': 1.0 / (first + 1) if first is not None else 0.0,
        'ndcg@10': dcg / idcg if idcg else 0.0,
        'hit@20': 1.0 if any(gains[:20]) else 0.0,
    }


def run_size(corpus, queries, models_dir, tokens, rerankers, depth, work):
    from byax import local_inference
    from byax.libraries import LibraryIndex, LibraryRegistry
    from byax.retrieval import rerank_results

    registry = LibraryRegistry(work / 'config')
    library = registry.create(f'bench-{tokens}', [str(corpus)])
    registry.configure_chunking(library['library_id'], tokens)
    registry.configure_embedding(
        library['library_id'], {'provider': 'local:qwen3-embedding-0.6b-int8', 'dimension': 512}
    )
    library = registry.get(library['library_id'])
    index = LibraryIndex(library, cache_dir=work / f'cache-{tokens}')
    index.refresh()
    chunks = index.status()['chunks']
    start = time.perf_counter()
    rebuilt = index.rebuild_vectors()
    embed_seconds = time.perf_counter() - start
    with index._db() as db:
        texts = {
            r['chunk_id']: ' '.join(r['text'].split())
            for r in db.execute('SELECT chunk_id, text FROM chunks')
        }
    provider = local_inference.provider_for(library['embedding'], directory=models_dir)
    models = {name: local_inference.load_reranker(name, directory=models_dir) for name in rerankers}
    pipelines = {'keyword': None, 'hybrid': None, **{f'hybrid+{name}': name for name in rerankers}}
    scores = {name: [] for name in pipelines}
    times = {name: [] for name in pipelines}
    warm = {name: [] for name in pipelines if name.startswith('hybrid+')}
    skipped = []
    for item in queries:
        relevant = {
            cid
            for cid, text in texts.items()
            if all(re.search(pattern, text, re.IGNORECASE) for pattern in item['relevant'])
        }
        if not relevant:
            skipped.append(item['query'])
            continue
        for name, reranker in pipelines.items():
            for attempt in (0, 1):
                begin = time.perf_counter()
                if name == 'keyword':
                    found = index.search(item['query'], limit=20)['results']
                else:
                    found = index.hybrid_search(item['query'], limit=max(20, depth), provider=provider)[
                        'results'
                    ]
                    if reranker:
                        found = rerank_results(item['query'], found, models[reranker], depth)
                elapsed = (time.perf_counter() - begin) * 1000
                if attempt == 0:
                    times[name].append(elapsed)
                    scores[name].append(metrics([r['chunk_id'] for r in found], relevant))
                elif name in warm:
                    warm[name].append(elapsed)
    report = {
        'chunk_tokens': tokens,
        'chunks': chunks,
        'embedding': {
            'seconds': round(embed_seconds, 1),
            'per_chunk_ms': round(embed_seconds / max(1, chunks) * 1000),
            'done': rebuilt['done'],
            'state': rebuilt['embedding_state'],
        },
        'queries_scored': len(scores['keyword']),
        'queries_without_relevant_chunks': skipped,
        'pipelines': {},
    }
    for name in pipelines:
        keys = ('hit@10', 'mrr@10', 'ndcg@10', 'hit@20')
        mean = (
            {k: round(statistics.fmean(s[k] for s in scores[name]), 3) for k in keys} if scores[name] else {}
        )
        report['pipelines'][name] = {**mean, 'latency_ms': summarize(times[name]) if times[name] else None}
        if name in warm and warm[name]:
            report['pipelines'][name]['latency_ms_repeat_same_query'] = summarize(warm[name])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--queries', type=Path, required=True)
    parser.add_argument('--models-dir', type=Path, required=True)
    parser.add_argument('--chunk-tokens', type=int, nargs='+', default=[400])
    parser.add_argument('--rerankers', nargs='*', default=['answerai-colbert-small-v1'])
    parser.add_argument('--depth', type=int, default=40)
    parser.add_argument('--work-dir', type=Path, help='Keep indexes here so a rerun skips embedding')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    os.environ['BYAX_MODELS_DIR'] = str(args.models_dir)
    import onnxruntime

    queries = json.loads(args.queries.read_text(encoding='utf-8'))
    work = args.work_dir or Path(tempfile.mkdtemp(prefix='byax-semantic-'))
    work.mkdir(parents=True, exist_ok=True)
    from byax import __version__, local_models

    report = {
        'measured_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'environment': {
            'platform': platform.platform(),
            'processor': platform.processor() or platform.machine(),
            'logical_cpus': os.cpu_count(),
            'python': platform.python_version(),
            'onnxruntime': onnxruntime.__version__,
            'engine': __version__,
            'hardware_qualification': 'Unqualified execution host; CPU/SSD target hardware not verified.',
        },
        'models': {
            name: local_models.get_model(name)['version']
            for name in ('qwen3-embedding-0.6b-int8', *args.rerankers)
        },
        'corpus': str(args.corpus.name),
        'queries': len(queries),
        'rerank_depth': args.depth,
        'targets': {'hybrid_p95_ms': 300, 'hybrid_with_reranking_p95_ms': 1000},
        'sizes': [
            run_size(args.corpus, queries, args.models_dir, tokens, args.rerankers, args.depth, work)
            for tokens in args.chunk_tokens
        ],
    }
    text = json.dumps(report, indent=2)
    if args.out:
        args.out.write_text(text + '\n', encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
