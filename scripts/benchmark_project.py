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
        'reorder_note': 'Engine transaction only; GUI paint after a binder move is measured with the A2 views.',
    }
    return entry
