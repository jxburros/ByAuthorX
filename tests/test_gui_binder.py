"""The Binder panel (CP A2): outline and corkboard views, keyboard and drag reordering, synopses."""

import copy
import os
import time
from typing import ClassVar

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt
from PySide6.QtGui import QDropEvent, QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox, QWidget

from byax import engine, gui_a11y, project
from byax.gui import MainWindow
from byax.gui_project import MIME, OutlineTree, SynopsisEditor
from byax.model import new_document, text_of


def _h(identity, text, level):
    return {
        'id': identity,
        'type': 'heading',
        'text': text,
        'style': f'heading{level}',
        'marks': [],
        'attrs': {'level': level},
    }


def _p(identity, text):
    return {'id': identity, 'type': 'paragraph', 'text': text, 'style': 'body', 'marks': [], 'attrs': {}}


def manuscript():
    doc = new_document('Novel')
    doc['blocks'] = [
        _h('ch1', 'Chapter one', 1),
        _h('s1', 'Arrival', 2),
        _p('s1a', 'The boat came in on the evening tide.'),
        _p('s1b', 'Mara counted the gulls.'),
        _h('s2', 'The keeper', 2),
        _p('s2a', 'He did not look up.'),
        _h('ch2', 'Chapter two', 1),
        _h('s3', 'Night', 2),
        _p('s3a', 'The lamp failed at midnight.'),
        _h('s4', 'Morning', 2),
        _p('s4a', 'Gulls again.'),
    ]
    doc, _ = engine.apply(doc, {'operations': [{'type': 'project_init'}]})
    return doc


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, monkeypatch, tmp_path):
    w = MainWindow()
    w.recovery_dir = tmp_path / 'recovery'
    w.recovery_dir.mkdir()
    errors, messages = [], []
    monkeypatch.setattr(w, 'error', lambda error: errors.append(str(error)))
    monkeypatch.setattr(w, 'message', lambda title, text: messages.append((title, text)))
    w.test_errors, w.test_messages = errors, messages
    w.motion.set_reduced(True)
    yield w
    w.dirty = False
    w.editor.blockSignals(True)
    w.close()


@pytest.fixture
def binder(window):
    window.doc = manuscript()
    window.refresh()
    window.show()
    return window.show_binder()


def ids(window):
    return {row['title']: row['id'] for row in project.items(window.doc)}


def titles(window):
    return [row['title'] for row in project.items(window.doc)]


def select(panel, title):
    panel.select(ids(panel.host)[title])
    return ids(panel.host)[title]


def drop(view, source, target, where):
    """Deliver a real drop event: the same mime data a drag carries, at a point inside the target row."""
    rect = view.rect_of(target)
    if isinstance(view, OutlineTree):
        y = {
            'before': rect.top() + 2,
            'after': rect.bottom() - 2,
            'into': rect.center().y(),
        }[where]
        point = QPointF(rect.center().x(), y)
    else:
        point = QPointF(rect.left() + 3 if where == 'before' else rect.right() - 3, rect.center().y())
    mime = QMimeData()
    mime.setData(MIME, source.encode())
    event = QDropEvent(point, Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier)
    view.dropEvent(event)


def unlabeled(root):
    problems = []
    for widget in [root, *root.findChildren(QWidget)]:
        if not gui_a11y.actionable(widget):
            continue
        if not widget.accessibleName().strip():
            problems.append(f'{type(widget).__name__} {widget.objectName()!r}: no accessible name')
        if not widget.toolTip().strip():
            problems.append(f'{type(widget).__name__} {widget.accessibleName()!r}: no tooltip')
    return problems


# Structure and content


