import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtWidgets import QApplication

from byax import engine, model, storage
from byax.errors import ByaxError
from byax.gui import MainWindow
from byax.gui_editor import scalar_offset, text_operations


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


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


def test_typing_preserves_ids_and_unrelated_structure(window):
    doc = model.new_document(text='First paragraph\nSecond paragraph')
    doc['metadata']['unknown_extension'] = {'keep': True}
    first, second = [b['id'] for b in doc['blocks']]
    doc['blocks'][1]['marks'] = [{'start': 0, 'end': 6, 'type': 'bold', 'value': True}]
    window.doc = doc
    window.refresh(0)
    window.editor.insertPlainText('New ')
    assert window.flush_edits()
    assert not window.test_errors
    assert window.doc['blocks'][0]['id'] == first
    assert window.doc['blocks'][0]['text'] == 'New First paragraph'
    assert window.doc['blocks'][1]['id'] == second
    assert window.doc['blocks'][1]['marks'] == doc['blocks'][1]['marks']
    assert window.doc['metadata']['unknown_extension'] == {'keep': True}


def test_paragraph_split_preserves_neighbor_identity(window):
    window.doc = model.new_document(text='Alpha beta\nUnrelated')
    original, neighbor = [b['id'] for b in window.doc['blocks']]
    window.refresh(5)
    window.editor.insertPlainText('\n')
    assert window.flush_edits()
    assert [b['text'] for b in window.doc['blocks']] == ['Alpha', ' beta', 'Unrelated']
    assert window.doc['blocks'][0]['id'] == original
    assert window.doc['blocks'][2]['id'] == neighbor
    window.editor.insertPlainText('New')
    assert window.flush_edits()
    assert window.doc['blocks'][1]['text'] == 'New beta'


def test_table_cell_edit_preserves_table(window):
    doc = model.new_document(text='Introduction')
    doc, _ = engine.apply(doc, {'operations': [{'type': 'insert_table', 'after': doc['blocks'][0]['id'], 'rows': 2, 'columns': 2}]})
    table = doc['blocks'][1]
    cell = table['rows'][0]['cells'][0]['id']
    window.doc = doc
    window.refresh(0)
    assert window.editor.navigate(cell)
    window.editor.insertPlainText('Release date')
    assert window.flush_edits()
    assert not window.test_errors
    assert window.doc['blocks'][1]['id'] == table['id']
    assert window.doc['blocks'][1]['rows'][0]['cells'][0]['text'] == 'Release date'
    assert len(window.doc['blocks']) == 2


def test_unicode_edit_uses_scalar_grapheme_offsets(window):
    window.doc = model.new_document(text='A😀 cafe\u0301')
    window.refresh(3)
    window.editor.insertPlainText('!')
    assert window.flush_edits()
    assert window.doc['blocks'][0]['text'] == 'A😀! cafe\u0301'
    ops = text_operations(window.doc['blocks'][0]['id'], 'cafe\u0301', 'cafe')
    assert ops[0]['start'] == 3
    assert ops[0]['end'] == 5
    assert scalar_offset('A😀Z', 3) == 2


def test_open_session_lock_blocks_other_writer(window, tmp_path):
    path = tmp_path / 'document.byax'
    doc = model.new_document(text='Locked')
    storage.save_document(doc, path)
    assert window.open_path(path)
    with pytest.raises(ByaxError):
        storage.save_document(doc, path)
    window.editor.insertPlainText('Edit ')
    assert window.flush_edits()
    assert window.save()
    assert storage.load_document(path)['blocks'][0]['text'] == 'Edit Locked'


def test_autosave_and_undo(window):
    original = window.doc['blocks'][0]['id']
    window.editor.insertPlainText('Recovered text')
    assert window.flush_edits()
    window.autosave()
    recovered = storage.load_document(window.recovery_path())
    assert recovered['blocks'][0]['text'] == 'Recovered text'
    window.undo()
    assert window.doc['blocks'][0]['text'] == ''
    assert window.doc['blocks'][0]['id'] == original
    window.redo()
    assert window.doc['blocks'][0]['text'] == 'Recovered text'


def test_split_and_join_preserve_marks(window):
    window.doc = model.new_document(text='Alpha beta')
    window.doc['blocks'][0]['marks'] = [{'start': 0, 'end': 10, 'type': 'bold', 'value': True}]
    window.refresh(5)
    window.editor.insertPlainText('\n')
    assert window.flush_edits()
    assert window.doc['blocks'][1]['marks'] == [{'start': 0, 'end': 5, 'type': 'bold', 'value': True}]
    cursor = window.editor.textCursor()
    cursor.deletePreviousChar()
    assert window.flush_edits()
    assert len(window.doc['blocks']) == 1
    assert window.doc['blocks'][0]['text'] == 'Alpha beta'
    assert any(mark['end'] == 10 for mark in window.doc['blocks'][0]['marks'])


