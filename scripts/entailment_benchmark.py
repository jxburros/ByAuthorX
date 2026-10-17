"""Agreement of the entailment stage with human labels (context engine plan 9; exit criterion of Phase 5).

Maintainer tool. Runs the installed entailment model over the hand-labelled pairs in
``tests/fixtures/entailment/pairs.json`` (rebuild with ``scripts/build_entailment_fixture.py``), chooses the
two thresholds on the development half (odd pair ids) and reports agreement on the held-out half (even ids)
and on all pairs. Nothing is uploaded: the model runs on this computer.

    uv run python scripts/entailment_benchmark.py --models-dir <dir with hhem-2.1-open-int8> \\
        --write docs/benchmarks/0.4-entailment.json
    uv run python scripts/entailment_benchmark.py --models-dir <dir> --reference-model <model-fp32.onnx>

The model directory is the one ``byax models --action download`` fills (``BYAX_MODELS_DIR``); build it with
``scripts/export_hhem_onnx.py`` when the mirror is not populated yet. Reported:

* the shipped method (claim split into sentences and clauses; each piece scored; supported only if every
  piece is, unsupported only if none is, partial otherwise), and the same model on the whole claim for
  comparison;
* accuracy, per-class precision and recall, the confusion matrix, and the false-positive rates for the
  category that matters most: how often an unsupported (or only partly supported) claim is called supported;
* latency per claim on this machine.
"""

import argparse
import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

PAIRS = ROOT / 'tests' / 'fixtures' / 'entailment' / 'pairs.json'
LABELS = ('supported', 'partial', 'unsupported')
GRID = [round(x * 0.05, 2) for x in range(1, 20)]


def hardware():
    model = ''
    try:
        for line in Path('/proc/cpuinfo').read_text(encoding='utf-8').splitlines():
            if line.startswith('model name'):
                model = line.split(':', 1)[1].strip()
                break
    except OSError:
        pass
    return {
        'cpu': model or platform.processor() or platform.machine(),
        'logical_cores': os.cpu_count(),
        'platform': platform.platform(),
        'python': platform.python_version(),
    }


def percentile(values, q):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


def metrics(truth, predicted):
    """Accuracy, per-class precision/recall and the confusion matrix (rows: truth, columns: prediction)."""
    matrix = {t: dict.fromkeys(LABELS, 0) for t in LABELS}
    for t, p in zip(truth, predicted, strict=True):
        matrix[t][p] += 1
    total = len(truth)
    per_class = {}
    for label in LABELS:
        tp = matrix[label][label]
        called = sum(matrix[t][label] for t in LABELS)
        actual = sum(matrix[label].values())
        per_class[label] = {
            'precision': round(tp / called, 3) if called else None,
            'recall': round(tp / actual, 3) if actual else None,
            'support': actual,
        }
    not_supported = sum(sum(matrix[t].values()) for t in ('partial', 'unsupported'))
    unsupported = sum(matrix['unsupported'].values())
    called_supported_wrongly = matrix['partial']['supported'] + matrix['unsupported']['supported']
    return {
        'pairs': total,
        'accuracy': round(sum(matrix[label][label] for label in LABELS) / total, 3),
        'macro_f1': round(
            statistics.mean(
                2 * c['precision'] * c['recall'] / (c['precision'] + c['recall'])
                if c['precision'] and c['recall']
                else 0.0
                for c in per_class.values()
            ),
            3,
        ),
        'per_class': per_class,
        'confusion': matrix,
        'false_positive_rate_supported': {
            'unsupported_called_supported': round(matrix['unsupported']['supported'] / unsupported, 3),
            'not_fully_supported_called_supported': round(called_supported_wrongly / not_supported, 3),
        },
        'supported_vs_rest': {
            'precision': per_class['supported']['precision'],
            'recall': per_class['supported']['recall'],
        },
    }


def verdicts(entries, supported_at, unsupported_at, key):
    from byax.grounding_entailment import aggregate, classify

    return [aggregate([classify(s, supported_at, unsupported_at) for s in entry[key]]) for entry in entries]


MAX_FALSE_SUPPORTED = 0.15


