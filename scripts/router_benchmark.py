#!/usr/bin/env python3
"""Route accuracy of the assistant router on a labelled request set (docs/benchmarks/0.3-router.json).

    uv run python scripts/router_benchmark.py            # print the numbers
    uv run python scripts/router_benchmark.py --write    # refresh the published JSON

Fixture: tests/fixtures/router/novel.json (a synthetic four-chapter novel with a binder, statuses and a
codex, one entry hidden from AI) and tests/fixtures/router/requests.jsonl, one labelled request per
line: the request text, an optional selection (a whole block) or cursor block, the document (``novel``,
or ``large``: the same novel with every scene repeated to about 34,000 tokens), the model (``local-8k``
by default) and the route a writer would expect from the plan's route table. The labels were written
from the plan's route definitions before the numbers were measured and are not tuned to the rules; a
few requests the rules are known to misread are kept and marked with a note.

``byax.router.decide`` is deterministic and needs no model, network or index, so accuracy is exact
for this set. It measures the rules against one writer's labels on one synthetic manuscript, not how
well a route serves a real request.
"""

from __future__ import annotations

import argparse
import copy
import json
import platform
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
FIXTURES = ROOT / 'tests' / 'fixtures' / 'router'
OUTPUT = ROOT / 'docs' / 'benchmarks' / '0.3-router.json'
LARGE_REPEAT = 20


def _heading(identity, text, level):
    return {
        'id': identity,
        'type': 'heading',
        'text': text,
        'style': f'heading{level}',
        'marks': [],
        'attrs': {'level': level},
    }


def _paragraph(identity, text):
    return {'id': identity, 'type': 'paragraph', 'text': text, 'style': 'body', 'marks': [], 'attrs': {}}


def fixture_document(*, repeat: int = 1) -> dict:
    """The fixture novel as a document: binder (chapters and scenes), statuses, one synopsis, codex.

    ``repeat`` repeats every scene's paragraphs (with a numbered variation) to make a long manuscript.
    """
    from byax import engine, project
    from byax.model import new_document

    spec = json.loads((FIXTURES / 'novel.json').read_text(encoding='utf-8'))
    doc = new_document(spec['title'])
    blocks = []
    for c, chapter in enumerate(spec['chapters'], 1):
        blocks.append(_heading(f'c{c}', chapter['title'], 1))
        for s, scene in enumerate(chapter['scenes'], 1):
            blocks.append(_heading(f'c{c}s{s}', scene['title'], 2))
            for r in range(repeat):
                for p, text in enumerate(scene['paragraphs'], 1):
                    suffix = '' if r == 0 else f'-{r}'
                    body = text if r == 0 else f'{text} The hour was {r} past the turn of the tide.'
                    blocks.append(_paragraph(f'c{c}s{s}p{p}{suffix}', body))
    doc['blocks'] = blocks
    doc, _ = engine.apply(
        doc, {'operations': [{'type': 'project_init', 'kinds': {'1': 'chapter', '2': 'scene'}}]}
    )
    operations = []
    for c, chapter in enumerate(spec['chapters'], 1):
        item = project.item_id_for(f'c{c}')
        operations.append({'type': 'project_set_status', 'target': item, 'status': chapter['status']})
        if chapter.get('synopsis'):
            operations.append(
                {'type': 'project_set_meta', 'target': item, 'values': {'synopsis': chapter['synopsis']}}
            )
    operations.append({'type': 'project_set_target', 'words': 80000})
    for entry in spec['codex']:
        values = {k: copy.deepcopy(v) for k, v in entry.items() if k != 'id'}
        operations.append({'type': 'codex_add', 'id': entry['id'], 'values': values})
    doc, _ = engine.apply(doc, {'operations': operations})
    return doc


