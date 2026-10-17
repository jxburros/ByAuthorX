"""Manuscript project workload (CP A1 exit criterion): open, reorder a chapter, save.

Run through ``scripts/benchmark.py --project`` (add ``--no-gui`` for engine timings
only). The fixture is the deterministic 100,000-word ``project_100k`` from
``generate_fixtures.py``. Timings are engine/storage only; GUI paint is excluded,
so they are compared with the typing and structural-edit targets as proxies.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from generate_fixtures import make_fixture

from byax import project
from byax.engine import apply
from byax.model import canonical_json, text_of
from byax.statistics import counts
from byax.storage import load_document, save_document

DEFINITIONS = {
    'project_open': 'load_document of the saved 100,000-word project package: unzip, hashes, validation.',
    'project_typing_transaction': 'One canonical insert_text in a scene paragraph, including validation and history.',
    'project_reorder_chapter': 'One canonical project_move of a chapter with its scenes, including anchor reporting.',
    'project_save': 'save_document of the reordered project to an existing path (atomic replacement).',
    'project_binder_query': 'project.items: binder rows with nesting and authored word counts.',
}


def reorder(doc):
    rows = [row for row in project.items(doc) if row['kind'] == 'chapter']
    return apply(
        doc,
        {
            'expected_revision': doc['revision'],
            'operations': [{'type': 'project_move', 'target': rows[0]['id'], 'after': rows[4]['id']}],
        },
    )


BINDER_DEFINITIONS = {
    'binder_build': 'Rebuild the Binder panel (outline rows, corkboard cards, header) from the 100,000-word project and repaint it.',
    'binder_reorder_keyboard_to_paint': 'Alt+Down/Alt+Up on a chapter: plan, one canonical project_move envelope through the window commit (editor document update included), binder rebuild and synchronous repaint of editor and binder.',
    'binder_reorder_drop_to_paint': 'The same move requested by a drop on another chapter (heading levels normalized), through the window commit, to repaint.',
    'binder_corkboard_switch': 'Switch the panel between outline and corkboard and repaint.',
    'binder_synopsis_to_paint': 'Save a synopsis through project_set_meta, rebuild the binder and repaint.',
}


def binder_gui_metrics(doc, samples, measure):
    """The Binder panel in the real window (CP A2 exit criterion): open, reorder by keyboard and by drop.

    Offscreen numbers exclude the compositor, like the other GUI metrics in ``benchmark.py``.
    """
    from PySide6.QtWidgets import QApplication

    from byax.gui import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    errors = []
    window.error = lambda error: errors.append(str(error))
    window.doc = doc
    window.dirty = False
    window.refresh(0)
    window.show()
    app.processEvents()

    def paint(panel):
        panel.stack.currentWidget().repaint()
        window.editor.viewport().repaint()

    panel = window.show_binder('outline')
    app.processEvents()
    chapters = [row['id'] for row in project.items(window.doc) if row['kind'] == 'chapter']
    scenes = [row['id'] for row in project.items(window.doc) if row['kind'] == 'scene']

    def build(_):
        panel.refresh(force=True)
        paint(panel)

    def keyboard(index):
        panel.select(chapters[2], notify=False)
        assert panel.step('down' if index % 2 == 0 else 'up')
        paint(panel)

    def drop(index):
        first, second = (chapters[0], chapters[3]) if index % 2 == 0 else (chapters[3], chapters[0])
        assert panel.drop(first, second, 'after')
        paint(panel)

    def switch(index):
        panel.set_view('corkboard' if index % 2 == 0 else 'outline')
        paint(panel)

    def synopsis(index):
        panel.set_synopsis(scenes[1], f'A synopsis, revision {index}.')
        paint(panel)

    metrics = {
        'binder_build': measure(build, samples),
        'binder_reorder_keyboard_to_paint': measure(keyboard, samples),
        'binder_reorder_drop_to_paint': measure(drop, samples),
        'binder_corkboard_switch': measure(switch, samples),
        'binder_synopsis_to_paint': measure(synopsis, samples),
    }
    window.dirty = False
    window.close()
    window.deleteLater()
    app.processEvents()
    return {
        'definitions': BINDER_DEFINITIONS,
        'binder_items': len(chapters) + len(scenes),
        'metrics': metrics,
        'target_comparison': {
            'binder_reorder_500ms': max(
                metrics['binder_reorder_keyboard_to_paint']['p95_ms'],
                metrics['binder_reorder_drop_to_paint']['p95_ms'],
            )
            <= 500,
            'binder_build_500ms': metrics['binder_build']['p95_ms'] <= 500,
        },
        'errors': errors,
    }


def project_metrics(measure, samples):
    doc = make_fixture('project_100k')
    entry = {
        'semantic_sha256': hashlib.sha256(canonical_json(doc).encode()).hexdigest(),
        'blocks': len(doc['blocks']),
        'words': counts(text_of(doc))['words'],
        'binder_items': len(project.items(doc)),
        'definitions': DEFINITIONS,
        'metrics': {},
    }
    with tempfile.TemporaryDirectory(prefix='byax-project-bench-') as root:
        path = Path(root) / 'project.byax'
        save_document(doc, path)
        entry['package_bytes'] = path.stat().st_size
        entry['metrics']['project_open'] = measure(lambda _: load_document(path), samples)
        opened = load_document(path)
        typing = {
            'expected_revision': opened['revision'],
            'operations': [{'type': 'insert_text', 'target': 'scene_15_2_p1', 'offset': 0, 'text': 'Now. '}],
        }
        entry['metrics']['project_typing_transaction'] = measure(lambda _: apply(opened, typing), samples)
        entry['metrics']['project_reorder_chapter'] = measure(lambda _: reorder(opened), samples)
        moved, result = reorder(opened)
        entry['reorder_anchor_mappings'] = len(result.get('anchor_mappings', []))
        entry['metrics']['project_save'] = measure(lambda _: save_document(moved, path), samples)
        entry['metrics']['project_binder_query'] = measure(lambda _: project.items(moved), samples)
    metrics = entry['metrics']
    entry['target_comparison'] = {
        'typing_50ms': 'not_measured: the typing transaction is the idle commit, not keystroke feedback',
        'reorder_500ms': metrics['project_reorder_chapter']['p95_ms'] <= 500,
        'reorder_note': 'Engine transaction only; GUI paint after a binder move (keyboard and drop) is in the binder metrics, measured through the Binder panel.',
    }
    return entry