def choose(entries, key):
    """The thresholds with the best macro-F1 on ``entries`` among those that call at most
    ``MAX_FALSE_SUPPORTED`` of the unsupported claims supported (a wrong "supported" does more harm than a
    wrong "unsupported"); ties go to higher accuracy, then the stricter ``supported_at``."""
    best = None
    truth = [e['label'] for e in entries]
    for hi in GRID:
        for lo in GRID:
            if lo >= hi:
                continue
            result = metrics(truth, verdicts(entries, hi, lo, key))
            if result['false_positive_rate_supported']['unsupported_called_supported'] > MAX_FALSE_SUPPORTED:
                continue
            ranking = (result['macro_f1'], result['accuracy'], hi, -lo)
            if best is None or ranking > best[0]:
                best = (ranking, hi, lo)
    return {'supported_at': best[1], 'unsupported_at': best[2], 'method': key}


def operating_points(entries, lo=0.5):
    """How the trade-off moves with ``supported_at`` (the held-out half and all pairs)."""
    rows = []
    for hi in (0.5, 0.6, 0.7, 0.8, 0.9, 0.95):
        row = {'supported_at': hi, 'unsupported_at': lo}
        for name, part in (('held_out', [e for e in entries if e['id'] % 2 == 0]), ('all', entries)):
            m = metrics([e['label'] for e in part], verdicts(part, hi, lo, 'pieces'))
            row[name] = {
                'accuracy': m['accuracy'],
                'supported_recall': m['per_class']['supported']['recall'],
                'supported_precision': m['per_class']['supported']['precision'],
                'unsupported_called_supported': m['false_positive_rate_supported'][
                    'unsupported_called_supported'
                ],
            }
        rows.append(row)
    return rows


def score_all(scorer, data):
    """Per pair: scores of each claim piece and of the whole claim, plus the time taken."""
    from byax.grounding_entailment import EntailmentStage, premise_window

    stage = EntailmentStage(scorer)
    entries = []
    for pair in data['pairs']:
        premise = premise_window(data['sources'][pair['source']]['text'])
        started = time.perf_counter()
        assessed = stage.assess(pair['claim'], premise)
        whole = scorer.score([(premise, pair['claim'])])[0]
        elapsed = time.perf_counter() - started
        entries.append(
            {
                'id': pair['id'],
                'label': pair['label'],
                'kind': pair['kind'],
                'pieces': [round(score, 4) for _, score, _ in assessed] or [whole],
                'whole': [round(whole, 4)],
                'seconds': elapsed,
            }
        )
    return entries


def evaluate(entries, thresholds):
    hi, lo = thresholds['supported_at'], thresholds['unsupported_at']
    key = thresholds['method']
    development = [e for e in entries if e['id'] % 2 == 1]
    held_out = [e for e in entries if e['id'] % 2 == 0]
    out = {}
    for name, part in (('development', development), ('held_out', held_out), ('all', entries)):
        out[name] = metrics([e['label'] for e in part], verdicts(part, hi, lo, key))
    wrong = [
        {'id': e['id'], 'kind': e['kind'], 'label': e['label'], 'got': got}
        for e, got in zip(entries, verdicts(entries, hi, lo, key), strict=True)
        if e['label'] != got
    ]
    by_kind = {}
    for e, got in zip(entries, verdicts(entries, hi, lo, key), strict=True):
        row = by_kind.setdefault(e['kind'], {'pairs': 0, 'agree': 0})
        row['pairs'] += 1
        row['agree'] += e['label'] == got
    out['by_kind'] = {
        k: {**v, 'agreement': round(v['agree'] / v['pairs'], 3)} for k, v in sorted(by_kind.items())
    }
    out['disagreements'] = len(wrong)
    return out


