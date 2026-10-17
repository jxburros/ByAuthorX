"""Codex mention precision/recall on the Pride and Prejudice fixture and the published benchmark."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests' / 'fixtures' / 'codex' / 'pride-and-prejudice-1-6.json'
PUBLISHED = ROOT / 'docs' / 'benchmarks' / '0.3-codex.json'


def _benchmark():
    spec = importlib.util.spec_from_file_location('codex_benchmark', ROOT / 'scripts' / 'codex_benchmark.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fixture_holds_novel_text_only_with_reviewed_labels():
    fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
    text = ' '.join(p for c in fixture['chapters'] for p in c['paragraphs'])
    assert [c['title'] for c in fixture['chapters']] == [
        f'Chapter {n}' for n in ('I', 'II', 'III', 'IV', 'V', 'VI')
    ]
    assert 'Gutenberg' not in text and 'START OF' not in text and '[Illustration' not in text
    surfaces = {g['surface'] for g in fixture['gold'] if g['entry'] == 'cx_elizabeth'}
    assert {'Elizabeth', 'Lizzy', 'Eliza', 'Miss Eliza'} <= surfaces
    assert {g['entry'] for g in fixture['gold'] if g['surface'] == 'Miss Bennet'} == {'cx_jane'}
    for label in fixture['gold']:
        paragraph = fixture['chapters'][label['chapter']]['paragraphs'][label['paragraph']]
        assert paragraph[label['start'] : label['end']] == label['surface']


def test_mention_precision_and_recall_on_the_novel_fixture():
    module = _benchmark()
    fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
    full = module.accuracy(fixture)
    assert full['labelled'] == len(fixture['gold']) > 250
    assert full['precision'] >= 0.98 and full['recall'] >= 0.98, (full['false_positives'], full['missed'])
    names = module.accuracy(fixture, module.names_only(fixture))
    assert names['recall'] < full['recall']  # aliases are what make the codex find "Lizzy" and "Darcy"
    published = json.loads(PUBLISHED.read_text(encoding='utf-8'))
    assert published['mentions']['with_aliases']['precision'] == full['precision']
    assert published['mentions']['with_aliases']['recall'] == full['recall']
    assert published['mentions']['names_only']['recall'] == names['recall']


def test_published_project_search_latency_meets_the_keyword_target():
    published = json.loads(PUBLISHED.read_text(encoding='utf-8'))['project_search']
    assert published['words'] >= 100_000
    assert published['keyword_search']['meets_target'] is True
    assert published['keyword_search']['worst_query_p95_ms'] < 100
