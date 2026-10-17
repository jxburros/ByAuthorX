#!/usr/bin/env python3
"""Compile pipeline workload (CP B2 exit criterion): the 100,000-word project to a book.

Compiles the deterministic ``project_100k`` fixture with each bundled preset: the novel preset to
print PDF (twice, to show the layout signature and page breaks repeat), the novel preset to EPUB
(twice, to show identical bytes) and the manuscript preset to Word with its round-trip check.
Timings are wall clock for the whole ``compile_document`` call (building the compiled document
through engine operations, preflight, layout, writing and, for Word, re-import and comparison).

    QT_QPA_PLATFORM=offscreen uv run python scripts/benchmark_compile.py --out artifacts/compile.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchmark import cpu_identity
from generate_fixtures import make_fixture

from byax import compile as book
from byax.model import canonical_json

DEFINITIONS = {
    'compile_seconds': 'Wall clock of one compile_document call, fixture construction excluded.',
    'layout_signature': 'layout.layout_signature of the resolved pages (spec 6.4): page sizes, folios and break decisions.',
    'page_starts_sha256': 'SHA-256 of the JSON list of the block that opens each page body (the page breaks).',
    'output_sha256': 'SHA-256 of the written file. PDF bytes embed a creation date and are not compared.',
}


def run(doc, out, preset, target):
    started = time.perf_counter()
    report = book.compile_document(doc, out, preset=preset, target=target, overwrite=True)
    seconds = time.perf_counter() - started
    data = report['compile']
    entry = {
        'preset': preset,
        'target': target,
        'compile_seconds': round(seconds, 3),
        'compiled_sha256': data['compiled']['sha256'],
        'compiled_blocks': data['compiled']['blocks'],
        'words': data['words'],
        'losses': len(report['losses']),
        'preflight': report['preflight']['summary'],
    }
    if target == 'pdf':
        entry.update(
            page_count=data['page_count'],
            layout_signature=data['layout_signature'],
            page_starts_sha256=hashlib.sha256(json.dumps(data['page_starts']).encode()).hexdigest(),
        )
    else:
        entry['output_sha256'] = hashlib.sha256(Path(out).read_bytes()).hexdigest()
    if 'roundtrip' in data:
        entry['roundtrip_differences'] = data['roundtrip']['differences']
    return entry


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--out', type=Path, default=Path('artifacts/compile-benchmark.json'))
    args = parser.parse_args()
    from PySide6.QtCore import qVersion

    from byax import __version__
    from byax.layout import ensure_app

    ensure_app()
    doc = make_fixture('project_100k')
    report = {
        'schema_version': 1,
        'measured_at_utc': datetime.now(UTC).isoformat(),
        'environment': {
            'platform': platform.platform(),
            'machine': platform.machine(),
            'processor': cpu_identity(),
            'logical_cpus': os.cpu_count(),
            'python': platform.python_version(),
            'engine': __version__,
            'qt': qVersion(),
            'qt_platform': os.environ.get('QT_QPA_PLATFORM'),
            'hardware_qualification': 'Unqualified execution host; CPU/SSD target hardware not verified.',
        },
        'fixture': {
            'name': 'project_100k',
            'semantic_sha256': hashlib.sha256(canonical_json(doc).encode()).hexdigest(),
            'blocks': len(doc['blocks']),
        },
        'definitions': DEFINITIONS,
        'runs': [],
    }
    with tempfile.TemporaryDirectory(prefix='byax-compile-bench-') as root:
        for preset, target, repeat in (('novel', 'pdf', 2), ('novel', 'epub', 2), ('manuscript', 'docx', 1)):
            for index in range(repeat):
                out = Path(root) / f'{preset}-{index}.{target}'
                report['runs'].append(run(make_fixture('project_100k'), out, preset, target))
    pdf = [r for r in report['runs'] if r['target'] == 'pdf']
    epub = [r for r in report['runs'] if r['target'] == 'epub']
    report['reproducible'] = {
        'pdf_layout_signature': len({r['layout_signature'] for r in pdf}) == 1,
        'pdf_page_breaks': len({r['page_starts_sha256'] for r in pdf}) == 1,
        'epub_bytes': len({r['output_sha256'] for r in epub}) == 1,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
