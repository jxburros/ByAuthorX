"""Writing goals and named drafts in the desktop window (CP A3, A4)."""

import json
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from byax import drafts, engine, goals, gui_a11y, project
from byax.gui import MainWindow
from byax.gui_drafts import CompareDraftsDialog, DraftsDialog, SaveDraftDialog
from byax.gui_goals import GoalsDialog
from byax.model import new_document
from byax.storage import load_document, save_document


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
        _h('s2', 'The keeper', 2),
        _p('s2a', 'He did not look up.'),
        _h('ch2', 'Chapter two', 1),
        _h('s3', 'Night', 2),
        _p('s3a', 'The lamp failed at midnight.'),
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
    w.doc = manuscript()
    w.refresh()
    w.goals_begin()
    yield w
    w.dirty = False
    w.editor.blockSignals(True)
    w.close()


def unlabeled(root):
    """Every actionable control needs an accessible name and a tooltip once the dialog is on screen."""
    root.show()
    QApplication.processEvents()
    problems = []
    for widget in [root, *root.findChildren(QWidget)]:
        if not gui_a11y.actionable(widget):
            continue
        if not widget.accessibleName().strip():
            problems.append(f'{type(widget).__name__} {widget.objectName()!r}: no accessible name')
        if not widget.toolTip().strip():
            problems.append(f'{type(widget).__name__} {widget.accessibleName()!r}: no tooltip')
    root.close()
    return problems


def write(window, text):
    assert window.commit([{'type': 'insert_text', 'target': 's1a', 'offset': 0, 'text': text}])


# Goals


def test_the_status_bar_button_follows_the_session_and_the_daily_goal(window):
    assert window.goals_button.text() == 'Session +0'
    write(window, 'one two three four five ')
    assert window.goal_session.written == 5 and window.goals_button.text() == 'Session +5'
    window.set_local_goals(None, 20)
    assert window.goals_button.text() == 'Session 5/20'
    window.set_local_goals(10, 20)
    assert window.goals_button.text() == 'Today 5/10'
    write(window, 'six seven eight nine ten eleven ')
    assert window.goals_button.text() == 'Today 11/10'
    assert 'Writing goals' == window.goals_button.accessibleName()
    description = window.goals_button.accessibleDescription()
    assert 'Written today: 11 words' in description and 'Daily goal: 10 words' in description


def test_deleting_text_never_counts_against_the_session(window):
    write(window, 'one two three ')
    assert window.goal_session.written == 3
    window.commit([{'type': 'set_text', 'target': 's1a', 'text': 'x'}])
    assert window.goal_session.words < window.goal_session.start_words and window.goal_session.written == 0
    write(window, 'a b c d e f ')  # the paragraph is back to 7 words, one fewer than at the start
    assert window.goal_session.written == 0
    write(window, 'g h i j k ')
    assert window.goal_session.written == 4


def test_sessions_are_stored_outside_the_document_and_its_history(window, tmp_path):
    path = tmp_path / 'novel.byax'
    save_document(window.doc, path)
    saved_bytes = path.read_bytes()
    window.path = path
    window.dirty = False
    write(window, 'alpha beta gamma delta ')
    history_before = json.dumps(window.doc['history'], sort_keys=True)
    revision = window.doc['revision']
    window.goals_save()
    assert (
        window.doc['revision'] == revision
        and json.dumps(window.doc['history'], sort_keys=True) == history_before
    )
    record = window.goal_store.document(window.doc['id'])
    assert record['sessions'][0]['written'] == 4
    assert 'sessions' not in json.dumps(window.doc) and 'goals' not in json.dumps(window.doc['history'])
    assert window.goal_store.path.is_file() and path not in window.goal_store.path.parents
    # Recording a session is not a save: the file on disk is exactly what it was before the edit.
    assert load_document(path)['history'] == json.loads(json.dumps(load_document(path)['history']))
    assert path.read_bytes() == saved_bytes


def test_opening_another_document_stores_the_finished_session(window, tmp_path):
    first = window.doc['id']
    write(window, 'one two three ')
    other = tmp_path / 'other.byax'
    save_document(new_document('Other', text='Short'), other)
    window.dirty = False
    assert window.open_path(other)
    assert window.goal_store.document(first)['days'][
        goals.status(window.doc, window.goal_store)['daily']['date']
    ] == {
        'written': 3,
        'sessions': 1,
    }
    assert window.goal_session.doc_id == window.doc['id'] != first and window.goal_session.written == 0