def models() -> dict:
    """The model configurations requests name (no request is ever sent)."""
    from byax.providers import ProviderConfig

    return {
        'none': None,
        'local-8k': ProviderConfig('local-8k', 'ollama', 'fixture-8k', max_context_tokens=8192),
        'local-32k': ProviderConfig(
            'local-32k', 'ollama', 'fixture-32k', max_context_tokens=32768, context_tokens=32768
        ),
        'remote-unverified': ProviderConfig('remote-unverified', 'openai', 'fixture-remote'),
        'remote-200k': ProviderConfig(
            'remote-200k', 'anthropic', 'fixture-long', context_tokens=200_000, max_input_chars=1_000_000
        ),
    }


def load_requests(path=FIXTURES / 'requests.jsonl') -> list[dict]:
    rows = []
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _selection(doc, block_id):
    from byax.model import find_object

    text = find_object(doc, block_id).get('text', '')
    return [{'target': block_id, 'start': 0, 'end': len(text), 'text': text}]


def measure(requests: list[dict] | None = None) -> dict:
    """Route accuracy with a per-route breakdown, the confusion matrix and every miss."""
    from byax import router

    requests = requests if requests is not None else load_requests()
    documents = {'novel': fixture_document(), 'large': fixture_document(repeat=LARGE_REPEAT)}
    configs = models()
    per_route: dict[str, Counter] = defaultdict(Counter)
    confusion: dict[str, Counter] = defaultdict(Counter)
    misses, timings = [], []
    for row in requests:
        doc = documents[row.get('doc', 'novel')]
        selection = _selection(doc, row['selection']) if row.get('selection') else None
        started = time.perf_counter()
        decision = router.decide(
            row['request'],
            doc,
            selection=selection,
            cursor=row.get('cursor'),
            provider=configs[row.get('model', 'local-8k')],
            libraries=[],
        )
        timings.append(time.perf_counter() - started)
        expected, observed = row['expected'], decision['route']
        per_route[expected]['labelled'] += 1
        per_route[expected]['correct'] += expected == observed
        confusion[expected][observed] += 1
        if expected != observed:
            misses.append(
                {
                    'id': row['id'],
                    'request': row['request'],
                    'expected': expected,
                    'observed': observed,
                    'rule': decision['rule'],
                    **({'note': row['note']} if row.get('note') else {}),
                }
            )
    correct = sum(c['correct'] for c in per_route.values())
    timings.sort()
    return {
        'requests': len(requests),
        'correct': correct,
        'accuracy': round(correct / len(requests), 4) if requests else None,
        'per_route': {
            route: {
                'labelled': counts['labelled'],
                'correct': counts['correct'],
                'accuracy': round(counts['correct'] / counts['labelled'], 4),
            }
            for route, counts in sorted(per_route.items())
        },
        'confusion': {route: dict(sorted(counts.items())) for route, counts in sorted(confusion.items())},
        'misses': misses,
        'decide_ms': {
            'median': round(timings[len(timings) // 2] * 1000, 3),
            'p95': round(timings[min(len(timings) - 1, int(len(timings) * 0.95))] * 1000, 3),
        },
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
    parser.add_argument('--write', action='store_true', help='Refresh docs/benchmarks/0.3-router.json')
    args = parser.parse_args()
    result = measure()
    report = {
        'schema_version': 1,
        'measured_at_utc': datetime.now(UTC).isoformat(),
        'environment': environment(),
        'commands': ['uv run python scripts/router_benchmark.py --write'],
        'fixture': {
            'document': 'tests/fixtures/router/novel.json (synthetic; built by fixture_document)',
            'requests': 'tests/fixtures/router/requests.jsonl',
            'models': sorted(models()),
        },
        'method': 'byax.router.decide on each labelled request; a request is correct when the decided route '
        'equals its label. Accuracy = correct / requests. Deterministic: no model, network or index.',
        'qualification': "One writer's labels on one synthetic manuscript, written from the plan's route "
        'table; it measures the rules, not answer quality. Learned routing is not used.',
        'routing': result,
    }
    text = json.dumps(report, indent=2, ensure_ascii=False) + '\n'
    if args.write:
        OUTPUT.write_text(text, encoding='utf-8')
    print(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
