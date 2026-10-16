"""The synced-folder info bar and the conflicted-copy review flow in the desktop window."""

import os
import shutil

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QPushButton

from byax import gui_sync, storage, sync_safety
from byax.cli import execute
from byax.gui import MainWindow

TEXT = 'Chapter one\nThe first section text.\nChapter two\nThe second section text.'


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
    messages = []
    monkeypatch.setattr(w, 'message', lambda title, text: messages.append((title, text)))
    w.test_errors, w.test_messages = errors, messages
    yield w
    w.dirty = False
    w.editor.blockSignals(True)
    w.close()


def edit(path, target, text):
    execute(
        'apply',
        {'path': str(path), 'input': {'operations': [{'type': 'set_text', 'target': target, 'text': text}]}},
    )


def make_pair(folder, copy_name="Novel (Jeff's conflicted copy 2026-10-14).byax"):
    folder.mkdir(parents=True, exist_ok=True)
    document = folder / 'Novel.byax'
    execute('new', {'out': str(document), 'text': TEXT, 'overwrite': True})
    copy = folder / copy_name
    shutil.copy(document, copy)
    return document, copy, [b['id'] for b in storage.load_document(document)['blocks']]


def pick_dialog(monkeypatch):
    """Capture the conflicted-copies dialog instead of running it modally."""
    shown = []
    monkeypatch.setattr(gui_sync.ConflictedCopiesDialog, 'exec', lambda self: shown.append(self) or 0)
    return shown


def test_the_bar_is_hidden_for_an_ordinary_folder_and_new_documents(window, tmp_path):
    document, copy, _ = make_pair(tmp_path / 'plain')
    copy.unlink()
    assert window.open_path(document)
    assert window.sync_bar.isHidden()
    window.new_file()
    assert window.sync_bar.isHidden()


def test_a_conflicted_copy_shows_a_non_modal_bar_with_a_review_button(window, tmp_path, monkeypatch):
    document, copy, _ids = make_pair(tmp_path / 'Dropbox' / 'Books')
    assert window.open_path(document)
    bar = window.sync_bar
    assert not bar.isHidden() and not isinstance(bar, QDialog) and not window.isModal()
    assert 'conflicted copy' in bar.text.text() and 'Dropbox' in bar.text.text()
    assert not bar.review_button.isHidden()
    # Typing is not blocked while the notice is up.
    window.editor.setFocus()
    window.editor.insertPlainText('typed ')
    assert window.editor.toPlainText().startswith('typed ')
    assert window.flush_edits()
    called = []
    monkeypatch.setattr(window, 'review_conflicted_copies', lambda: called.append(True))
    bar.review_button.click()
    assert called == [True]
    assert copy.exists()


def test_the_bar_controls_have_accessible_names_and_descriptions(window, tmp_path):
    document, _, _ = make_pair(tmp_path / 'Dropbox')
    assert window.open_path(document)
    bar = window.sync_bar
    assert bar.accessibleName() and bar.text.accessibleName()
    for button in bar.findChildren(QPushButton):
        assert button.accessibleName() and button.toolTip(), button.text()
    assert {b.accessibleName() for b in bar.findChildren(QPushButton)} == {
        'Review conflicted copy',
        'Synced folder details',
        'Dismiss synced folder notice',
    }
    labels = {a.text() for a in window.findChildren(QAction)}
    assert {'Synced folder safety…', 'Review conflicted copies…'} <= labels


def test_dismissing_hides_the_bar_until_something_new_appears(window, tmp_path):
    document, _copy, _ = make_pair(tmp_path / 'Dropbox')
    assert window.open_path(document)
    window.sync_bar.dismiss_button.click()
    assert window.sync_bar.isHidden()
    window.refresh_sync_bar()
    assert window.sync_bar.isHidden()
    shutil.copy(document, document.parent / 'Novel (Sam’s conflicted copy 2026-10-15).byax')
    window.refresh_sync_bar()
    assert not window.sync_bar.isHidden() and '2 conflicted' in window.sync_bar.text.text()