def test_binder_is_a_sidebar_panel_with_an_empty_state_that_creates_the_binder(window):
    window.doc = new_document('Plain')
    window.doc['blocks'] = [_h('a', 'Chapter', 1), _p('b', 'Text here.')]
    window.refresh()
    window.show()
    panel = window.show_binder()
    assert window.sidebar_selectors['left'].currentData() == 'binder'
    assert panel.stack.currentWidget() is panel.empty and panel.act['create'].isEnabled()
    assert not panel.act['up'].isEnabled() and not panel.act['draft'].isEnabled()
    before = window.doc['blocks']
    panel.act['create'].trigger()
    assert 'project' in window.doc and window.doc['blocks'] == before
    assert panel.stack.currentWidget() is panel.outline and titles(window) == ['Chapter']
    assert not panel.act['create'].isEnabled()


def test_the_panel_moves_to_the_sidebar_that_selects_it(window):
    window.show()
    window.sidebar_selectors['right'].setCurrentIndex(window.sidebar_selectors['right'].findData('binder'))
    assert window.binder_panel.parentWidget() is window.sidebar_panels['right']['binder']
    assert not window.sidebar_panels['left']['binder-note'].isHidden()
    window.show_binder()
    assert window.binder_panel.parentWidget() is window.sidebar_panels['right']['binder']


def test_outline_shows_title_kind_status_words_and_progress(binder, window):
    window.commit(
        [
            {'type': 'project_set_target', 'target': ids(window)['Arrival'], 'words': 20},
            {'type': 'project_set_status', 'target': ids(window)['Arrival'], 'status': 'draft'},
            {'type': 'project_set_target', 'words': 100},
        ]
    )
    window.binder_panel.refresh(force=True)
    tree = binder.outline
    assert tree.topLevelItemCount() == 2
    chapter = tree.topLevelItem(0)
    assert [chapter.child(i).text(0) for i in range(chapter.childCount())] == ['Arrival', 'The keeper']
    spoken = chapter.child(0).data(0, Qt.AccessibleTextRole)
    assert 'Arrival, scene' in spoken and 'status draft' in spoken and 'of 20' in spoken
    row = chapter.child(0).data(0, Qt.UserRole + 3)
    assert (
        row['words']
        == row['total_words']
        == len(
            [
                'Arrival',
                'The',
                'boat',
                'came',
                'in',
                'on',
                'the',
                'evening',
                'tide.',
                'Mara',
                'counted',
                'the',
                'gulls.',
            ]
        )
    )
    words = len(text_of(window.doc).split())
    assert binder.summary.text() == f'{words} of 100 words ({round(100 * words / 100)}%)'
    assert (
        not binder.progress.isHidden()
        and binder.progress.maximum() == 100
        and binder.progress.value() == words
    )


def test_corkboard_cards_show_synopsis_status_words_and_target(binder, window):
    window.commit(
        [
            {
                'type': 'project_set_meta',
                'target': ids(window)['Arrival'],
                'values': {'synopsis': 'Mara arrives.'},
            },
            {'type': 'project_set_target', 'target': ids(window)['Arrival'], 'words': 50},
        ]
    )
    binder.set_view('corkboard')
    board = binder.board
    assert window.settings.value('binder/view') == 'corkboard'
    assert [board.item(i).text() for i in range(board.count())] == [
        'Arrival',
        'The keeper',
        'Night',
        'Morning',
    ]
    row = board.item(0).data(Qt.UserRole + 3)
    assert row['synopsis'] == 'Mara arrives.' and row['target_words'] == 50
    assert 'Synopsis: Mara arrives.' in board.item(0).data(Qt.AccessibleTextRole)
    assert not binder.scope_box.isHidden()
    # Cards inside one chapter, and drilling into a container.
    binder.scope_box.setCurrentIndex(binder.scope_box.findData(ids(window)['Chapter two']))
    binder._scope_chosen(binder.scope_box.currentIndex())
    assert [board.item(i).text() for i in range(board.count())] == ['Night', 'Morning']
    binder.scope = ''
    binder.refresh(force=True)
    binder.set_view('outline')
    binder.select(ids(window)['Chapter one'])
    binder.act['inside'].trigger()
    assert binder.view == 'corkboard' and [board.item(i).text() for i in range(board.count())] == [
        'Arrival',
        'The keeper',
    ]


