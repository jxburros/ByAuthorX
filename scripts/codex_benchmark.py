#!/usr/bin/env python3
"""Codex mention accuracy and project search latency (docs/benchmarks/0.3-codex.json).

    uv run python scripts/codex_benchmark.py            # print the numbers
    uv run python scripts/codex_benchmark.py --write    # refresh the published JSON

Accuracy: ``byax.codex.mentions`` on tests/fixtures/codex/pride-and-prejudice-1-6.json (chapters I-VI,
labels from scripts/build_codex_fixture.py). A mention is correct when its entry and exact span equal
a label. Two codex configurations are measured: the fixture codex (names and aliases) and the same
entries with canonical names only, which shows how much aliases contribute.

Latency: the 100,000-word ``project_100k`` fixture (scripts/generate_fixtures.py) saved as a project
folder; index build, a no-op incremental refresh, keyword ``ProjectIndex.search`` p95 over a query mix,
codex mention heatmap over the whole manuscript and a selection-sized ``codex.ai_context``.
"""

from __future__ import annotations

import argparse
import copy
import json
import platform
import statistics
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
FIXTURE = ROOT / 'tests' / 'fixtures' / 'codex' / 'pride-and-prejudice-1-6.json'
OUTPUT = ROOT / 'docs' / 'benchmarks' / '0.3-codex.json'
QUERIES = (
    'observation',
    '"field team"',
    'interpret*',
    'narrative -table',
    'NEAR(clear account, 5)',
    'Alex Rivera',
    'referenes',
    'heading:"Scene 12.3"',
    'meaning wording',
    'ervatio',
)


def fixture_document(fixture: dict, entries: dict | None = None) -> tuple[dict, dict]:
    """Novel fixture as a document (one heading per chapter, binder initialised); paragraph -> block id."""
    from byax import engine
    from byax.model import new_document

    doc = new_document(fixture['source']['title'])
    doc['id'], doc['revision'] = 'doc_codex_fixture', 'rev_codex_fixture'
    doc['blocks'], places = [], {}
    for c, chapter in enumerate(fixture['chapters']):
        doc['blocks'].append(
            {
                'id': f'h_{c}',
                'type': 'heading',
                'text': chapter['title'],
                'style': 'heading1',
                'marks': [],
                'attrs': {'level': 1},
            }
        )
        for p, text in enumerate(chapter['paragraphs']):
            identity = f'p_{c}_{p}'
            places[identity] = (c, p)
            doc['blocks'].append(
                {'id': identity, 'type': 'paragraph', 'text': text, 'style': 'body', 'marks': [], 'attrs': {}}
            )
    doc['codex'] = {'entries': copy.deepcopy(entries if entries is not None else fixture['codex'])}
    doc, _ = engine.apply(doc, {'operations': [{'type': 'project_init'}]})
    return doc, places


def names_only(fixture: dict) -> dict:
    return {
        key: {k: v for k, v in entry.items() if k != 'aliases'} for key, entry in fixture['codex'].items()
    }


def accuracy(fixture: dict, entries: dict | None = None) -> dict:
    from byax import codex

    doc, places = fixture_document(fixture, entries)
    found = codex.mentions(doc, limit=10**6)['mentions']
    predicted = {
        (*places[m['target']], m['start'], m['end'], m['entry']) for m in found if m['target'] in places
    }
    labelled = {(g['chapter'], g['paragraph'], g['start'], g['end'], g['entry']) for g in fixture['gold']}
    correct = predicted & labelled
    by_entry = {}
    for key in sorted(fixture['codex']):
        got = {x for x in predicted if x[4] == key}
        want = {x for x in labelled if x[4] == key}
        if got or want:
            by_entry[key] = {'correct': len(got & want), 'reported': len(got), 'labelled': len(want)}
    return {
        'correct': len(correct),
        'reported': len(predicted),
        'labelled': len(labelled),
        'precision': round(len(correct) / len(predicted), 4) if predicted else 1.0,
        'recall': round(len(correct) / len(labelled), 4) if labelled else 1.0,
        'false_positives': [list(x) for x in sorted(predicted - labelled)][:20],
        'missed': [list(x) for x in sorted(labelled - predicted)][:20],
        'by_entry': by_entry,
        'automaton': codex.automaton_backend(),
    }


def _p95(samples):
    ordered = sorted(samples)
    return ordered[max(0, round(0.95 * len(ordered)) - 1)]


def _ms(seconds):
    return round(seconds * 1000, 2)


