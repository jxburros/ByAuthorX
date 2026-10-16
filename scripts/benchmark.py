#!/usr/bin/env python3
"""Publish measured local engine/layout workloads, never remote latency as typing.

Run: QT_QPA_PLATFORM=offscreen python scripts/benchmark.py --out artifacts/benchmark.json
GUI metrics (startup to window, keystroke to paint, structural edit to paint,
save blocking and idle CPU) run the real MainWindow on the current Qt platform;
pass --no-gui for engine/layout timings only. Offscreen numbers exclude the
compositor and GPU, so installed-desktop measurements remain separate. No metric
is considered measured before this script runs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
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
    return {
        'samples': samples,
        'median_ms': statistics.median(times),
        'p95_ms': ordered[max(0, __import__('math').ceil(0.95 * samples) - 1)],
        'min_ms': min(times),
        'max_ms': max(times),
        'raw_ms': times,
    }


def cpu_identity():
    value = platform.processor()
    if value in ('', platform.machine()) and Path('/proc/cpuinfo').exists():
        for line in Path('/proc/cpuinfo').read_text().splitlines():
            if line.startswith('model name'):
                return line.partition(':')[2].strip()
    return value or 'Unavailable'


STARTUP_PROBE = """
import os, sys, time
from PySide6.QtWidgets import QApplication
app = QApplication([])
from byax.gui import MainWindow
window = MainWindow(sys.argv[1])
window.dirty = False
window.show()
deadline = time.monotonic() + 60
while not (window.windowHandle() and window.windowHandle().isExposed()) and time.monotonic() < deadline:
    app.processEvents()