def test_orphaned_items_are_reported_not_hidden_silently(binder, window):
    assert window.commit([{'type': 'delete_block', 'target': 's2'}])
    assert not binder.orphans.isHidden() and '1 binder item' in binder.orphans.text()


# Keyboard reordering


def test_alt_arrow_keys_move_and_indent_items_in_the_outline(binder, window):
    select(binder, 'Chapter two')
    binder.outline.setFocus()
    before = window.doc['revision']
    QTest.keyClick(binder.outline, Qt.Key_Up, Qt.AltModifier)
    assert titles(window)[:3] == ['Chapter two', 'Night', 'Morning'] and window.doc['revision'] != before
    assert binder.current_id() == ids(window)['Chapter two']
    QTest.keyClick(binder.outline, Qt.Key_Down, Qt.AltModifier)
    assert titles(window)[0] == 'Chapter one'
    QTest.keyClick(binder.outline, Qt.Key_Right, Qt.AltModifier)
    rows = {row['title']: row for row in project.items(window.doc)}
    assert rows['Chapter two']['parent'] == ids(window)['Chapter one'] and rows['Night']['depth'] == 2
    QTest.keyClick(binder.outline, Qt.Key_Left, Qt.AltModifier)
    rows = {row['title']: row for row in project.items(window.doc)}
    assert rows['Chapter two']['parent'] is None and rows['Night']['depth'] == 1
    assert window.test_errors == []


def test_keyboard_moves_are_one_undoable_revision_each(binder, window):
    select(binder, 'Morning')
    revisions = len(window.doc['history']['revisions'])
    binder.act['up'].trigger()
    assert titles(window)[-2:] == ['Morning', 'Night']
    assert len(window.doc['history']['revisions']) == revisions + 1
    window.undo()
    assert titles(window)[-2:] == ['Night', 'Morning']


def test_unavailable_moves_are_disabled_and_do_not_change_the_document(binder, window):
    select(binder, 'Chapter one')
    assert (
        not binder.act['up'].isEnabled()
        and not binder.act['out'].isEnabled()
        and binder.act['down'].isEnabled()
    )
    before = window.doc['revision']
    QTest.keyClick(binder.outline, Qt.Key_Up, Qt.AltModifier)
    assert window.doc['revision'] == before
    assert 'not possible' in window.status_label.text()


def test_move_actions_have_alt_arrow_shortcuts_in_the_binder_menu(window):
    menu = next(a.menu() for a in window.menuBar().actions() if a.text() == '&Binder')
    actions = {a.text(): a for a in menu.actions() if not a.isSeparator() and not a.menu()}
    for name, key in (
        ('Move earlier', 'Alt+Up'),
        ('Move later', 'Alt+Down'),
        ('Outdent', 'Alt+Left'),
        ('Indent', 'Alt+Right'),
    ):
        assert actions[name].shortcut() == QKeySequence(key), name
        assert QKeySequence(key).toString(QKeySequence.SequenceFormat.NativeText) in actions[name].toolTip()
        assert actions[name].statusTip()
    assert {'Show in editor', 'Split at editor cursor…', 'Merge with next item', 'Draft a synopsis…'} <= set(
        actions
    )


# Dragging


def test_dropping_an_item_before_after_or_into_another_reorders_through_the_engine(binder, window):
    window.show()
    QApplication.processEvents()
    lookup = ids(window)
    drop(binder.outline, lookup['Chapter two'], lookup['Chapter one'], 'before')
    assert titles(window)[0] == 'Chapter two'
    lookup = ids(window)
    drop(binder.outline, lookup['The keeper'], lookup['Night'], 'after')
    rows = project.items(window.doc)
    keeper = next(r for r in rows if r['title'] == 'The keeper')
    assert (
        keeper['parent'] == ids(window)['Chapter two']
        and titles(window).index('The keeper') == titles(window).index('Night') + 1
    )
    lookup = ids(window)
    drop(binder.outline, lookup['Morning'], lookup['Chapter one'], 'into')
    assert (
        next(r for r in project.items(window.doc) if r['title'] == 'Morning')['parent']
        == ids(window)['Chapter one']
    )
    assert window.test_errors == []
    # Every drop was one revision with the text intact.
    assert text_of(window.doc).count('Gulls again.') == 1


