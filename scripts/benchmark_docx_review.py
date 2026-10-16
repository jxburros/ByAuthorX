"""Word review round trip workload (CP C1): build a review copy, plan and apply a returned copy's merge.

Run through ``scripts/benchmark.py --docx-review`` (add ``--no-gui``). The fixture is the deterministic
100,000-word ``project_100k`` from ``generate_fixtures.py``. The returned copy carries 200 tracked edits
(replacements, insertions and deletions spread through the manuscript), 50 comments and one moved
paragraph, written the way Word saves them by ``tests/docx_word.py``.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

from generate_fixtures import make_fixture

from byax import docx_review
from byax.model import canonical_json, text_of
from byax.statistics import counts

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))

DEFINITIONS = {
    'review_copy_build': 'prepare_review_package: projection, DOCX writing with identity bookmarks and comments, ZIP normalization; no I/O.',
    'review_merge_plan': 'plan_review_merge of the returned copy: bounded archive read, revisions, alignment, three-way placement.',
    'review_merge_apply': 'merge_reviewed_copy: the plan plus one canonical transaction registering every proposal and comment.',
}


def returned_copy(data, doc):
    from docx_word import WordEditor

    editor = WordEditor(data)
    paragraphs = editor.paragraphs()
    texts = [
        ''.join(
            t.text or ''
            for t in p.iter('{%s}t' % 'http://schemas.openxmlformats.org/wordprocessingml/2006/main')
        )
        for p in paragraphs
    ]
    candidates = [index for index, text in enumerate(texts) if len(text) > 40]
    step = max(1, len(candidates) // 200)
    for number, index in enumerate(candidates[::step][:200]):
        text = texts[index]
        if number % 3 == 0:
            editor.replace(index, 4, 9, 'edited')
        elif number % 3 == 1:
            editor.insert(index, len(text) // 2, 'inserted ')
        else:
            editor.delete(index, len(text) - 10, len(text) - 1)
        if number % 4 == 0:
            editor.comment(index, 0, 3, f'Comment {number}')
    editor.move_paragraph(candidates[-1], candidates[0])
    return editor.save()


def docx_review_metrics(measure, samples):
    doc = make_fixture('project_100k')
    entry = {
        'semantic_sha256': hashlib.sha256(canonical_json(doc).encode()).hexdigest(),
        'blocks': len(doc['blocks']),
        'words': counts(text_of(doc))['words'],
        'definitions': DEFINITIONS,
        'metrics': {},
    }
    entry['metrics']['review_copy_build'] = measure(
        lambda _: docx_review.prepare_review_package(doc, created='2026-10-01T08:00:00Z'), samples
    )
    with tempfile.TemporaryDirectory(prefix='byax-review-bench-') as root:
        out = Path(root) / 'copy.docx'
        sent, result, data = docx_review.create_review_package(doc, out, created='2026-10-01T08:00:00Z')
        returned = Path(root) / 'returned.docx'
        returned.write_bytes(returned_copy(data, sent))
        entry['review_copy_bytes'] = len(data)
        entry['returned_copy_bytes'] = returned.stat().st_size
        entry['metrics']['review_merge_plan'] = measure(
            lambda _: docx_review.plan_review_merge(sent, returned), samples
        )
        entry['metrics']['review_merge_apply'] = measure(
            lambda _: docx_review.merge_reviewed_copy(sent, returned), samples
        )
        _, report = docx_review.plan_review_merge(sent, returned)
        entry['proposals'] = report['proposals']
        entry['comments'] = report['comments']['added']
        entry['conflicts'] = len(report['conflicts'])
    entry['target_comparison'] = {
        'merge_plan_5s': entry['metrics']['review_merge_plan']['p95_ms'] <= 5000,
        'note': 'Interactive targets do not cover a one-off merge; 5 s is the working budget for a 100,000-word manuscript.',
    }
    return entry