def test_markdown_no_edit_save_preserves_original_bytes(window, tmp_path):
    path = tmp_path / 'draft.md'
    original = b'# Title\r\n\r\nA paragraph with **bold** words.\r\n'
    path.write_bytes(original)
    assert window.open_path(path)
    revision = window.doc['revision']
    assert window.flush_edits()
    assert window.doc['revision'] == revision
    assert not window.dirty
    assert window.save()
    assert path.read_bytes() == original
    assert not window.test_errors


def test_markdown_visual_edit_preserves_unrelated_source(window, tmp_path):
    path = tmp_path / 'draft.md'
    path.write_bytes(b'# Title\r\n\r\nA paragraph.\r\n\r\n<!-- keep raw source -->\r\n')
    window.report = lambda *args: None
    assert window.open_path(path)
    target = window.doc['blocks'][1]['id']
    window.editor.navigate(target)
    window.editor.insertPlainText('Edited ')
    assert window.flush_edits()
    assert window.save()
    assert path.read_bytes() == b'# Title\r\n\r\nEdited A paragraph.\r\n\r\n<!-- keep raw source -->\r\n'


def test_markdown_source_edit_preserves_master_identity(window, tmp_path):
    from byax.exchange import import_document
    path = tmp_path / 'draft.md'
    path.write_text('# Title\n\nOriginal paragraph.\n')
    assert window.open_path(path)
    original_ids = [b['id'] for b in window.doc['blocks']]
    doc_id = window.doc['id']
    edited = tmp_path / 'edited.md'
    edited.write_text('# Title\n\nChanged paragraph.\n')
    source_doc, _ = import_document(edited)
    assert window.apply_markdown_source(source_doc)
    assert window.doc['id'] == doc_id
    assert [b['id'] for b in window.doc['blocks']] == original_ids
    assert window.save()
    assert path.read_text() == '# Title\n\nChanged paragraph.\n'


