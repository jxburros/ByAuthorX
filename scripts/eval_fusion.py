#!/usr/bin/env python3
"""Does weighting the keyword list in reciprocal-rank fusion close the gap to keyword search?

    BYAX_MODELS_DIR=... uv run python scripts/eval_fusion.py

Embeds the sample libraries once with the on-device model, then re-scores hybrid search while the keyword
list's weight in the fusion (score = sum of weight / (k + rank)) goes 1, 2, 3, 4. Weight 1 is what
`ranking.rrf` does today. Writes docs/benchmarks/context-0.4-fusion.json.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from byax import evaluation as ev  # noqa: E402
from byax import ranking  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def weighted(weights, original):
    def fuse(lists, k=ranking.RRF_K, *, names=None):
        if names != ['keyword', 'dense']:
            return original(lists, k, names=names)
        merged = {}
        for position, ranked in enumerate(lists):
            for rank, item in enumerate(ranked, 1):
                hit = ranking.Hit.from_result(item)
                entry = merged.setdefault(
                    hit.key,
                    ranking.Hit(**{**hit.__dict__, 'ranks': {}, 'score': 0.0, 'payload': dict(hit.payload)}),
                )
                if names[position] in entry.ranks:
                    continue
                entry.ranks[names[position]] = rank
                entry.score += weights[position] / (k + rank)
        best = {key: min(h.ranks.values()) for key, h in merged.items()}
        return sorted(merged.values(), key=lambda h: (-h.score, best[h.key], h.key))

    return fuse


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--weights', nargs='+', type=float, default=[1, 2, 3, 4])
    parser.add_argument('--out', type=Path, default=ROOT / 'docs' / 'benchmarks' / 'context-0.4-fusion.json')
    args = parser.parse_args(argv)
    original = ranking.rrf
    rows = []
    with tempfile.TemporaryDirectory(prefix='byax-fusion-') as scratch:
        work = Path(scratch)
        config = ev.resolve_config('hybrid-qwen3')
        keyword = ev.resolve_config('keyword')
        for library in ev.FIXTURE_LIBRARIES:
            spec = ev.SAMPLES[library]
            corpus = ev.build_fixture_corpus(library, work, sample=spec)
            queries = ev.load_queries(library)
            base = ev.evaluate(corpus, queries, keyword)
            config.prepare(corpus)
            for weight in args.weights:
                ranking.rrf = weighted((weight, 1.0), original)
                try:
                    report = ev.evaluate(corpus, queries, config, prepared={})
                finally:
                    ranking.rrf = original
                rows.append(
                    {
                        'library': library,
                        'keyword_weight': weight,
                        'metrics': report['metrics'],
                        'by_type': report['by_type'],
                        'keyword_metrics': base['metrics'],
                        'keyword_by_type': base['by_type'],
                        'queries': report['queries']['evaluated'],
                    }
                )
                print(library, weight, report['metrics'], 'keyword', base['metrics'], flush=True)
    summary = {}
    for weight in args.weights:
        picked = [r for r in rows if r['keyword_weight'] == weight]
        summary[str(weight)] = ev.macro_average([{'metrics': r['metrics']} for r in picked])
    summary['keyword'] = ev.macro_average(
        [{'metrics': r['keyword_metrics']} for r in rows if r['keyword_weight'] == args.weights[0]]
    )
    result = {
        'schema_version': 1,
        'measured_at_utc': datetime.now(UTC).isoformat(),
        'environment': ev.environment(),
        'embedder': 'qwen3-embedding-0.6b-int8',
        'summary_macro': summary,
        'runs': rows,
    }
    args.out.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
