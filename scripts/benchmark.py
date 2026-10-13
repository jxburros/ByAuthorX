#!/usr/bin/env python3
"""Publish measured local engine/layout workloads, never remote latency as typing.

Run: QT_QPA_PLATFORM=offscreen python scripts/benchmark.py --out artifacts/benchmark.json
A display-ready GUI startup/keystroke benchmark and physical platform accessibility
checks remain separate. No metric is considered measured before this script runs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

from generate_fixtures import make_fixture

from byax.model import canonical_json


def measure(callback, samples):
    times = []
    for index in range(samples):
        start = time.perf_counter()
        callback(index)
        times.append((time.perf_counter() - start) * 1000)
    ordered = sorted(times)
    return {'samples': samples, 'median_ms': statistics.median(times),
            'p95_ms': ordered[max(0, __import__('math').ceil(.95 * samples) - 1)],
            'min_ms': min(times), 'max_ms': max(times), 'raw_ms': times}


def cpu_identity():
    value = platform.processor()
    if not value and Path('/proc/cpuinfo').exists():
        for line in Path('/proc/cpuinfo').read_text().splitlines():
            if line.startswith('model name'):
                return line.partition(':')[2].strip()
    return value or 'Unavailable'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=Path('artifacts/benchmark.json'))
    parser.add_argument('--samples', type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.samples <= 1000:
        parser.error('--samples must be between 1 and 1000')
    from PySide6.QtCore import qVersion

    from byax import __version__
    from byax.engine import apply, inspect
    from byax.layout import build_document, ensure_app, resolved_page_count
    ensure_app()
    report = {'schema_version': 1, 'measured_at_utc': datetime.now(UTC).isoformat(),
        'environment': {'platform': platform.platform(), 'machine': platform.machine(),
            'processor': cpu_identity(), 'logical_cpus': os.cpu_count(), 'python': platform.python_version(),
            'engine': __version__, 'qt': qVersion(), 'qt_platform': os.environ.get('QT_QPA_PLATFORM'),
            'hardware_qualification': 'Unqualified execution host; CPU/SSD target hardware not verified.'},
        'provider_configuration': 'None; local deterministic operations only.',
        'definitions': {'p95': 'Nearest-rank percentile of wall-clock sample durations.',
            'model_edit': 'Canonical candidate transaction against original loaded fixture; includes validation/history; excludes GUI input and paint.',
            'full_reflow': 'After a canonical paragraph insertion, construct shared QTextDocument and resolve all physical display pages; includes full layout, excludes paint and the edit itself.',
            'inspection': 'Bounded semantic inspection against an already loaded document.',
            'fixture_load': 'Fixture construction excluded from every timed operation.'},
        'not_measured': ['Cold GUI startup to usable window', 'Keystroke-to-visible-feedback latency',
            'Incremental affected-region reflow', 'Idle CPU activity', 'Cancellation acknowledgement',
            'Physical Windows 11 / Ubuntu 24.04 desktop behavior', 'Remote provider latency'],
        'fixtures': {}}
    for name in ('mixed_100', 'long_500'):
        doc = make_fixture(name)
        layout = build_document(doc)
        entry = {'semantic_sha256': hashlib.sha256(canonical_json(doc).encode()).hexdigest(),
            'blocks': len(doc['blocks']), 'text_characters': sum(len(b.get('text', '')) for b in doc['blocks']),
            'requested_pages': doc['settings']['fixture']['requested_pages'], 'measured_pages': resolved_page_count(layout, doc),
            'layout_findings_count': len(layout.byax_findings), 'pagination': 'Explicit page breaks',
            'metrics': {}}
        target = 'paragraph_50' if name == 'mixed_100' else 'paragraph_250'
        edit = {'expected_revision': doc['revision'], 'operations': [{'type': 'insert_text', 'target': target, 'offset': 0, 'text': 'Updated. '}]}
        entry['metrics']['model_edit'] = measure(lambda _, doc=doc, edit=edit: apply(doc, edit), args.samples)
        edited_doc, _ = apply(doc, edit)
        entry['metrics']['full_reflow'] = measure(lambda _, edited_doc=edited_doc: resolved_page_count(build_document(edited_doc), edited_doc), args.samples)
        entry['metrics']['inspection'] = measure(lambda _, doc=doc, target=target: inspect(doc, target=target, limit=10), args.samples)
        entry['target_comparison'] = {'typing_50ms': 'not_measured: model transaction excludes GUI feedback',
            'reflow_500ms': 'full reflow proxy only; affected-region GUI feedback unmeasured',
            'inspection_500ms': entry['metrics']['inspection']['p95_ms'] <= 500}
        report['fixtures'][name] = entry
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