def test_a_risky_pattern_without_a_copy_is_shown_only_in_a_synced_folder(window, tmp_path):
    synced, copy, _ = make_pair(tmp_path / 'OneDrive' / 'Books')
    copy.unlink()
    (synced.parent / '.Novel.byax.abc123.tmp').write_bytes(b'half')
    assert window.open_path(synced)
    bar = window.sync_bar
    assert not bar.isHidden() and 'OneDrive' in bar.text.text() and 'temporary save file' in bar.text.text()
    assert bar.review_button.isHidden()
    plain, copy, _ = make_pair(tmp_path / 'plain')
    copy.unlink()
    (plain.parent / '.Novel.byax.abc123.tmp').write_bytes(b'half')
    assert window.open_path(plain)
    assert window.sync_bar.isHidden()


def test_details_describe_the_provider_evidence_and_risks(window, tmp_path, monkeypatch):
    document, _, _ = make_pair(tmp_path / 'Dropbox')
    assert window.open_path(document)
    shown = []
    monkeypatch.setattr(window, 'report', lambda title, result, art=None: shown.append((title, result)))
    window.sync_bar.details_button.click()
    title, text = shown[0]
    assert title == 'Synced folder safety'
    assert 'Dropbox folder' in text and 'Conflicted copies of this document' in text
    assert 'The editing lock is stored on this computer only' in text
    # This very window holds the lock, so it must not be reported as another session.
    assert 'another ByAuthorX window' not in text


def test_review_dialog_merges_a_copy_into_proposed_changes_without_touching_files(
    window, tmp_path, monkeypatch
):
    document, copy, ids = make_pair(tmp_path / 'Dropbox')
    edit(document, ids[1], 'Laptop edit.')
    edit(copy, ids[3], 'Desktop edit.')
    copy_bytes, document_bytes = copy.read_bytes(), document.read_bytes()
    assert window.open_path(document)
    shown = pick_dialog(monkeypatch)
    window.review_conflicted_copies()
    dialog = shown[0]
    assert dialog.copies.count() == 1 and dialog.copies.accessibleName()
    assert dialog.merge_button.isEnabled() and not dialog.archive_button.isEnabled()
    assert 'Dropbox conflicted copy' in dialog.detail.text()
    dialog.merge_button.click()
    proposals = [p for p in window.doc['review']['proposals'] if p['kind'] == 'merge']
    assert len(proposals) == 1 and proposals[0]['status'] == 'pending' and not proposals[0]['conflicts']
    assert window.inspector.currentIndex() == 0
    assert window.review_list.currentItem().data(0x100)[1]['id'] == proposals[0]['id']
    assert window.dirty and window.test_errors == []
    # Nothing changed on disk: the proposal exists only in the open window until saved.
    assert copy.read_bytes() == copy_bytes and document.read_bytes() == document_bytes
    assert [b['text'] for b in window.doc['blocks']][1] == 'Laptop edit.'
    # Accepting through the existing review flow brings in the desktop edit as well.
    window.commit([{'type': 'accept_proposal', 'target': proposals[0]['id']}])
    assert [b['text'] for b in window.doc['blocks']][1::2] == ['Laptop edit.', 'Desktop edit.']
    assert window.save(wait=True)
    assert [b['text'] for b in storage.load_document(document)['blocks']][3] == 'Desktop edit.'


def test_same_section_conflicts_are_explained_and_resolved_with_the_existing_flow(
    window, tmp_path, monkeypatch
):
    document, copy, ids = make_pair(tmp_path / 'Dropbox')
    edit(document, ids[3], 'The second part text.')
    edit(copy, ids[3], 'The second chapter text.')
    assert window.open_path(document)
    assert window.merge_conflicted_copy(str(copy))
    assert window.test_messages and 'same section' in window.test_messages[0][1]
    assert 'Resolve selected merge conflicts' in window.test_messages[0][1]
    proposal = next(p for p in window.doc['review']['proposals'] if p['kind'] == 'merge')
    assert len(proposal['conflicts']) == 1
    monkeypatch.setattr(
        'byax.gui_workflows.QInputDialog.getItem', staticmethod(lambda *a, **k: ('Use incoming draft', True))
    )
    monkeypatch.setattr(window, 'report', lambda *a, **k: None)
    window.resolve_merge_conflicts()
    resolved = next(p for p in window.doc['review']['proposals'] if p['kind'] == 'merge')
    assert resolved['conflicts'][0]['status'] == 'resolved'
    window.commit([{'type': 'accept_proposal', 'target': proposal['id']}])
    assert window.doc['blocks'][3]['text'] == 'The second chapter text.'