def test_a_drop_inside_the_dragged_section_is_refused(binder, window):
    lookup = ids(window)
    before = window.doc['revision']
    drop(binder.outline, lookup['Chapter one'], lookup['Arrival'], 'after')
    assert window.doc['revision'] == before
    assert 'cannot be dropped' in window.status_label.text()


def test_cards_reorder_by_dropping_before_or_after_another_card(binder, window):
    binder.set_view('corkboard')
    window.show()
    QApplication.processEvents()
    lookup = ids(window)
    drop(binder.board, lookup['Morning'], lookup['Night'], 'before')
    assert titles(window)[-3:] == ['Chapter two', 'Morning', 'Night']
    lookup = ids(window)
    drop(binder.board, lookup['Arrival'], lookup['Night'], 'after')
    rows = project.items(window.doc)
    arrival = next(r for r in rows if r['title'] == 'Arrival')
    assert arrival['parent'] == ids(window)['Chapter two'] and arrival['level'] == 2
    assert window.test_errors == []


def test_a_foreign_drop_is_ignored(binder, window):
    mime = QMimeData()
    mime.setText('plain text')
    before = window.doc['revision']
    binder.outline.dropEvent(QDropEvent(QPointF(20, 20), Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier))
    assert window.doc['revision'] == before


# Navigation and editing metadata


def test_clicking_or_pressing_enter_shows_the_heading_in_the_editor(binder, window, monkeypatch):
    shown = []
    monkeypatch.setattr(window, 'navigate_target', lambda target: shown.append(target) or True)
    window.show()
    QApplication.processEvents()
    rect = binder.outline.rect_of(ids(window)['The keeper'])
    QTest.mouseClick(binder.outline.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    assert shown == ['s2']
    select(binder, 'Night')
    QTest.keyClick(binder.outline, Qt.Key_Return)
    assert shown[-1] == 's3'
    binder.set_view('corkboard')
    QApplication.processEvents()
    rect = binder.board.rect_of(ids(window)['Morning'])
    QTest.mouseClick(binder.board.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    assert shown[-1] == 's4'


def test_selecting_does_not_navigate_but_show_in_editor_does(binder, window, monkeypatch):
    shown = []
    monkeypatch.setattr(window, 'navigate_target', lambda target: shown.append(target) or True)
    select(binder, 'Night')
    assert shown == []
    binder.act['go'].trigger()
    assert shown == ['s3']


def test_synopsis_is_edited_in_the_detail_field_and_empty_text_clears_it(binder, window):
    item = select(binder, 'Arrival')
    assert binder.synopsis.isEnabled() and binder.synopsis.toPlainText() == ''
    binder.synopsis.setPlainText('Mara arrives and counts the gulls.')
    binder.synopsis.committed.emit(binder.synopsis.toPlainText())
    assert project.find_item(window.doc, item)['synopsis'] == 'Mara arrives and counts the gulls.'
    assert binder.synopsis.toPlainText() == 'Mara arrives and counts the gulls.'
    binder.synopsis.setPlainText('   ')
    binder.synopsis.committed.emit(binder.synopsis.toPlainText())
    assert 'synopsis' not in project.find_item(window.doc, item)
    # An unchanged synopsis makes no revision.
    revision = window.doc['revision']
    binder.synopsis.committed.emit('')
    assert window.doc['revision'] == revision


def test_synopsis_is_edited_in_place_on_a_card(binder, window):
    binder.set_view('corkboard')
    window.show()
    QApplication.processEvents()
    item = select(binder, 'The keeper')
    binder.board.setFocus()
    binder.act['edit'].trigger()
    editor = binder.board.viewport().findChild(SynopsisEditor)
    assert editor is not None and editor.accessibleName() == 'Synopsis of The keeper'
    editor.setPlainText('He ignores Mara.')
    QTest.keyClick(editor, Qt.Key_Return, Qt.ControlModifier)
    assert project.find_item(window.doc, item)['synopsis'] == 'He ignores Mara.'
    assert 'He ignores Mara.' in binder.board.item(1).data(Qt.AccessibleTextRole)


def test_escape_cancels_a_card_edit(binder, window):
    binder.set_view('corkboard')
    window.show()
    QApplication.processEvents()
    item = select(binder, 'Night')
    binder.board.edit(binder.board.currentIndex())
    editor = binder.board.viewport().findChild(SynopsisEditor)
    editor.setPlainText('Discard me')
    QTest.keyClick(editor, Qt.Key_Escape)
    assert 'synopsis' not in project.find_item(window.doc, item)


def test_status_and_word_target_actions(binder, window, monkeypatch):
    item = select(binder, 'Night')
    next(action for status, action in binder.status_actions if status == 'final').trigger()
    assert project.find_item(window.doc, item)['status'] == 'final'
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('first pass', True))
    binder.act['status_other'].trigger()
    assert project.find_item(window.doc, item)['status'] == 'first pass'
    monkeypatch.setattr(QInputDialog, 'getInt', lambda *a, **k: (1500, True))
    binder.act['target'].trigger()
    assert project.find_item(window.doc, item)['target_words'] == 1500
    monkeypatch.setattr(QInputDialog, 'getInt', lambda *a, **k: (0, True))
    binder.act['target'].trigger()
    assert 'target_words' not in project.find_item(window.doc, item)
    assert window.test_errors == []


def test_add_a_scene_or_chapter_after_the_selection(binder, window, monkeypatch):
    select(binder, 'Arrival')
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('Storm', True))
    binder.act['scene'].trigger()
    assert titles(window)[:4] == ['Chapter one', 'Arrival', 'Storm', 'The keeper']
    assert binder.current_id() == ids(window)['Storm']
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('Epilogue', True))
    select(binder, 'Morning')
    binder.act['chapter'].trigger()
    assert titles(window)[-1] == 'Epilogue'
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('', True))
    revision = window.doc['revision']
    binder.act['scene'].trigger()
    assert window.doc['revision'] == revision