@pytest.mark.parametrize('fixture_name', ['letter', 'resume', 'report', 'newsletter', 'press_kit'])
def test_fixture_refresh_and_flush_are_noop(window, fixture_name):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('fixture_generator', Path(__file__).parents[1] / 'scripts' / 'generate_fixtures.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    window.doc = module.make_fixture(fixture_name)
    revision = window.doc['revision']
    window.refresh()
    assert window.flush_edits()
    assert not window.test_errors
    assert window.doc['revision'] == revision
    assert not window.dirty


def test_regex_replacement_preview_expands_groups(window):
    window.doc = model.new_document(text='Smith, Alex\nJones, Morgan')
    window.refresh()
    matches = engine.find(window.doc, r'(\w+), (\w+)', regex=True)['matches']
    preview = window.replacement_preview(matches, r'(\w+), (\w+)', r'\2 \1', regex=True)
    assert [item['replacement'] for item in preview] == ['Alex Smith', 'Morgan Jones']
    assert 'Smith, Alex' in preview[0]['explanation']


def test_nested_list_indent_does_not_change_semantic_heading(window):
    window.doc = model.new_document(text='List item')
    window.doc['blocks'][0]['attrs']['list'] = {'kind': 'ordered', 'level': 1}
    window.refresh(0)
    window.list_indent(1)
    assert not window.test_errors
    assert window.doc['blocks'][0]['type'] == 'paragraph'
    assert window.doc['blocks'][0]['attrs']['list']['level'] == 2
    window.list_indent(-1)
    assert window.doc['blocks'][0]['attrs']['list']['level'] == 1


def test_merged_table_refresh_is_noop(window):
    doc = model.new_document(text='Table')
    doc, _ = engine.apply(doc, {'operations': [{'type': 'insert_table', 'rows': 2, 'columns': 2}]})
    doc['blocks'][1]['rows'][0]['cells'][0]['attrs']['colspan'] = 2
    doc['blocks'][1]['rows'][0]['cells'][0]['text'] = 'A\nB'
    window.doc = doc
    window.refresh(0)
    revision = doc['revision']
    assert window.flush_edits()
    assert not window.test_errors
    assert window.doc['revision'] == revision


def test_section_scope_uses_semantic_headings(window):
    window.doc = model.new_document(text='One\nFirst section\nTwo\nSecond section')
    window.doc['blocks'][0]['type'] = 'heading'
    window.doc['blocks'][2]['type'] = 'heading'
    ids = [block['id'] for block in window.doc['blocks']]
    assert window.section_targets(ids[1]) == set(ids[:2])
    assert window.section_targets(ids[3]) == set(ids[2:])


def test_comment_reply_gui_uses_canonical_history(window, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    target = window.doc['blocks'][0]['id']
    assert window.commit([{'type': 'add_comment', 'target': target, 'text': 'Check this passage.'}])
    revision = window.doc['revision']
    window.review_list.setCurrentRow(0)
    monkeypatch.setattr(QInputDialog, 'getMultiLineText', lambda *args, **kwargs: ('Checked against the source.', True))
    window.reply_comment()
    assert window.doc['review']['comments'][0]['replies'][0]['text'] == 'Checked against the source.'
    assert window.doc['revision'] != revision
    assert window.review_list.count() == 2


def test_move_table_row_gui_preserves_cell_identity(window, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    window.doc, _ = engine.apply(window.doc, {'operations': [{'type': 'insert_table', 'rows': 2, 'columns': 2}]})
    table = window.doc['blocks'][1]
    cell_id = table['rows'][0]['cells'][0]['id']
    window.refresh()
    window.editor.navigate(cell_id)
    monkeypatch.setattr(QInputDialog, 'getInt', lambda *args, **kwargs: (2, True))
    window.reorder_table('row')
    assert window.doc['blocks'][1]['rows'][1]['cells'][0]['id'] == cell_id
    assert not window.test_errors


def test_move_section_gui_moves_heading_and_body(window, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    window.doc = model.new_document(text='One\nBody one\nTwo\nBody two')
    for index in (0, 2):
        window.doc['blocks'][index]['type'] = 'heading'
        window.doc['blocks'][index]['attrs']['level'] = 1
    window.refresh(0)
    answers = iter([('1. One', True), ('End of document', True)])
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *args, **kwargs: next(answers))
    window.move_section()
    assert [block['text'] for block in window.doc['blocks']] == ['Two', 'Body two', 'One', 'Body one']
    assert not window.test_errors


def test_1500_typing_bursts_reopen_and_checkpoint_survival(window, tmp_path):
    window.doc = model.new_document(text='Start')
    window.doc, _ = engine.checkpoint(window.doc, 'Before typing')
    window.refresh(0)
    for _ in range(1500):
        window.editor.insertPlainText('x')
        assert window.flush_edits()
    assert not window.test_errors
    assert window.doc['blocks'][0]['text'] == 'x' * 1500 + 'Start'
    assert window.doc['requests'] == {}
    assert len(window.doc['history']['revisions']) <= 1000
    assert engine._revision(window.doc, 'Before typing')['blocks'][0]['text'] == 'Start'
    path = tmp_path / 'soak.byax'
    storage.save_document(window.doc, path)
    window.dirty = False
    assert window.open_path(path)
    window.editor.insertPlainText('y')
    assert window.flush_edits()
    assert window.save()
    assert storage.load_document(path)['blocks'][0]['text'] == 'y' + 'x' * 1500 + 'Start'
    window.undo()
    assert window.doc['blocks'][0]['text'] == 'x' * 1500 + 'Start'
    window.redo()
    assert window.doc['blocks'][0]['text'].startswith('y')


def test_failed_flush_keeps_pending_text_and_undo_stacks(window, monkeypatch):
    window.editor.insertPlainText('Pending text')
    def fail(*args, **kwargs):
        raise ByaxError('limit_exceeded', 'History cap reached')
    monkeypatch.setattr(engine, 'apply', fail)
    assert not window.flush_edits()
    assert window.editor.toPlainText() == 'Pending text'
    assert window.doc['blocks'][0]['text'] == ''
    window.undo()
    assert window.editor.toPlainText() == 'Pending text'
    assert window.dirty


def test_policy_changes_require_desktop_confirmation(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    target = window.doc['blocks'][0]['id']
    window.protect()
    window.toggle_tracking(True)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.No)
    window.unprotect()
    window.toggle_tracking(False)
    assert window.doc['settings']['protections'] == [{'target': target, 'kind': 'text'}]
    assert window.doc['settings']['track_changes']
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.Yes)
    window.unprotect()
    window.toggle_tracking(False)
    assert window.doc['settings']['protections'] == []
    assert not window.doc['settings']['track_changes']
    assert not window.test_errors