window.editor.viewport().repaint()
app.processEvents()
print('ready', flush=True)
window.dirty = False
"""


def _isolated_env(root):
    env = dict(os.environ)
    for key in ('XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME'):
        env[key] = str(Path(root) / key.lower())
    env['PYTHONPATH'] = os.pathsep.join(
        [str(Path(__file__).resolve().parents[1] / 'src'), env.get('PYTHONPATH', '')]
    )
    return env


def gui_metrics(doc, args):
    """Run the real desktop window; every timing goes through the Qt event loop."""
    from PySide6.QtCore import QEventLoop, Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from byax import storage
    from byax.gui import MainWindow

    app = QApplication.instance()
    with tempfile.TemporaryDirectory(prefix='byax-bench-') as root:
        path = Path(root) / 'fixture.byax'
        storage.save_document(doc, path)
        env = _isolated_env(root)
        startup = []
        for _ in range(args.startup_samples):
            start = time.perf_counter()
            process = subprocess.run(
                [sys.executable, '-c', STARTUP_PROBE, str(path)],
                env=env,
                capture_output=True,
                text=True,
                timeout=300,
                check=True,
            )
            if 'ready' not in process.stdout:
                raise RuntimeError('Startup probe did not report a ready window.')
            startup.append((time.perf_counter() - start) * 1000)

        def summary(times):
            ordered = sorted(times)
            return {
                'samples': len(times),
                'median_ms': statistics.median(times),
                'p95_ms': ordered[max(0, __import__('math').ceil(0.95 * len(times)) - 1)],
                'min_ms': min(times),
                'max_ms': max(times),
                'raw_ms': times,
            }

        def settle(seconds=0.2):
            loop = QEventLoop()
            QTimer.singleShot(int(seconds * 1000), loop.quit)
            loop.exec()

        window = MainWindow()
        window.recovery_dir = Path(root) / 'recovery'
        window.recovery_dir.mkdir()
        errors = []
        window.error = lambda error: errors.append(str(error))
        window.show()
        window.open_path(path)
        window.set_file_view(args.file_view)
        settle(0.5)
        paragraphs = [b['id'] for b in window.doc['blocks'] if b['type'] == 'paragraph']
        middle = paragraphs[len(paragraphs) // 2]
        window.editor.navigate(middle)
        window.editor.setFocus()
        keystroke, commit = [], []
        for index in range(args.samples):
            start = time.perf_counter()
            QTest.keyClick(window.editor, Qt.Key_A)
            window.editor.viewport().repaint()
            keystroke.append((time.perf_counter() - start) * 1000)
            start = time.perf_counter()
            window.flush_edits()
            commit.append((time.perf_counter() - start) * 1000)
        structural, rebuild = [], []
        for index in range(args.samples):
            target = paragraphs[(index * 7) % len(paragraphs)]
            window.editor.navigate(target)
            start = time.perf_counter()
            window.apply_style('heading2' if index % 2 == 0 else 'body')
            window.editor.viewport().repaint()
            structural.append((time.perf_counter() - start) * 1000)
            settle(0.01)
        for index in range(max(3, args.samples // 4)):
            start = time.perf_counter()
            window.refresh()
            window.editor.viewport().repaint()
            rebuild.append((time.perf_counter() - start) * 1000)
        window.editor.navigate(middle)
        for _ in range(args.save_edits):
            window.editor.insertPlainText('x')
            window.flush_edits()
        history_bytes = len(json.dumps(window.doc['history']))
        block, completion = [], []
        for index in range(max(3, args.samples // 4)):
            window.editor.insertPlainText('s')
            window.flush_edits()
            start = time.perf_counter()
            window.save()
            block.append((time.perf_counter() - start) * 1000)
            window.wait_for_save()
            completion.append((time.perf_counter() - start) * 1000)
        settle(1.0)
        wall, cpu = time.perf_counter(), time.process_time()
        settle(args.idle_seconds)
        wall, cpu = time.perf_counter() - wall, time.process_time() - cpu
        window.dirty = False
        window.close()
        window.deleteLater()
        app.processEvents()
    return {
        'metrics': {
            'gui_startup_to_window': summary(startup),
            'keystroke_to_paint': summary(keystroke),
            'typing_commit': summary(commit),
            'structural_edit_to_paint': summary(structural),
            'structural_full_rebuild_to_paint': summary(rebuild),
            'save_ui_block': summary(block),
            'save_completion': summary(completion),
        },
        'idle': {
            'seconds': round(wall, 3),
            'cpu_seconds': round(cpu, 4),
            'idle_cpu_percent': round(100 * cpu / wall, 3),
        },
        'history_after_edits': {'edits': args.save_edits, 'history_json_bytes': history_bytes},
        'errors': errors,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=Path('artifacts/benchmark.json'))
    parser.add_argument(
        '--file-view', choices=['byax', 'pdf', 'docx', 'odt', 'rtf', 'html', 'md', 'txt'], default='byax'
    )
    parser.add_argument(
        '--scaling',
        action='store_true',
        help='Also measure 100/500/2000 flowing pages and up to 15000 blocks.',
    )
    parser.add_argument(
        '--project',
        action='store_true',
        help='Also measure the 100,000-word manuscript project: open, reorder a chapter, save.',
    )
    parser.add_argument(
        '--docx-review',
        action='store_true',
        help='Also measure the Word review round trip on the 100,000-word manuscript.',
    )
    parser.add_argument('--samples', type=int, default=20)
    parser.add_argument('--no-gui', action='store_true', help='Skip GUI startup/latency/idle metrics.')
    parser.add_argument('--startup-samples', type=int, default=5)
    parser.add_argument('--idle-seconds', type=float, default=10.0)
    parser.add_argument('--save-edits', type=int, default=1000)
    args = parser.parse_args()
    if not 1 <= args.samples <= 1000:
        parser.error('--samples must be between 1 and 1000')
    from PySide6.QtCore import qVersion

    from byax import __version__
    from byax.engine import apply, inspect
    from byax.layout import build_document, ensure_app, resolved_page_count

    ensure_app()
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
        'file_view': args.file_view,
        'provider_configuration': 'None; local deterministic operations only.',
        'definitions': {
            'p95': 'Nearest-rank percentile of wall-clock sample durations.',
            'model_edit': 'Canonical candidate transaction against original loaded fixture; includes validation/history; excludes GUI input and paint.',
            'full_reflow': 'After a canonical paragraph insertion, construct shared QTextDocument and resolve all physical display pages; includes full layout, excludes paint and the edit itself.',
            'inspection': 'Bounded semantic inspection against an already loaded document.',
            'fixture_load': 'Fixture construction excluded from every timed operation.',
            'gui_startup_to_window': 'Fresh interpreter process launch until MainWindow with the fixture is shown, exposed and its first paint processed; includes Python/Qt import and package load.',
            'keystroke_to_paint': 'QTest key press delivered through the event loop to the editor plus a synchronous viewport repaint; excludes the 300 ms idle commit.',
            'typing_commit': 'flush_edits after one keystroke: canonical transaction, history delta and sidebar/status refresh.',
            'structural_edit_to_paint': 'Paragraph style change from the GUI command through commit, editor document update and synchronous viewport repaint.',
            'structural_full_rebuild_to_paint': 'Same edit forced through a full shared-document rebuild, for comparison.',
            'save_ui_block': 'Time save() holds the UI thread before returning to the event loop; serialization continues on a worker.',
            'save_completion': 'Time until the worker save finishes and the window marks the document saved.',
            'idle_cpu_percent': 'Process CPU time divided by wall time while the loaded window runs its event loop without input.',
        },
        'not_measured': [
            'Cancellation acknowledgement',
            'Physical Windows 11 / Ubuntu 24.04 desktop behavior',
            'Remote provider latency',
        ],
        'fixtures': {},
    }
    for name in ('mixed_100', 'long_500'):
        doc = make_fixture(name)
        layout = build_document(doc)
        entry = {
            'semantic_sha256': hashlib.sha256(canonical_json(doc).encode()).hexdigest(),
            'blocks': len(doc['blocks']),
            'text_characters': sum(len(b.get('text', '')) for b in doc['blocks']),
            'requested_pages': doc['settings']['fixture']['requested_pages'],
            'measured_pages': resolved_page_count(layout, doc),
            'layout_findings_count': len(layout.byax_findings),
            'pagination': 'Explicit page breaks',
            'metrics': {},
        }
        target = 'paragraph_50' if name == 'mixed_100' else 'paragraph_250'
        edit = {
            'expected_revision': doc['revision'],
            'operations': [{'type': 'insert_text', 'target': target, 'offset': 0, 'text': 'Updated. '}],
        }
        entry['metrics']['model_edit'] = measure(lambda _, doc=doc, edit=edit: apply(doc, edit), args.samples)
        edited_doc, _ = apply(doc, edit)
        entry['metrics']['full_reflow'] = measure(
            lambda _, edited_doc=edited_doc: resolved_page_count(build_document(edited_doc), edited_doc),
            args.samples,
        )
        entry['metrics']['inspection'] = measure(
            lambda _, doc=doc, target=target: inspect(doc, target=target, limit=10), args.samples
        )
        entry['target_comparison'] = {
            'typing_50ms': 'not_measured: model transaction excludes GUI feedback',
            'reflow_500ms': 'full reflow proxy only; affected-region GUI feedback unmeasured',
            'inspection_500ms': entry['metrics']['inspection']['p95_ms'] <= 500,
        }
        report['fixtures'][name] = entry
    if not args.no_gui:
        for name in ('mixed_100', 'long_500'):
            gui = gui_metrics(make_fixture(name), args)
            entry = report['fixtures'][name]
            entry['metrics'].update(gui.pop('metrics'))
            entry['gui'] = gui
            entry['target_comparison'].update(
                {
                    'typing_50ms': entry['metrics']['keystroke_to_paint']['p95_ms'] <= 50,
                    'reflow_500ms': entry['metrics']['structural_edit_to_paint']['p95_ms'] <= 500,
                    'startup_3s': entry['metrics']['gui_startup_to_window']['p95_ms'] <= 3000,
                }
            )
    if args.project:
        from benchmark_project import project_metrics

        report['fixtures']['project_100k'] = project_metrics(measure, args.samples)
        if not args.no_gui:
            gui = gui_metrics(make_fixture('project_100k'), args)
            entry = report['fixtures']['project_100k']
            entry['metrics'].update(gui.pop('metrics'))
            entry['gui'] = gui
            entry['target_comparison'].update(
                {
                    'typing_50ms': entry['metrics']['keystroke_to_paint']['p95_ms'] <= 50,
                    'structural_500ms': entry['metrics']['structural_edit_to_paint']['p95_ms'] <= 500,
                }
            )
    if args.docx_review:
        from benchmark_docx_review import docx_review_metrics

        report['fixtures']['project_100k_docx_review'] = docx_review_metrics(measure, args.samples)
    if args.scaling:
        from benchmark_scaling import scaling_metrics

        report['scaling'] = scaling_metrics(measure, args.samples)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