# Split and merge


def test_split_at_the_editor_cursor_creates_a_new_scene_and_keeps_comments(binder, window, monkeypatch):
    window.commit([{'type': 'add_comment', 'target': 's1b', 'start': 0, 'end': 4, 'text': 'Who?'}])
    select(binder, 'Arrival')
    assert not binder.act['split'].isEnabled()
    window.editor.navigate('s1b')
    cursor = window.editor.textCursor()
    cursor.movePosition(cursor.MoveOperation.StartOfBlock)
    window.editor.setTextCursor(cursor)
    binder.update_actions()
    assert binder.act['split'].isEnabled()
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('On the quay', True))
    binder.act['split'].trigger()
    assert titles(window)[:4] == ['Chapter one', 'Arrival', 'On the quay', 'The keeper']
    created = binder.current_id()
    assert project.own_blocks(window.doc, created)[1] == 's1b'
    comment = window.doc['review']['comments'][0]
    assert comment['target'] == 's1b' and comment.get('anchor_status') in (None, 'anchored')
    assert window.test_errors == []


def test_split_explains_when_the_cursor_is_not_in_the_item(binder, window, monkeypatch):
    select(binder, 'Night')
    window.editor.navigate('s1a')
    shown = []
    monkeypatch.setattr(QMessageBox, 'information', lambda *a, **k: shown.append(a[2]))
    binder.update_actions()
    assert not binder.act['split'].isEnabled()
    binder.split_at_cursor()
    assert shown and 'cursor' in shown[0]


def test_merge_with_the_next_item_asks_first(binder, window, monkeypatch):
    select(binder, 'Night')
    asked = []
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: asked.append(a[2]) or QMessageBox.No)
    binder.act['merge'].trigger()
    assert titles(window).count('Morning') == 1 and 'Merge “Morning” into “Night”' in asked[0]
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.Yes)
    binder.act['merge'].trigger()
    assert 'Morning' not in titles(window)
    assert 'Gulls again.' in text_of(window.doc) and 'The lamp failed at midnight.' in text_of(window.doc)
    select(binder, 'Night')
    assert not binder.act['merge'].isEnabled()


