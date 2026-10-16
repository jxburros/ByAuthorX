#!/usr/bin/env python3
"""Measure retrieval quality, latency, index size and build time on the evaluation fixtures.

    uv run python scripts/eval_retrieval.py                       # keyword, full fixtures
    uv run python scripts/eval_retrieval.py --sample              # the fast subset pytest uses
    uv run python scripts/eval_retrieval.py --configs keyword hybrid-fake context
    uv run python scripts/eval_retrieval.py --scale 10000         # also time the synthetic scale corpus

Writes `docs/benchmarks/context-<version>-<config>.json` (override the folder with --out-dir, the
whole name with --output, and stamp context with --note), recording hardware and software like the
layout benchmarks. `--update-thresholds` rewrites the CI floors in `tests/fixtures/eval/thresholds.json`
from a sample run. Metrics are defined in `byax.evaluation`; labels are locators, never chunk IDs.

The `papers` library uses the full 30-article set when `scripts/fetch_eval_fixtures.py` has cached it,
otherwise only the three committed PDFs (the report says which). Nothing here uses the network or a
model; `hybrid-fake` embeds with a deterministic hashing function.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from byax import __version__  # noqa: E402
from byax import evaluation as ev  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFINITIONS = {
    'recall@k': "Share of a query's relevant locators covered by at least one of the top k hits (hit and locator overlap).",
    'ndcg@10': 'Binary gain per hit that newly covers a relevant locator; ideal list is min(locators, 10) gains.',
    'mrr': 'Reciprocal rank of the first hit overlapping any relevant locator within the top 20; 0 when none.',
    'latency_ms': 'Wall time of one search call per query after one untimed warm-up; nearest-rank percentiles. '
    'latency_cpu_ms is the process CPU time of the same calls (the host was shared with other jobs: see the load averages).',
    'index_bytes': 'Size of the derived SQLite index after a WAL checkpoint, including stored vectors.',
    'build_seconds': 'LibraryIndex.refresh from an empty cache (parsing, chunking, FTS). prepare_seconds adds embedding.',
    'absence': 'literal probes count as found when any hit overlaps a labelled occurrence; expected-absent probes report '
    'whether the engine returned nothing (a lexical OR search never does: semantic absence is reported separately).',
}
BASELINE = {
    'label': '0.2.0 keyword-only baseline',
    'measured_on_engine': __version__,
    'comparable_to_engine': '0.2.0',
    'why': 'Retrieval is unchanged between 0.2.0 (commit 48ca3e3) and 0.3.0: the diff of src/byax/libraries.py, '
    'retrieval.py, embeddings.py and library_parser.py between those commits is empty. The numbers were measured '
    'on 0.3.0 because the harness needs this checkout. Every later phase reports against them.',
    'engine_under_test': 'LibraryIndex.search: FTS5 BM25, every query term OR-ed, line/paragraph chunks of at most 1800 characters',
}
NOT_MEASURED = [
    'Reference-laptop latency: this host is not a qualified reference machine.',
    'Model-backed embeddings, reranking and entailment (hybrid-fake uses a hashing function, not a model).',
    'Cross-library queries from webclips into the novels and papers.',
]


def papers_directory(full: bool, work: Path) -> tuple[Path | None, str]:
    """Flat folder with the full verified set, or None to use only the committed PDFs."""
    if not full:
        return None, 'committed PDFs only'
    import fetch_eval_fixtures as fetch

    cache = ev.default_papers_cache()
    result = fetch.fetch(ev.fixtures_root(), cache, download_missing=False)
    if result['missing'] or result['mismatch']:
        return (
            None,
            f'committed PDFs only ({len(result["missing"])} cached files missing; run fetch_eval_fixtures.py)',
        )
    destination = work / 'papers-full'
    fetch.materialize(ev.fixtures_root(), cache, destination)
    return destination, 'full 30-article set'


def run_scale(config_spec, documents: int, work: Path, queries: int) -> dict:
    import generate_fixtures

    target = work / 'scale-source'
    info = generate_fixtures.make_eval_scale(target, documents, queries=queries)
    corpus = ev.build_corpus('scale', target / 'corpus', work, limits={'chunks': 2_000_000})
    queries = ev.load_queries_file(target / 'queries.jsonl')
    report = ev.evaluate(corpus, queries, config_spec)
    report['scale'] = {'documents': info['documents'], 'queries': info['queries']}
    return report


def summarize(reports: list[dict]) -> dict:
    latencies = [r['latency_ms'] for r in reports]
    return {
        'macro_average': ev.macro_average(reports),
        'queries_evaluated': sum(r['queries']['evaluated'] for r in reports),
        'latency_ms_p95_max': max((x['p95'] for x in latencies), default=0.0),
        'latency_cpu_ms_p95_max': max((r['latency_cpu_ms']['p95'] for r in reports), default=0.0),
        'index_bytes_total': sum(r['index']['bytes'] for r in reports),
        'build_seconds_total': round(
            sum(r['index']['build_seconds'] + r['index']['prepare_seconds'] for r in reports), 3
        ),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        '--libraries', nargs='+', default=list(ev.FIXTURE_LIBRARIES), choices=ev.FIXTURE_LIBRARIES
    )
    parser.add_argument('--configs', nargs='+', default=['keyword'])
    parser.add_argument(
        '--sample', action='store_true', help='use the fast pytest subset instead of the full fixtures'
    )
    parser.add_argument(
        '--scale', type=int, default=0, metavar='N', help='also measure an N-document synthetic corpus'
    )
    parser.add_argument('--scale-queries', type=int, default=50, help='golden queries for the scale corpus')
    parser.add_argument('--out-dir', type=Path, default=ROOT / 'docs' / 'benchmarks')
    parser.add_argument('--output', type=Path, help='exact output file (single config only)')
    parser.add_argument('--note', default='', help='free text recorded in the file')
    parser.add_argument(
        '--baseline', action='store_true', help='write context-0.2-baseline.json (keyword, 0.2.0-equivalent)'
    )
    parser.add_argument(
        '--update-thresholds', action='store_true', help='rewrite CI floors from this (sample) run'
    )
    parser.add_argument('--keep', type=Path, help='work in this folder instead of a temporary one')
    args = parser.parse_args(argv)
    if args.output and len(args.configs) != 1:
        parser.error('--output needs exactly one --configs value')
    if args.baseline:
        args.configs, args.output = ['keyword'], args.out_dir / 'context-0.2-baseline.json'
    if args.update_thresholds and not args.sample:
        parser.error('--update-thresholds applies to --sample runs')

    root = ev.fixtures_root()
    environment_start = ev.environment()
    manifest_digest = hashlib.sha256((root / 'MANIFEST.json').read_bytes()).hexdigest()
    thresholds = {}
    for config_name in args.configs:
        with tempfile.TemporaryDirectory(prefix='byax-eval-') as scratch:
            work = args.keep or Path(scratch)
            papers, papers_note = papers_directory(not args.sample, work)
            reports = {}
            allowed = ev.libraries_for(config_name, sample=args.sample)
            for library in [x for x in args.libraries if x in allowed]:
                started = time.perf_counter()
                reports[library] = ev.run_library(
                    library,
                    config_name,
                    work,
                    sample=args.sample,
                    papers_dir=papers if library == 'papers' else None,
                )
                print(
                    f'{config_name:12s} {library:11s} '
                    + ' '.join(f'{k}={v:.3f}' for k, v in reports[library]['metrics'].items())
                    + f' p95={reports[library]["latency_ms"]["p95"]:.1f}ms ({time.perf_counter() - started:.1f}s)',
                    flush=True,
                )
            scale = run_scale(config_name, args.scale, work, args.scale_queries) if args.scale else None
            if scale:
                print(f'{config_name:12s} scale({args.scale}) ' + ' '.join(f'{k}={v:.3f}' for k, v in scale['metrics'].items())
                      + f' p95={scale["latency_ms"]["p95"]:.1f}ms build={scale["index"]["build_seconds"]:.0f}s')  # fmt: skip
        result = {
            'schema_version': 1,
            'measured_at_utc': datetime.now(UTC).isoformat(),
            'environment': environment_start,
            'corpus_mode': 'sample' if args.sample else 'full',
            'papers': papers_note,
            'fixtures_manifest_sha256': manifest_digest,
            'provider_configuration': 'None; local deterministic operations only. No network, no model.',
            'definitions': DEFINITIONS,
            'not_measured': NOT_MEASURED,
            'note': args.note,
            'load_average_at_end_1m_5m_15m': [round(x, 2) for x in os.getloadavg()],
            'libraries': reports,
            'summary': summarize(list(reports.values())),
        }
        if scale:
            result['scale'] = scale
        if args.baseline:
            result['baseline'] = BASELINE
        if args.update_thresholds:
            thresholds[config_name] = {
                library: {**report['metrics'], 'queries': report['queries']['evaluated']}
                for library, report in reports.items()
            }
        if not args.update_thresholds:
            args.out_dir.mkdir(parents=True, exist_ok=True)
            path = args.output or args.out_dir / f'context-{__version__}-{config_name}.json'
            path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
            print('wrote', path)
    if args.update_thresholds:
        path = root / 'thresholds.json'
        current = (
            json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'tolerance': {}, 'floors': {}}
        )
        current['floors'].update(thresholds)
        path.write_text(json.dumps(current, indent=2) + '\n', encoding='utf-8')
        print('updated', path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
