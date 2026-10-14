import copy
import os
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtWidgets import QApplication

from byax import engine, model
from byax.gui import MainWindow
from byax.layout import (
    BLOCK_ID_PROPERTY,
    build_document,
    ensure_app,
    resolve_pages,
    update_document,
)


def signature(qdoc):
    rows = []
    block = qdoc.firstBlock()
    while block.isValid():
        fmt = block.blockFormat()
        fragments = []
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.isValid():
                char = fragment.charFormat()
                fragments.append(
                    (
                        fragment.text(),
                        char.fontWeight(),
                        char.fontItalic(),
                        char.fontPointSize(),
                        char.fontUnderline(),
                        char.anchorHref(),
                    )
                )
            iterator += 1
        rows.append(
            (
                fmt.property(BLOCK_ID_PROPERTY),
                block.text(),
                fmt.headingLevel(),
                str(fmt.alignment()),
                fmt.leftMargin(),
                fmt.topMargin(),
                fmt.bottomMargin(),
                fmt.textIndent(),
                str(fmt.pageBreakPolicy()),
                tuple(fragments),
            )
        )
        block = block.next()
    return rows


def positions_and_findings(qdoc):
    return qdoc.byax_positions, sorted((f['code'], f.get('target') or '') for f in qdoc.byax_findings)


@pytest.fixture(scope='module')
def app():
    return ensure_app()


@pytest.fixture
def doc(app):
    document = model.new_document(text='\n'.join(f'Paragraph {i} text.' for i in range(40)))
    document, _ = engine.apply(
        document,
        {
            'operations': [
                {'type': 'insert_table', 'after': document['blocks'][5]['id'], 'rows': 2, 'columns': 2}
            ]
        },
    )
    return document


def ids(doc):
    return [b['id'] for b in doc['blocks'] if b['type'] == 'paragraph']


@pytest.mark.parametrize(
    'make',
    [
        lambda d: [{'type': 'apply_style', 'target': ids(d)[10], 'style': 'heading1'}],
        lambda d: [{'type': 'set_attributes', 'target': ids(d)[11], 'values': {'alignment': 'center'}}],
        lambda d: [{'type': 'insert_paragraph', 'after': ids(d)[20], 'text': 'Inserted'}],
        lambda d: [{'type': 'insert_paragraph', 'after': ids(d)[-1], 'text': 'At the end'}],
        lambda d: [{'type': 'delete_block', 'target': ids(d)[30]}],
        lambda d: [{'type': 'delete_block', 'target': ids(d)[0]}],
        lambda d: [{'type': 'delete_block', 'target': ids(d)[-1]}],
        lambda d: [{'type': 'move_block', 'target': ids(d)[2], 'after': ids(d)[25]}],
        lambda d: [{'type': 'split_block', 'target': ids(d)[12], 'offset': 4}],
        lambda d: [{'type': 'join_blocks', 'target': ids(d)[14], 'next': ids(d)[15]}],
        lambda d: [
            {'type': 'format_text', 'target': ids(d)[16], 'start': 0, 'end': 9, 'marks': {'bold': True}}
        ],
        lambda d: [{'type': 'set_text', 'target': ids(d)[17], 'text': 'Line one\nline two'}],
        lambda d: [{'type': 'set_attributes', 'target': ids(d)[18], 'values': {'page_break_before': True}}],
        lambda d: [
            {'type': 'apply_style', 'target': ids(d)[3], 'style': 'heading2'},
            {'type': 'delete_block', 'target': ids(d)[35]},
        ],
    ],
)
def test_incremental_update_matches_full_rebuild(doc, make):
    qdoc = build_document(doc)
    changed, _ = engine.apply(doc, {'operations': make(doc)})
    assert update_document(qdoc, doc, changed)
    expected = build_document(changed)
    assert signature(qdoc) == signature(expected)
    assert positions_and_findings(qdoc)[0] == positions_and_findings(expected)[0]


