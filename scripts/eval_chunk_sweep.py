#!/usr/bin/env python3
"""Sweep the chunker's target size (200-1000 tokens) and report retrieval quality per size.

    uv run python scripts/eval_chunk_sweep.py                      # keyword + hybrid-fake, all five libraries
    BYAX_MODELS_DIR=... uv run python scripts/eval_chunk_sweep.py --models   # + the on-device embedder (slow)

Writes docs/benchmarks/context-0.4-chunk-sweep.json. `LibraryIndex(chunk_tokens=N)` re-chunks every file,
so each size is a fresh index. Labels are locators, so the same golden queries score every size.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from byax import evaluation as ev  # noqa: E402
from byax.libraries import CHUNKING  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SIZES = [200, 300, 400, 600, 800, 1000]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--sizes', nargs='+', type=int, default=SIZES)
    parser.add_argument('--configs', nargs='+', default=['keyword', 'hybrid-fake'])
    parser.add_argument(
        '--libraries', nargs='+', default=list(ev.FIXTURE_LIBRARIES), choices=ev.FIXTURE_LIBRARIES
    )
    parser.add_argument('--sample', action='store_true', help='use the fast subsets (needed for --models)')
    parser.add_argument(
        '--models', action='store_true', help='also run hybrid-qwen3 on novel and papers samples'
    )
    parser.add_argument(
        '--out', type=Path, default=ROOT / 'docs' / 'benchmarks' / 'context-0.4-chunk-sweep.json'
    )
    args = parser.parse_args(argv)
    import fetch_eval_fixtures as fetch

    runs = [(config, args.libraries, args.sample) for config in args.configs]
    if args.models:
        runs.append(('hybrid-qwen3', ['novel', 'papers'], True))
    rows = []
    with tempfile.TemporaryDirectory(prefix='byax-sweep-') as scratch:
        work = Path(scratch)
        papers = work / 'papers-full'
        fetch.materialize(ev.fixtures_root(), ev.default_papers_cache(), papers)
        papers_full = papers if len(list(papers.glob('*.pdf'))) == 30 else None
        for config, libraries, sample in runs:
            for size in args.sizes:
                for library in libraries:
                    started = time.perf_counter()
                    report = ev.run_library(
                        library,
                        config,
                        work / f'{config}-{size}',
                        sample=sample,
                        papers_dir=None if sample or library != 'papers' else papers_full,
                        chunk_tokens=size,
                    )
                    rows.append(
                        {
                            'config': config,
                            'chunk_tokens': size,
                            'library': library,
                            'sample': sample,
                            'queries': report['queries']['evaluated'],
                            'metrics': report['metrics'],
                            'chunks': report['index']['chunks'],
                            'index_bytes': report['index']['bytes'],
                            'latency_ms_p95': report['latency_ms']['p95'],
                        }
                    )
                    print(
                        f'{config:12s} {size:5d} {library:11s} '
                        + ' '.join(f'{k}={v:.3f}' for k, v in report['metrics'].items())
                        + f' chunks={report["index"]["chunks"]} ({time.perf_counter() - started:.0f}s)',
                        flush=True,
                    )
    summary = {}
    for config, _, sample in runs:
        for size in args.sizes:
            picked = [r for r in rows if r['config'] == config and r['chunk_tokens'] == size]
            summary.setdefault(config, {})[str(size)] = {
                **ev.macro_average([{'metrics': r['metrics']} for r in picked]),
                'libraries': [r['library'] for r in picked],
                'chunks': sum(r['chunks'] for r in picked),
            }
    result = {
        'schema_version': 1,
        'measured_at_utc': datetime.now(UTC).isoformat(),
        'environment': ev.environment(),
        'default_chunk_tokens': CHUNKING['target_tokens'],
        'sizes': args.sizes,
        'summary': summary,
        'runs': rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print('wrote', args.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