def test_closing_the_window_stores_the_session(window, tmp_path):
    write(window, 'one two ')
    doc_id = window.doc['id']
    window.dirty = False
    window.editor.blockSignals(True)
    window.close()
    assert window.goal_store.document(doc_id)['sessions'][0]['written'] == 2


def test_the_goals_dialog_sets_local_goals_and_the_document_target(window):
    write(window, 'one two three ')
    window.set_local_goals(5, 100)
    window.commit([{'type': 'project_set_target', 'target': project.items(window.doc)[1]['id'], 'words': 30}])
    dialog = GoalsDialog(window)
    assert dialog.daily.value() == 5 and dialog.session.value() == 100 and dialog.target.value() == 0
    assert (
        'Written today: 3 words' in dialog.today.text()
        and 'This session: 3 of 100' in dialog.session_label.text()
    )
    assert 'Arrival' in dialog.items.item(0).text() and '30' in dialog.items.item(0).text()
    dialog.daily.setValue(8)
    dialog.target.setValue(500)
    assert dialog.save() and dialog.result() == QDialog.Accepted
    assert window.doc['project']['target_words'] == 500
    assert window.goal_store.document(window.doc['id'])['daily_words'] == 8
    assert window.goals_button.text() == 'Today 3/8'
    again = GoalsDialog(window)
    assert again.target.value() == 500 and again.document_bar.maximum() == 500
    again.target.setValue(0)
    again.session.setValue(0)
    assert again.save() and 'target_words' not in window.doc['project']


def test_the_dialog_creates_the_binder_for_a_document_target_when_there_is_none(window):
    window.doc = new_document(text='Just words here')
    window.refresh()
    window.goals_begin()
    dialog = GoalsDialog(window)
    dialog.target.setValue(1000)
    assert dialog.save()
    assert window.doc['project']['target_words'] == 1000 and 'project' in window.doc


def test_goal_controls_are_named_and_the_menu_item_exists(window):
    dialog = GoalsDialog(window)
    assert unlabeled(dialog) == []
    for control in (dialog.target, dialog.daily, dialog.session):
        assert control.accessibleName() and control.toolTip()
    texts = {a.text() for a in window.findChildren(QAction)}
    assert 'Writing goals…' in texts


def test_an_unreadable_goals_file_does_not_stop_writing(window):
    window.goal_store.path.parent.mkdir(parents=True, exist_ok=True)
    window.goal_store.path.write_text('{broken', encoding='utf-8')
    write(window, 'still works ')
    window.goals_save()
    assert window.test_errors == [] and 'unavailable' in window.status_label.text()
    assert next(b for b in window.doc['blocks'] if b['id'] == 's1a')['text'].startswith('still works')
    assert window.goals_state()['daily']['goal'] is None


# Drafts


def make_draft(window, name='First draft', note='Before the rewrite'):
    window.doc, _ = drafts.save_draft(window.doc, name, note)
    window.update_sidebar()


def test_save_draft_as_records_a_note_and_keeps_the_current_draft(window, monkeypatch):
    def accept(dialog):
        dialog.name.setText('  First draft ')
        dialog.note.setPlainText('Before the rewrite')
        return QDialog.Accepted

    monkeypatch.setattr(SaveDraftDialog, 'exec', accept)
    assert window.save_draft_as()
    assert window.doc['history']['current_branch'] == 'main' and window.dirty
    entry = {d['name']: d for d in drafts.list_drafts(window.doc)}['First draft']
    assert entry['note'] == 'Before the rewrite'
    monkeypatch.setattr(SaveDraftDialog, 'exec', lambda dialog: QDialog.Rejected)
    assert window.save_draft_as() is False
    monkeypatch.setattr(SaveDraftDialog, 'exec', accept)
    assert window.save_draft_as() is False and window.test_errors  # the name is taken


def test_save_draft_as_can_switch_to_the_new_draft(window, monkeypatch):
    def accept(dialog):
        dialog.name.setText('Second')
        dialog.switch.setChecked(True)
        return QDialog.Accepted

    monkeypatch.setattr(SaveDraftDialog, 'exec', accept)
    assert window.save_draft_as()
    assert window.doc['history']['current_branch'] == 'Second'