def test_incremental_update_refuses_changes_that_need_a_rebuild(doc):
    qdoc = build_document(doc)
    before = signature(qdoc)
    table = next(b for b in doc['blocks'] if b['type'] == 'table')
    cases = [
        [{'type': 'insert_table', 'after': ids(doc)[3], 'rows': 1, 'columns': 1}],
        [{'type': 'update_cell', 'target': table['rows'][0]['cells'][0]['id'], 'text': 'cell'}],
        [
            {
                'type': 'set_attributes',
                'target': ids(doc)[4],
                'values': {'list': {'kind': 'bullet', 'level': 1}},
            }
        ],
        [{'type': 'set_layout', 'values': {'width': 500}}],
        [{'type': 'define_style', 'name': 'body', 'values': {'font_size': 14}}],
    ]
    for operations in cases:
        changed, _ = engine.apply(doc, {'operations': operations})
        assert not update_document(qdoc, doc, changed)
        assert signature(qdoc) == before


def test_incremental_findings_follow_the_rewritten_blocks(doc):
    qdoc = build_document(doc)
    target = ids(doc)[7]
    changed, _ = engine.apply(
        doc,
        {
            'operations': [
                {'type': 'set_attributes', 'target': target, 'values': {'font_family': 'No Such Font'}}
            ]
        },
    )
    assert update_document(qdoc, doc, changed)
    assert ('font_substitution', target) in positions_and_findings(qdoc)[1]
    restored = copy.deepcopy(changed)
    restored['blocks'] = copy.deepcopy(doc['blocks'])
    assert update_document(qdoc, changed, restored)
    assert ('font_substitution', target) not in positions_and_findings(qdoc)[1]


@pytest.fixture
def window(app, monkeypatch, tmp_path):
    w = MainWindow()
    w.recovery_dir = tmp_path / 'recovery'
    w.recovery_dir.mkdir()
    errors = []
    monkeypatch.setattr(w, 'error', lambda error: errors.append(str(error)))
    w.test_errors = errors
    yield w
    w.dirty = False
    w.editor.blockSignals(True)
    w.close()


def test_gui_structural_edit_keeps_editor_document(window):
    window.doc = model.new_document(text='\n'.join(f'Paragraph {i}' for i in range(200)))
    window.refresh(0)
    document = window.editor.document()
    target = window.doc['blocks'][100]['id']
    assert window.editor.navigate(target)
    window.apply_style('heading2')
    assert window.editor.document() is document
    assert signature(document) == signature(build_document(window.doc))
    window.undo()
    assert window.editor.document() is document
    assert window.doc['blocks'][100]['style'] == 'body'
    assert signature(document) == signature(build_document(window.doc))
    window.editor.insertPlainText('typed ')
    assert window.flush_edits()
    assert not window.test_errors


def test_page_preview_resolves_on_worker(window):
    window.doc = model.new_document(text='\n'.join(f'Paragraph {i}' for i in range(300)))
    window.refresh(0)
    window.toggle_pages()
    assert window._active_tasks
    deadline = time.perf_counter() + 30
    while window.pages.document is None and time.perf_counter() < deadline:
        QApplication.processEvents()
    document = window.editor.document()
    assert window.pages.document is document
    assert len(document.byax_resolved['pages']) == len(resolve_pages(window.doc)['pages'])
    part = document.byax_resolved['pages'][0]['fragments'][0]['document']
    assert part.thread() is QApplication.instance().thread()
    window.pages.grab()
    window.toggle_pages()


def test_export_runs_on_worker(window, monkeypatch, tmp_path):
    import threading

    from PySide6.QtWidgets import QFileDialog

    from byax import publication

    path = str(tmp_path / 'out.pdf')
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (path, ''))
    threads, reports = [], []
    real = publication.export_clean

    def export(doc, target, **kwargs):
        threads.append(threading.get_ident())
        return real(doc, target, **kwargs)

    monkeypatch.setattr(publication, 'export_clean', export)
    monkeypatch.setattr(window, 'report', lambda title, result, art=None: reports.append(result))
    window.export()
    deadline = time.perf_counter() + 60
    while not reports and time.perf_counter() < deadline:
        QApplication.processEvents()
    assert threads and threads[0] != threading.get_ident()
    assert reports and os.path.getsize(path) > 0