def reference_run(args, catalogue, entry, data, thresholds):
    """The same thresholds and method with the float32 export, installed in a throwaway directory."""
    import copy
    import hashlib
    import shutil
    import tempfile

    from byax import local_models
    from byax.local_inference import EntailmentScorer

    model = Path(args.reference_model)
    patched = copy.deepcopy(catalogue)
    target = next(m for m in patched['models'] if m['name'] == args.name)
    item = next(f for f in target['files'] if f['path'] == 'model.onnx')
    item['size'] = model.stat().st_size
    item['sha256'] = hashlib.sha256(model.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory() as folder:
        destination = Path(folder) / args.name
        shutil.copytree(Path(args.models_dir) / args.name, destination)
        shutil.copyfile(model, destination / 'model.onnx')
        local_models._write_manifest(target, destination, 'reference')
        scorer = EntailmentScorer(args.name, catalogue=patched, directory=folder)
        result = evaluate(score_all(scorer, data), thresholds)
    return {'model_bytes': item['size'], 'model_sha256': item['sha256'], **result}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--models-dir', required=True, help='directory holding the installed model')
    parser.add_argument('--name', default='hhem-2.1-open-int8')
    parser.add_argument(
        '--reference-model', help='the float32 model-fp32.onnx from the export recipe, to compare'
    )
    parser.add_argument('--write', help='write the report to this JSON file')
    args = parser.parse_args(argv)
    from byax import local_models
    from byax.local_inference import EntailmentScorer

    data = json.loads(PAIRS.read_text(encoding='utf-8'))
    catalogue = local_models.load_catalogue()
    entry = local_models.get_model(args.name, catalogue)
    scorer = EntailmentScorer(args.name, catalogue=catalogue, directory=args.models_dir)
    scorer.score([('Warm-up premise text.', 'A warm-up claim.')])  # load and verify the model once
    entries = score_all(scorer, data)
    development = [e for e in entries if e['id'] % 2 == 1]
    chosen = choose(development, 'pieces')
    whole = choose(development, 'whole')
    shipped = {
        'supported_at': entry['runtime']['supported_at'],
        'unsupported_at': entry['runtime']['unsupported_at'],
        'method': 'pieces',
    }
    report = {
        'benchmark': 'entailment agreement with human labels',
        'plan': 'context-engine-plan.md section 9 (Phase 5), exit criterion',
        'date': time.strftime('%Y-%m-%d'),
        'model': {
            'name': args.name,
            'version': entry['version'],
            'upstream': entry['upstream'],
            'size_bytes': entry['size_bytes'],
            'model_sha256': next(f['sha256'] for f in entry['files'] if f['path'] == 'model.onnx'),
        },
        'hardware': hardware(),
        'fixture': {
            'file': 'tests/fixtures/entailment/pairs.json',
            'pairs': len(data['pairs']),
            'counts': data['counts'],
            'sources': len(data['sources']),
            'split': 'odd pair ids are the development half (thresholds chosen there); even ids are held out',
        },
        'labels': {
            'supported': 'the source states what the claim says (paraphrase or direct consequence)',
            'partial': 'part of the claim is stated and another substantive part is not (extra, stronger or contradicted)',
            'unsupported': 'no substantive part of the claim is stated by the source, even if it is true elsewhere',
        },
        'selected_on_development': chosen,
        'shipped_thresholds': shipped,
        'shipped': evaluate(entries, shipped),
        'operating_points': operating_points(entries),
        'whole_claim_same_model': {'selected_on_development': whole, **evaluate(entries, whole)},
        'latency_seconds_per_claim': {
            'p50': round(statistics.median(e['seconds'] for e in entries), 3),
            'p95': round(percentile([e['seconds'] for e in entries], 0.95), 3),
            'note': 'one claim incl. its pieces and a whole-claim pass, one thread, premise up to 3500 characters',
        },
    }
    if args.reference_model:
        report['float32_reference'] = reference_run(args, catalogue, entry, data, shipped)
    held = report['shipped']['held_out']
    everything = report['shipped']['all']
    report['decision'] = {
        'default': 'off',
        'bundled': False,
        'licence': 'Apache-2.0 (model card front matter of vectara/hallucination_evaluation_model and google/flan-t5-base)',
        'summary': (
            f'On the {held["pairs"]} held-out pairs the three-way verdict agrees with the human label '
            f'{held["accuracy"]:.0%} of the time (all {everything["pairs"]} pairs: {everything["accuracy"]:.0%}). '
            f'A claim called "supported" was supported {held["per_class"]["supported"]["precision"]:.0%} of the time '
            f'(all pairs: {everything["per_class"]["supported"]["precision"]:.0%}), and it found '
            f'{held["per_class"]["supported"]["recall"]:.0%} of the supported ones. '
            f'{held["false_positive_rate_supported"]["unsupported_called_supported"]:.1%} of unsupported claims were '
            f'called supported on the held-out half and {everything["false_positive_rate_supported"]["unsupported_called_supported"]:.1%} '
            f'over all pairs. "Partial" is the weakest category (precision {held["per_class"]["partial"]["precision"]:.0%}, '
            f'recall {held["per_class"]["partial"]["recall"]:.0%} held out). '
            'The stage ships advisory and off: it runs only when asked, the model is a separate download, and it is not '
            'part of the standard installer.'
        ),
    }
    text = json.dumps(report, indent=2, ensure_ascii=False) + '\n'
    if args.write:
        Path(args.write).write_text(text, encoding='utf-8')
    print(
        f'chosen on development: {chosen}; shipped {shipped}\n'
        f'held-out accuracy {held["accuracy"]} macro-F1 {held["macro_f1"]} '
        f'unsupported->supported {held["false_positive_rate_supported"]}'
    )
    print(json.dumps(report['shipped']['all'], indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