def test_drafts_dialog_lists_notes_and_switches(window, monkeypatch):
    make_draft(window)
    write(window, 'Later. ')
    dialog = DraftsDialog(window)
    names = [dialog.table.topLevelItem(i).text(0) for i in range(dialog.table.topLevelItemCount())]
    assert names == ['main (current)', 'First draft']
    assert dialog.table.topLevelItem(1).text(3) == 'Before the rewrite'
    assert not dialog.buttons['switch'].isEnabled()  # the current draft is selected
    dialog.table.setCurrentItem(dialog.table.topLevelItem(1))
    assert all(dialog.buttons[k].isEnabled() for k in ('switch', 'compare', 'bring'))
    dialog.switch()
    assert window.doc['history']['current_branch'] == 'First draft'
    assert next(b for b in window.doc['blocks'] if b['id'] == 's1a')['text'].startswith('The boat')
    assert 'Now writing in draft' in window.status_label.text()
    assert unlabeled(dialog) == []


def test_compare_dialog_lists_every_item_with_its_result_and_word_change(window):
    make_draft(window)
    ids = {r['title']: r['id'] for r in project.items(window.doc)}
    window.commit(
        [
            {'type': 'insert_text', 'target': 's1a', 'offset': 0, 'text': 'At dusk, '},
            {'type': 'project_add_item', 'kind': 'scene', 'text': 'Storm', 'parent': ids['Chapter two']},
        ]
    )
    dialog = CompareDraftsDialog(window)
    assert dialog.source.currentText() == 'First draft' and dialog.target.currentText() == 'main'
    rows = {
        dialog.table.topLevelItem(i).text(0).strip(): [
            dialog.table.topLevelItem(i).text(c) for c in range(1, 5)
        ]
        for i in range(dialog.table.topLevelItemCount())
    }
    assert rows['Arrival'] == ['Changed', rows['Arrival'][1], rows['Arrival'][2], '+2']
    assert rows['Storm'][0] == 'Added' and rows['The keeper'][0] == 'Unchanged'
    assert '1 changed, 1 added, 0 removed' in dialog.summary.text()
    arrival = next(
        i
        for i in range(dialog.table.topLevelItemCount())
        if 'Arrival' in dialog.table.topLevelItem(i).text(0)
    )
    dialog.table.setCurrentItem(dialog.table.topLevelItem(arrival))
    assert 'wording edit' in dialog.details.toPlainText()
    dialog.source.setCurrentText('main')
    assert 'Choose two different drafts' in dialog.summary.text() and dialog.table.topLevelItemCount() == 0
    assert unlabeled(dialog) == []


def test_bringing_changes_from_a_draft_is_a_proposal(window):
    make_draft(window, 'Alternate')
    window.doc, _ = drafts.switch_draft(window.doc, 'Alternate')
    window.refresh()
    window.commit([{'type': 'set_text', 'target': 's3a', 'text': 'The lamp stayed lit.'}])
    window.doc, _ = drafts.switch_draft(window.doc, 'main')
    window.refresh()
    dialog = CompareDraftsDialog(window, source='Alternate')
    assert dialog.bring_button.isEnabled()
    dialog.bring()
    proposal = window.doc['review']['proposals'][-1]
    assert proposal['kind'] == 'merge' and proposal['merge']['source_branch'] == 'Alternate'
    assert next(b for b in window.doc['blocks'] if b['id'] == 's3a')['text'] == 'The lamp failed at midnight.'
    assert 'Proposed changes' in window.status_label.text()


def test_compare_needs_two_drafts_and_menus_use_the_words_draft_and_compare(window):
    window.compare_drafts_dialog()
    assert window.test_messages and 'Save a draft first' in window.test_messages[-1][1]
    labels = {a.text() for a in window.findChildren(QAction)}
    assert {
        'Save draft as…',
        'Drafts…',
        'Compare drafts…',
        'Switch draft…',
        'Bring changes from draft…',
        'Start alternate draft…',
    } <= labels
    assert not any('branch' in label.lower() or label.startswith('Compare and merge') for label in labels)


def test_switch_and_bring_dialogs_say_so_when_there_is_no_other_draft(window):
    window.switch_draft()
    window.merge_draft()
    assert len(window.test_messages) == 2 and all('no other draft' in m[1] for m in window.test_messages)
