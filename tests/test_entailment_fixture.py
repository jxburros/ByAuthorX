"""The labelled claim/source set, the published agreement numbers, and the real-model check.

Plan Phase 5 exit criterion: "Entailment agreement and false-positive rates are published in
``docs/benchmarks/``". The numbers in ``0.4-entailment.json`` are produced by
``scripts/entailment_benchmark.py`` with the real model; here they are checked for internal consistency
and against the catalogue, and (under the ``eval_models`` marker, with the model installed) reproduced.
"""

import importlib.util
import json
import os
from pathlib import Path

import pytest

from byax import local_models

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests' / 'fixtures' / 'entailment' / 'pairs.json'
PUBLISHED = ROOT / 'docs' / 'benchmarks' / '0.4-entailment.json'
LABELS = ('supported', 'partial', 'unsupported')


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def data():
    return json.loads(FIXTURE.read_text(encoding='utf-8'))


@pytest.fixture(scope='module')
def published():
    return json.loads(PUBLISHED.read_text(encoding='utf-8'))


def test_the_labelled_set_is_large_balanced_and_well_formed(data):
    pairs = data['pairs']
    assert len(pairs) >= 150
    assert [p['id'] for p in pairs] == list(range(1, len(pairs) + 1))
    counts = {label: sum(p['label'] == label for p in pairs) for label in LABELS}
    assert counts == data['counts'] and all(count >= 50 for count in counts.values()), counts
    sources = data['sources']
    assert {p['source'] for p in pairs} <= set(sources)
    assert {s['origin'] for s in sources.values()} == {'pp', 'novel', 'research'}
    for pair in pairs:
        assert pair['claim'].strip() and pair['label'] in LABELS and pair['kind']
        assert pair['claim'] != sources[pair['source']]['text']
    # Each source is used with all three labels, so a model cannot score well by source alone.
    for key in sources:
        assert {p['label'] for p in pairs if p['source'] == key} == set(LABELS), key
    assert len({(p['source'], p['claim']) for p in pairs}) == len(pairs)


def test_the_fixture_matches_its_builder(data):
    module = load_script('build_entailment_fixture')
    assert json.dumps(module.build(), indent=1, ensure_ascii=False) + '\n' == FIXTURE.read_text(
        encoding='utf-8'
    )


def test_the_published_numbers_describe_this_fixture_and_the_shipped_thresholds(data, published):
    entry = local_models.get_model('hhem-2.1-open-int8')
    assert (
        published['fixture']['pairs'] == len(data['pairs'])
        and published['fixture']['counts'] == data['counts']
    )
    assert published['shipped_thresholds'] == {
        'supported_at': entry['runtime']['supported_at'],
        'unsupported_at': entry['runtime']['unsupported_at'],
        'method': 'pieces',
    }
    assert published['selected_on_development'] == published['shipped_thresholds']
    assert published['model']['model_sha256'] == next(
        f['sha256'] for f in entry['files'] if f['path'] == 'model.onnx'
    )
    assert published['model']['upstream']['revision'] == entry['upstream']['revision']
    assert published['hardware']['cpu'] and published['latency_seconds_per_claim']['p95'] > 0


@pytest.mark.parametrize('section', ['development', 'held_out', 'all'])
def test_every_published_figure_follows_from_its_confusion_matrix(data, published, section):
    for block in (published['shipped'], published['whole_claim_same_model'], published['float32_reference']):
        part = block[section]
        matrix = part['confusion']
        total = sum(sum(row.values()) for row in matrix.values())
        assert total == part['pairs']
        assert part['accuracy'] == round(sum(matrix[label][label] for label in LABELS) / total, 3)
        for label in LABELS:
            actual = sum(matrix[label].values())
            called = sum(matrix[t][label] for t in LABELS)
            row = part['per_class'][label]
            assert row['support'] == actual
            assert row['recall'] == round(matrix[label][label] / actual, 3)
            assert row['precision'] == round(matrix[label][label] / called, 3)
        unsupported = sum(matrix['unsupported'].values())
        rest = unsupported + sum(matrix['partial'].values())
        fp = part['false_positive_rate_supported']
        assert fp['unsupported_called_supported'] == round(
            matrix['unsupported']['supported'] / unsupported, 3
        )
        assert fp['not_fully_supported_called_supported'] == round(
            (matrix['unsupported']['supported'] + matrix['partial']['supported']) / rest, 3
        )
    sizes = {s: published['shipped'][s]['pairs'] for s in ('development', 'held_out', 'all')}
    assert sizes['development'] + sizes['held_out'] == sizes['all'] == len(data['pairs'])
    truth = {label: sum(p['label'] == label for p in data['pairs']) for label in LABELS}
    for label in LABELS:
        assert published['shipped']['all']['per_class'][label]['support'] == truth[label]


def test_the_shipped_agreement_is_reported_honestly(published):
    """The measured numbers are modest, so the stage is advisory and opt-in; the claim in the docs follows."""
    held = published['shipped']['held_out']
    assert held['accuracy'] >= 0.65 and held['per_class']['supported']['precision'] >= 0.8
    assert held['false_positive_rate_supported']['unsupported_called_supported'] <= 0.15
    # Splitting a claim into pieces is what lets "partial" exist at all: scoring whole claims never does.
    whole = published['whole_claim_same_model']['held_out']
    assert held['per_class']['partial']['recall'] > whole['per_class']['partial']['recall']
    assert published['decision']['default'] == 'off' and published['decision']['bundled'] is False


@pytest.mark.eval_models
@pytest.mark.skipif(
    not os.environ.get('BYAX_REAL_MODELS_DIR'),
    reason='set BYAX_REAL_MODELS_DIR to a folder with the model installed',
)
def test_the_real_model_reproduces_the_published_agreement(data, published, monkeypatch):
    """Maintainer check (``pytest -m eval_models``): the installed model gives the published numbers."""
    from byax.local_inference import EntailmentScorer

    monkeypatch.setenv('BYAX_MODELS_DIR', os.environ['BYAX_REAL_MODELS_DIR'])
    module = load_script('entailment_benchmark')
    scorer = EntailmentScorer('hhem-2.1-open-int8')
    entries = module.score_all(scorer, data)
    shipped = published['shipped_thresholds']
    measured = module.evaluate(entries, shipped)
    for section in ('development', 'held_out', 'all'):
        assert abs(measured[section]['accuracy'] - published['shipped'][section]['accuracy']) <= 0.02, section
    # The model card's own example: a factual-looking but unsupported claim is not called supported.
    berlin, paris = 'The capital of France is Berlin.', 'The capital of France is Paris.'
    wrong, right = scorer.score(
        [
            (berlin, paris),
            ('A person on a horse jumps over a broken down airplane.', 'A person is outdoors, on a horse.'),
        ]
    )
    assert wrong < 0.5 < right