def test_a_failed_merge_leaves_the_open_document_unchanged(window, tmp_path):
    document, copy, _ids = make_pair(tmp_path / 'Dropbox')
    other = document.parent / 'Novel (2).byax'
    execute('new', {'out': str(other), 'text': 'Another book'})
    assert window.open_path(document)
    revision, dirty = window.doc['revision'], window.dirty
    assert not window.merge_conflicted_copy(str(other))
    assert window.doc['revision'] == revision and window.dirty == dirty
    assert window.test_errors and 'different document' in window.test_errors[0]
    assert not window.merge_conflicted_copy(str(copy))
    assert window.test_errors[-1].startswith('The conflicted copy has nothing new')
    assert not [p for p in window.doc['review']['proposals'] if p['kind'] == 'merge']


def test_a_merged_copy_can_be_moved_into_the_archive_folder_after_saving(window, tmp_path, monkeypatch):
    document, copy, ids = make_pair(tmp_path / 'Dropbox')
    edit(copy, ids[3], 'Desktop edit.')
    assert window.open_path(document)
    assert window.merge_conflicted_copy(str(copy))
    proposal = next(p for p in window.doc['review']['proposals'] if p['kind'] == 'merge')
    window.commit([{'type': 'accept_proposal', 'target': proposal['id']}])
    shown = pick_dialog(monkeypatch)
    window.review_conflicted_copies()
    dialog = shown[0]
    assert '(already merged)' in dialog.copies.item(0).text()
    assert dialog.archive_button.isEnabled() and not dialog.merge_button.isEnabled()
    # Unsaved merge results are not on disk yet, so nothing is moved.
    window.test_messages.clear()
    dialog.archive_button.click()
    assert copy.exists() and 'Save the document first' in window.test_messages[0][1]
    assert window.save(wait=True)
    monkeypatch.setattr(QMessageBox, 'question', staticmethod(lambda *a, **k: QMessageBox.Yes))
    dialog.archive_button.click()
    assert not copy.exists()
    assert (document.parent / sync_safety.ARCHIVE_FOLDER / copy.name).exists()
    assert dialog.copies.count() == 0 and window.sync_bar.isHidden()


def test_a_sync_delivered_file_is_not_overwritten_when_saving(window, tmp_path):
    document, copy, ids = make_pair(tmp_path / 'Dropbox')
    copy.unlink()
    assert window.open_path(document)
    window.commit([{'type': 'set_text', 'target': ids[0], 'text': 'Local edit'}])
    delivered = tmp_path / 'delivered.byax'
    execute('new', {'out': str(delivered), 'text': 'Delivered by sync'})
    os.replace(delivered, document)
    delivered_bytes = document.read_bytes()
    # The window notices the change before writing.
    assert not window.save(wait=True)
    assert 'changed outside' in window.test_errors[-1] and document.read_bytes() == delivered_bytes
    # Even when the change lands after that check, the lock-time stamp check refuses to replace it.
    window.test_errors.clear()
    stale = window.file_stamp(document)
    os.replace(
        make_other(tmp_path, 'Delivered again'),
        document,
    )
    window._disk_stamp = stale
    window.file_stamp = lambda path: stale
    assert not window.save(wait=True)
    assert any('Nothing was overwritten' in e for e in window.test_errors)
    assert storage.load_document(document)['blocks'][0]['text'] == 'Delivered again'


def make_other(folder, text):
    path = folder / 'other-delivery.byax'
    execute('new', {'out': str(path), 'text': text, 'overwrite': True})
    return path
