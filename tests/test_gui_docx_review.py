"""File > Send for review (Word)… and File > Merge reviewed copy… in the desktop window (offscreen)."""

import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from docx_word import sample_document, word_session
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QFileDialog, QLineEdit, QPushButton

from byax import gui_docx_review
from byax.gui import MainWindow
from byax.storage import load_document, save_document


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, monkeypatch, tmp_path):
    w = MainWindow()
    w.recovery_dir = tmp_path / 'recovery'
    w.recovery_dir.mkdir()
    errors, messages, reports = [], [], []
    monkeypatch.setattr(w, 'error', lambda error: errors.append(str(error)))
    monkeypatch.setattr(w, 'message', lambda title, text: messages.append((title, text)))
    monkeypatch.setattr(w, 'report', lambda title, result, art=None: reports.append((title, result)))
    w.test_errors, w.test_messages, w.test_reports = errors, messages, reports
    yield w
    w.dirty = False
    w.editor.blockSignals(True)
    w.close()


def opened(window, tmp_path):
    document = tmp_path / 'Lighthouse.byax'
    save_document(sample_document(), document)
    assert window.open_path(document)
    return document


def menu_action(window, text):
    return next(action for action in window.findChildren(QAction) if action.text() == text)


def test_the_file_menu_offers_both_actions_with_descriptions(window):
    for text in ('Send for review (Word)…', 'Merge reviewed copy…'):
        action = menu_action(window, text)
        assert action.isEnabled() and (action.toolTip() or action.statusTip())


def test_the_send_dialog_is_labelled_for_assistive_technology(window):
    dialog = gui_docx_review.SendForReviewDialog(window)
    for widget in dialog.findChildren(QLineEdit) + dialog.findChildren(QComboBox):
        assert widget.accessibleName() and widget.toolTip()
    for button in dialog.findChildren(QPushButton):
        assert button.accessibleName() or button.text()
    assert [dialog.restriction.itemData(i) for i in range(dialog.restriction.count())] == [
        'tracked_changes',
        'comments',
        'none',
    ]
    assert dialog.values() == {'reviewer': '', 'restrict': 'tracked_changes'}
    dialog.deleteLater()


def test_send_then_merge_opens_the_proposals_in_proposed_changes(window, tmp_path):
    document = opened(window, tmp_path)
    target = tmp_path / 'copy.docx'
    review_id = window.send_for_review(
        target=target, values={'reviewer': 'Jane Editor', 'restrict': 'comments'}
    )
    assert review_id and target.is_file() and not window.test_errors
    saved = load_document(document)
    assert (
        saved['history']['peer_bases']['review:' + review_id]
        == saved['metadata']['review_packages'][review_id]['revision']
    )
    assert 'Merge reviewed copy' in window.test_reports[-1][1]
    returned = tmp_path / 'returned.docx'
    returned.write_bytes(word_session(target.read_bytes()))
    report = window.merge_reviewed_word_copy(source=returned)
    assert report['proposals'] == 10 and not window.test_errors
    assert [b['text'] for b in window.doc['blocks'] if b['type'] != 'table'] == [
        b['text'] for b in saved['blocks'] if b['type'] != 'table'
    ]
    assert window.inspector.currentIndex() == 0
    kind, record = window.review_list.currentItem().data(Qt.UserRole)
    assert kind == 'proposals' and record['kind'] == 'docx_review' and record['status'] == 'pending'
    title, text = window.test_reports[-1]
    assert title == 'Merge reviewed copy' and 'Replace “dusk” with “nightfall”' in text
    assert window.merge_reviewed_word_copy(source=returned)['review_merge']['already_merged']
    assert window.test_messages[-1][1] == 'This returned copy was already merged.'


def test_the_menu_actions_run_through_their_dialogs(window, tmp_path, monkeypatch):
    opened(window, tmp_path)
    target = tmp_path / 'from-menu'
    monkeypatch.setattr(gui_docx_review.SendForReviewDialog, 'exec', lambda self: QDialog.Accepted)
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args, **kwargs: (str(target), ''))
    menu_action(window, 'Send for review (Word)…').trigger()
    copy = target.with_suffix('.docx')
    assert copy.is_file() and not window.test_errors
    returned = tmp_path / 'returned.docx'
    returned.write_bytes(word_session(copy.read_bytes()))
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args, **kwargs: (str(returned), ''))
    menu_action(window, 'Merge reviewed copy…').trigger()
    assert len([p for p in window.doc['review']['proposals'] if p['kind'] == 'docx_review']) == 10


def test_unsaved_documents_and_documents_without_a_package_are_explained(window, tmp_path):
    assert window.send_for_review(target=tmp_path / 'x.docx', values={}) is None
    assert 'Save the document as a ByAuthorX file first' in window.test_messages[-1][1]
    opened(window, tmp_path)
    assert window.merge_reviewed_word_copy(source=tmp_path / 'x.docx') is None
    assert 'no review copy to merge' in window.test_messages[-1][1]