# Draft a synopsis (opt-in)


class FakeConfig:
    allow_remote = False


class FakeRegistry:
    calls: ClassVar[list] = []

    def __init__(self, *a, **k):
        pass

    def get(self, alias):
        return FakeConfig()

    def preview(self, alias, kind='review', **kwargs):
        FakeRegistry.calls.append(('preview', alias))
        return {'remote': False, 'locality_label': 'This computer'}

    def chat(self, alias, messages, **kwargs):
        FakeRegistry.calls.append(('chat', alias))
        assert kwargs['mode'] == 'chat' and kwargs['context']['scene']['text']
        return {
            'reply': ' Mara watches the boat.\n',
            'provider': alias,
            'model': 'm',
            'document_revision': 'r',
        }


def run_synchronously(window, monkeypatch):
    monkeypatch.setattr(window, 'background', lambda label, fn, done, failed=None: done(fn()))


def test_drafting_a_synopsis_needs_a_configured_provider(binder, window, monkeypatch):
    monkeypatch.setattr('byax.gui_ai.text_providers', list)
    shown = []
    monkeypatch.setattr(QMessageBox, 'information', lambda *a, **k: shown.append(a[2]))
    select(binder, 'Arrival')
    assert not binder.act['draft'].isEnabled()
    assert 'configured text provider' in binder.act['draft'].toolTip()
    assert binder.draft_synopsis() is False and shown
    assert window.doc['review']['proposals'] == []


def test_a_drafted_synopsis_is_a_pending_proposal_never_applied_silently(binder, window, monkeypatch):
    monkeypatch.setattr('byax.gui_ai.text_providers', lambda: [{'alias': 'local', 'kind': 'ollama'}])
    monkeypatch.setattr('byax.providers.ProviderRegistry', FakeRegistry)
    run_synchronously(window, monkeypatch)
    FakeRegistry.calls = []
    item = select(binder, 'Arrival')
    assert binder.act['draft'].isEnabled()
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.No)
    assert binder.draft_synopsis() is False
    assert ('chat', 'local') not in FakeRegistry.calls  # declining sends nothing
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.Yes)
    assert binder.draft_synopsis() is True
    assert 'synopsis' not in project.find_item(window.doc, item)
    proposal = window.doc['review']['proposals'][-1]
    assert proposal['author'] == 'assistant' and proposal['operations'][0]['values'] == {
        'synopsis': 'Mara watches the boat.'
    }
    assert proposal['provenance']['scope'] == 'binder synopsis'
    window.commit([{'type': 'accept_proposal', 'target': proposal['id']}])
    assert project.find_item(window.doc, item)['synopsis'] == 'Mara watches the boat.'


def test_a_synopsis_is_not_added_when_the_document_changed_meanwhile(binder, window, monkeypatch):
    monkeypatch.setattr('byax.gui_ai.text_providers', lambda: [{'alias': 'local', 'kind': 'ollama'}])
    monkeypatch.setattr('byax.providers.ProviderRegistry', FakeRegistry)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.Yes)
    pending = []
    monkeypatch.setattr(window, 'background', lambda label, fn, done, failed=None: pending.append((fn, done)))
    select(binder, 'Arrival')
    assert binder.draft_synopsis() is True
    window.commit([{'type': 'insert_text', 'target': 's1a', 'offset': 0, 'text': 'Early. '}])
    fn, done = pending[0]
    done(fn())
    assert window.doc['review']['proposals'] == []
    assert 'changed while the provider worked' in window.status_label.text()


# Accessibility, motion and menus


