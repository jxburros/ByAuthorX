"""Precision and recall of the fiction checks on the seeded fixture (docs/benchmarks/0.3-fiction-checks.json).

    uv run python scripts/fiction_benchmark.py            # print the numbers
    uv run python scripts/fiction_benchmark.py --write    # refresh the published JSON

The seeded fixture is hand-annotated (tests/fixtures/fiction/seeded-expected.json) before any run. A
finding counts as correct when its rule and matched text equal an annotation. The clean fixture must
produce no findings. These are small, curated fixtures: they show that each rule fires where intended
and stays quiet on ordinary prose, not how it performs on a novel.
"""

import argparse
import json
import platform
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests' / 'fixtures' / 'fiction'
OUTPUT = ROOT / 'docs' / 'benchmarks' / '0.3-fiction-checks.json'
PERFORMANCE = ROOT / 'docs' / 'benchmarks' / '0.3-fiction-performance.json'
COPIES = 205  # copies of the seeded fixture: about 100,000 words


def _findings(name):
    from byax.checks import run_checks
    from byax.exchange import import_document

    doc, _ = import_document(str(FIXTURES / name))
    doc['profiles']['fixture'] = json.loads((FIXTURES / 'profile.json').read_text(encoding='utf-8'))
    report = run_checks(doc, ['fiction'], profile='fixture', limit=10**6)
    return [(f['rule'], f['text']) for f in report['findings']]


def _expected(name):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))['findings']


def _match(found, annotations):
    """Count findings and annotations by (rule, text); whole-paragraph ranges match on a prefix."""
    prefixes = {i['rule']: i['text_starts'] for i in annotations if 'text_starts' in i}
    keys = [
        (rule, prefixes[rule] if rule in prefixes and text.startswith(prefixes[rule]) else text)
        for rule, text in found
    ]
    return Counter(keys), Counter((i['rule'], i.get('text') or i['text_starts']) for i in annotations)


def _tally(fixture, annotations):
    """Per-rule (true positives, reported, annotated) for one annotated fixture."""
    from byax.fiction_checks import RULES

    got, want = _match(_findings(fixture), _expected(annotations))
    matched = got & want
    return {
        rule: (
            sum(v for k, v in matched.items() if k[0] == rule),
            sum(v for k, v in got.items() if k[0] == rule),
            sum(v for k, v in want.items() if k[0] == rule),
        )
        for rule in sorted(RULES)
    }


def _report(tallies):
    """Scores per rule, per group and overall from {rule: (tp, reported, annotated)}."""
    from byax.fiction_checks import GROUPS

    rules = {rule: {**_scores(*counts), 'group': GROUPS[rule]} for rule, counts in tallies.items()}
    groups = {}
    for group in sorted(set(GROUPS.values())):
        members = [r for r in rules if rules[r]['group'] == group]
        groups[group] = {
            **_scores(*(sum(tallies[r][i] for r in members) for i in range(3))),
            'rules': members,
        }
    overall = _scores(*(sum(c[i] for c in tallies.values()) for i in range(3)))
    return {'overall': overall, 'groups': groups, 'rules': rules}


def measure():
    from byax.fiction_checks import VERSION, versions

    seeded = _tally('seeded.md', 'seeded-expected.json')
    limits = _tally('limits.md', 'limits-expected.json')
    combined = {r: tuple(a + b for a, b in zip(seeded[r], limits[r])) for r in seeded}
    return {
        'schema_version': 1,
        'suite': 'fiction',
        'rule_set_version': VERSION,
        'word_lists': versions(),
        'fixtures': {
            'seeded': 'tests/fixtures/fiction/seeded.md (annotations: seeded-expected.json)',
            'limits': 'tests/fixtures/fiction/limits.md (annotations: limits-expected.json)',
            'clean': 'tests/fixtures/fiction/clean.md',
            'profile': 'tests/fixtures/fiction/profile.json',
        },
        'method': 'Hand-annotated fixtures; a finding is correct when rule and matched text equal an '
        'annotation. Precision = correct / reported; recall = correct / annotated.',
        'qualification': 'Small curated fixtures. "seeded" shows each rule firing where intended; "limits" '
        'probes documented heuristic limits (a literal "felt", an unlisted way of telling an emotion, a '
        'distinct character with a similar name). Neither is an accuracy estimate for a full manuscript.',
        'seeded': _report(seeded),
        'limits': _report(limits),
        'combined': _report(combined),
        'clean': {'findings': len(_findings('clean.md'))},
    }


def _scores(true_positive, reported, annotated):
    return {
        'true_positives': true_positive,
        'reported': reported,
        'annotated': annotated,
        'precision': round(true_positive / reported, 3) if reported else None,
        'recall': round(true_positive / annotated, 3) if annotated else None,
    }


def performance():
    """Wall-clock cost on a 100,000-word manuscript: full run, one-paragraph incremental run, report."""
    from byax import engine, readability
    from byax.checks import run_checks
    from byax.exchange import import_document

    doc, _ = import_document(str(FIXTURES / 'seeded.md'))
    template = doc['blocks']
    doc['blocks'] = [
        {**b, 'id': f'{b["id"]}_{copy}', 'marks': []} for copy in range(COPIES) for b in template
    ]
    words = sum(len(b.get('text', '').split()) for b in doc['blocks'])
    target = next(b['id'] for b in doc['blocks'] if b.get('text', '').startswith('The silver'))
    edit = {'operations': [{'type': 'set_text', 'target': target, 'text': 'The lantern swung.'}]}
    changed, _ = engine.apply(doc, edit)
    profile = json.loads((FIXTURES / 'profile.json').read_text(encoding='utf-8'))
    result = {}
    for name, settings in (('fixture_profile_all_rules', profile), ('default_settings', None)):
        start = time.perf_counter()
        first = run_checks(doc, ['fiction'], profile=settings, limit=10**6, track=True)
        full = time.perf_counter() - start
        start = time.perf_counter()
        again = run_checks(changed, ['fiction'], profile=settings, limit=10**6, previous=first, track=True)
        incremental = time.perf_counter() - start
        result[name] = {
            'full_seconds': round(full, 2),
            'one_paragraph_edit_incremental_seconds': round(incremental, 2),
            'findings': len(first['findings']),
            'units_rechecked_after_edit': len(again['evaluation']['rechecked']),
            'units_reused_after_edit': len(again['evaluation']['reused']),
        }
    start = time.perf_counter()
    readability.readability_report(doc)
    result['readability_report_seconds'] = round(time.perf_counter() - start, 2)
    return {
        'schema_version': 1,
        'measured_at_utc': datetime.now(UTC).isoformat(),
        'environment': {
            'platform': platform.platform(),
            'machine': platform.machine(),
            'python': platform.python_version(),
        },
        'fixture': f'tests/fixtures/fiction/seeded.md repeated {COPIES} times',
        'words': words,
        'blocks': len(doc['blocks']),
        'results': result,
        'qualification': 'Single run on an unqualified execution host: an order of magnitude, not a target.',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--write', action='store_true', help='Write the JSON under docs/benchmarks')
    parser.add_argument(
        '--performance',
        action='store_true',
        help='Time a 100,000-word manuscript (docs/benchmarks/0.3-fiction-performance.json)',
    )
    options = parser.parse_args()
    result, output = (performance(), PERFORMANCE) if options.performance else (measure(), OUTPUT)
    text = json.dumps(result, indent=2, ensure_ascii=False) + '\n'
    if options.write:
        output.write_text(text, encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