def latency(samples: int = 20) -> dict:
    sys.path.insert(0, str(ROOT / 'scripts'))
    from generate_fixtures import project_fixture

    from byax import codex
    from byax.model import text_of
    from byax.project_index import ProjectIndex
    from byax.statistics import counts
    from byax.storage import save_document

    doc = project_fixture()
    doc['codex'] = {
        'entries': {
            'cx_alex': {
                'id': 'cx_alex',
                'kind': 'character',
                'name': 'Alex Rivera',
                'aliases': ['Alex'],
                'traits': [],
            },
            'cx_team': {
                'id': 'cx_team',
                'kind': 'term',
                'name': 'field team',
                'case_sensitive': False,
                'traits': [],
            },
            'cx_table': {
                'id': 'cx_table',
                'kind': 'item',
                'name': 'table',
                'case_sensitive': False,
                'traits': [],
            },
        }
    }
    with tempfile.TemporaryDirectory(prefix='byax-codex-bench-') as temporary:
        folder = Path(temporary) / 'project'
        folder.mkdir()
        path = folder / 'project_100k.byax'
        save_document(doc, path)
        index = ProjectIndex(path, doc=doc, cache_dir=Path(temporary) / 'cache')
        start = time.perf_counter()
        index.refresh(doc)
        build = time.perf_counter() - start
        start = time.perf_counter()
        index.refresh(doc)
        noop = time.perf_counter() - start
        edited = copy.deepcopy(doc)
        target = next(b for b in edited['blocks'] if b['id'] == 'scene_12_3_p1')
        target['text'] = 'A single edited paragraph. ' + target['text']
        edited['revision'] = 'rev_bench_edit'
        start = time.perf_counter()
        changed = index.update_document(edited)
        incremental = time.perf_counter() - start
        timings, totals = {}, {}
        for query in QUERIES:
            runs = []
            for _ in range(samples):
                start = time.perf_counter()
                result = index.search(query, limit=20)
                runs.append(time.perf_counter() - start)
            timings[query] = {'p50_ms': _ms(statistics.median(runs)), 'p95_ms': _ms(_p95(runs))}
            totals[query] = result['total']
        everything = [t for q in QUERIES for t in [timings[q]['p95_ms']]]
        runs = []
        for _ in range(max(3, samples // 4)):
            start = time.perf_counter()
            heat = codex.heatmap(edited)
            runs.append(time.perf_counter() - start)
        selection = []
        for _ in range(samples):
            start = time.perf_counter()
            codex.ai_context(edited, {'target': 'scene_12_3_p1', 'start': 0, 'end': 200, 'radius': 2})
            selection.append(time.perf_counter() - start)
        chunks = index.status()['chunks']
    return {
        'fixture': 'project_100k (scripts/generate_fixtures.py), saved as a one-document project folder',
        'words': counts(text_of(doc))['words'],
        'indexed_rows': chunks,
        'index_build_ms': _ms(build),
        'noop_refresh_ms': _ms(noop),
        'one_paragraph_update_ms': _ms(incremental),
        'one_paragraph_update_rows': changed,
        'keyword_search': {
            'samples_per_query': samples,
            'queries': timings,
            'result_totals': totals,
            'worst_query_p95_ms': max(everything),
            'target_p95_ms': 100,
            'meets_target': max(everything) < 100,
        },
        'heatmap_ms': {
            'p50': _ms(statistics.median(runs)),
            'max': _ms(max(runs)),
            'mentions': sum(e['mentions'] for e in heat['entries']),
        },
        'selection_context_ms': {'p50': _ms(statistics.median(selection)), 'p95': _ms(_p95(selection))},
    }


def environment() -> dict:
    from byax import __version__

    return {
        'platform': platform.platform(),
        'machine': platform.machine(),
        'processor': platform.processor() or 'unknown',
        'python': platform.python_version(),
        'engine': __version__,
        'hardware_qualification': 'Unqualified execution host; reference laptops not verified.',
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--write', action='store_true', help='Refresh docs/benchmarks/0.3-codex.json')
    parser.add_argument('--samples', type=int, default=20)
    args = parser.parse_args()
    fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
    report = {
        'schema_version': 1,
        'measured_at_utc': datetime.now(UTC).isoformat(),
        'environment': environment(),
        'commands': ['uv run python scripts/codex_benchmark.py --write'],
        'mentions': {
            'fixture': 'tests/fixtures/codex/pride-and-prejudice-1-6.json (built by scripts/build_codex_fixture.py)',
            'method': 'A reported mention is correct when its entry and exact span equal a label. '
            'Precision = correct / reported; recall = correct / labelled.',
            'qualification': 'One novel excerpt (7,694 words) with name-form labels; pronouns and descriptions are '
            'out of scope, so this measures the matcher against a writer-maintained codex, not coreference.',
            'words': sum(len(p.split()) for c in fixture['chapters'] for p in c['paragraphs']),
            'with_aliases': accuracy(fixture),
            'names_only': accuracy(fixture, names_only(fixture)),
        },
        'project_search': latency(args.samples),
    }
    text = json.dumps(report, indent=2, ensure_ascii=False) + '\n'
    if args.write:
        OUTPUT.write_text(text, encoding='utf-8')
    print(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