def test_binder_controls_have_names_tooltips_and_shortcuts(binder, window):
    window.show()
    QApplication.processEvents()
    for view in ('outline', 'corkboard'):
        binder.set_view(view)
        QApplication.processEvents()
        assert unlabeled(binder) == [], view
    assert binder.outline_button.accessibleName() == 'Outline view'
    assert binder.board_button.accessibleName() == 'Corkboard view'
    assert binder.menu_button.accessibleName() == 'Binder actions'
    for action in binder.act.values():
        assert action.toolTip().strip() and action.statusTip().strip(), action.text()
    assert unlabeled(window) == []
    # Every keyboard route reaches the same actions the context menu lists.
    menu = binder.actions_menu()
    assert {a.text() for a in menu.actions() if a.text()} >= {
        'Move earlier',
        'Move later',
        'Outdent',
        'Indent',
    }


def test_the_view_switch_does_not_leave_effects_under_reduced_motion(binder, window):
    binder.set_view('corkboard')
    binder.set_view('outline')
    assert binder.stack.currentWidget().graphicsEffect() is None
    assert window.motion.reduced


def test_the_binder_follows_the_document_through_undo_and_history(binder, window):
    select(binder, 'Chapter two')
    binder.act['up'].trigger()
    assert binder.outline.topLevelItem(0).text(0) == 'Chapter two'
    window.undo()
    assert binder.outline.topLevelItem(0).text(0) == 'Chapter one'
    assert binder.current_id() == ids(window)['Chapter two']


def test_a_hidden_panel_refreshes_when_it_is_shown_again(window):
    window.doc = manuscript()
    window.refresh()
    window.show()
    panel = window.binder_panel
    assert panel._stale or not panel.isVisible()
    window.commit([{'type': 'project_set_status', 'target': ids(window)['Arrival'], 'status': 'final'}])
    window.show_binder()
    item = panel.outline.topLevelItem(0).child(0)
    assert 'status final' in item.data(0, Qt.AccessibleTextRole)


def test_documents_without_a_binder_do_not_change_when_the_panel_is_built(window):
    window.doc = new_document(text='Just text')
    before = copy.deepcopy(window.doc)
    window.refresh()
    window.show_binder()
    assert window.doc == before and 'project' not in window.doc


def test_a_100k_word_project_opens_in_the_binder_and_reorders_within_the_structural_target(window):
    from importlib import util
    from pathlib import Path

    spec = util.spec_from_file_location(
        'generate_fixtures', Path(__file__).resolve().parents[1] / 'scripts' / 'generate_fixtures.py'
    )
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    window.doc = module.make_fixture('project_100k')
    started = time.perf_counter()
    window.refresh(0)
    window.show()
    panel = window.show_binder()
    opened = time.perf_counter() - started
    chapters = [r for r in project.items(window.doc) if r['kind'] == 'chapter']
    rows = project.items(window.doc)
    assert len(rows) >= 150 and panel.outline.topLevelItemCount() == sum(r['parent'] is None for r in rows)
    assert len(chapters) >= 10
    panel.select(chapters[0]['id'])
    began = time.perf_counter()
    for _ in range(3):
        assert panel.step('down')
    elapsed = (time.perf_counter() - began) / 3
    panel.set_view('corkboard')
    assert panel.board.count() > 100
    # docs/benchmarks: a structural edit paints within 500 ms (measured by scripts/benchmark.py --project);
    # the bound here is looser so a slow shared runner does not flake.
    assert elapsed < 2.0 and opened < 15.0
    assert window.test_errors == []


def test_binder_only_edits_do_not_rebuild_the_editor_document(binder, window):
    """Status, target and synopsis changes leave the text untouched, so the editor keeps its document."""
    document = window.editor.document()
    item = ids(window)['Arrival']
    window.editor.navigate('s1b')
    position = window.editor.textCursor().position()
    binder.set_synopsis(item, 'Mara arrives.')
    window.commit(
        [
            {'type': 'project_set_status', 'target': item, 'status': 'final'},
            {'type': 'project_set_target', 'target': item, 'words': 100},
        ]
    )
    assert window.editor.document() is document
    assert window.editor.textCursor().position() == position
    assert project.find_item(window.doc, item)['synopsis'] == 'Mara arrives.'
