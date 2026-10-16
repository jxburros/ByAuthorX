"""The published precision and recall for the fiction checks are the numbers the code produces today."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('fiction_benchmark', ROOT / 'scripts' / 'fiction_benchmark.py')
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def test_published_numbers_match_a_fresh_measurement():
    published = json.loads(
        (ROOT / 'docs' / 'benchmarks' / '0.3-fiction-checks.json').read_text(encoding='utf-8')
    )
    assert benchmark.measure() == published, 'run: uv run python scripts/fiction_benchmark.py --write'


def test_seeded_fixture_is_exact_and_the_limits_fixture_shows_honest_misses():
    result = benchmark.measure()
    assert result['clean']['findings'] == 0
    seeded = result['seeded']
    assert seeded['overall']['precision'] == seeded['overall']['recall'] == 1.0
    assert all(
        r['reported'] == r['annotated'] for r in seeded['rules'].values() if r['annotated'] or r['reported']
    )
    assert set(seeded['groups']) == {'dialogue', 'names', 'repetition', 'rhythm', 'sensory', 'style'}
    assert {rule for rule, r in seeded['rules'].items() if r['annotated']} == set(seeded['rules'])
    limits = result['limits']
    assert limits['rules']['fiction.filter_words']['precision'] < 0.5  # a literal "felt" is reported
    assert limits['rules']['fiction.show_tell']['recall'] == 0.0  # "was filled with fury" is missed
    assert limits['rules']['fiction.name_spelling']['precision'] == 0.0  # a distinct similar name
