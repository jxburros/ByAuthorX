"""File > Send to beta readers… and File > Import reader comments… in the desktop window (offscreen)."""

import json
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from docx_word import PARAGRAPHS, sample_document
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
)

from byax import beta_readers, gui_beta_readers
from byax.gui import MainWindow
from byax.quote_match import selectors
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


def comment_file(tmp_path, name, saved, review_id, reader, comments):
    package = saved['metadata']['review_packages'][review_id]
    path = tmp_path / name
    path.write_text(
        json.dumps(
            {
                'format': 'byax-comments',
                'version': 1,
                'review_id': review_id,
                'document_id': saved['id'],
                'snapshot_hash': package['snapshot_hash'],
                'reader': reader,
                'comments': comments,
            }
        )
    )
    return path


def anchored(block, text, quote, ident, **extra):
    source = PARAGRAPHS[int(block[2:]) - 1]
    at = source.index(quote)
    return {
        'id': ident,
        'block': block,
        'kind': 'comment',
        'text': text,
        **selectors(source, (at, at + len(quote))),
        **extra,
    }


def test_the_file_menu_offers_both_actions_with_descriptions(window):
    for text in ('Send to beta readers…', 'Import reader comments…'):
        action = menu_action(window, text)
        assert action.isEnabled() and (action.toolTip() or action.statusTip())


def test_the_send_dialog_is_labelled_for_assistive_technology_and_html_only_options_follow_the_format(window):
    dialog = gui_beta_readers.SendToBetaReadersDialog(window)
    for widget in (
        dialog.findChildren(QLineEdit)
        + dialog.findChildren(QComboBox)
        + dialog.findChildren(QCheckBox)
        + dialog.findChildren(QPlainTextEdit)
    ):
        assert widget.accessibleName() and widget.toolTip()
    for button in dialog.findChildren(QPushButton):
        assert button.accessibleName() or button.text()
    assert [dialog.format.itemData(i) for i in range(dialog.format.count())] == ['html', 'pdf', 'docx']
    assert dialog.values() == {'format': 'html', 'reviewer': '', 'companion': False, 'note': ''}
    dialog.companion.setChecked(True)
    dialog.note.setPlainText('  Please read kindly.  ')
    dialog.reader.setText(' Sam ')
    assert dialog.values() == {
        'format': 'html',
        'reviewer': 'Sam',
        'companion': True,
        'note': 'Please read kindly.',
    }
    dialog.format.setCurrentIndex(1)
    assert not dialog.companion.isEnabled() and not dialog.note.isEnabled()
    assert dialog.values() == {'format': 'pdf', 'reviewer': 'Sam', 'companion': False, 'note': ''}
    dialog.deleteLater()


def test_send_records_the_snapshot_and_import_adds_attributed_comments_and_proposals(window, tmp_path):
    document = opened(window, tmp_path)
    target = tmp_path / 'readers.html'
    review_id = window.send_to_beta_readers(
        target=target, values={'format': 'html', 'reviewer': '', 'companion': False, 'note': 'Be honest.'}
    )
    assert review_id and target.is_file() and not window.test_errors
    assert b'Be honest.' in target.read_bytes()
    saved = load_document(document)
    record = saved['metadata']['review_packages'][review_id]
    assert (
        saved['history']['peer_bases']['review:' + review_id] == record['revision']
        and record['format'] == 'html'
    )
    assert (
        'send the file' in window.test_reports[-1][1].lower()
        or 'Send it to your readers' in window.test_reports[-1][1]
    )
    hostile = '<b>bold</b> & <script>alert(1)</script>'
    sam = comment_file(
        tmp_path,
        'Sam.byaxcomments.json',
        saved,
        review_id,
        'Sam',
        [
            anchored('p_2', hostile, 'the sea worked', 'cm_a'),
            anchored('p_3', 'Shorter?', 'eleven years', 'cm_b', kind='suggestion', replacement='ten years'),
        ],
    )
    kim = comment_file(
        tmp_path,
        'Kim.byaxcomments.json',
        saved,
        review_id,
        'Kim',
        [
            anchored('p_2', 'Kim agrees', 'the sea worked', 'cm_a'),
        ],
    )
    before = [b['text'] for b in window.doc['blocks']]
    report = window.import_reader_comments(sources=[sam, kim])
    assert report['proposals'] == 1 and not window.test_errors
    assert [b['text'] for b in window.doc['blocks']] == before
    assert window.inspector.currentIndex() == 0
    kind, record = window.review_list.currentItem().data(Qt.UserRole)
    assert kind == 'proposals' and record['kind'] == 'beta_reader' and record['author'] == 'Sam'
    labels = [window.review_list.item(i).text() for i in range(window.review_list.count())]
    assert f'Sam: {hostile}' in labels and 'Kim: Kim agrees' in labels  # plain text, attributed to the reader
    title, text = window.test_reports[-1]
    assert title == 'Import reader comments' and 'Sam.byaxcomments.json (Sam)' in text and 'Replace' in text
    assert (
        window.import_reader_comments(sources=[sam])['comment_merge']['files'][0]['status']
        == 'already_merged'
    )
    persisted = window.doc
    assert len([c for c in persisted['review']['comments'] if c.get('source') == 'beta_reader']) == 3


def test_a_companion_copy_records_nothing(window, tmp_path):
    document = opened(window, tmp_path)
    revision = load_document(document)['revision']
    target = tmp_path / 'companion.html'
    window.send_to_beta_readers(
        target=target, values={'format': 'html', 'reviewer': '', 'companion': True, 'note': ''}
    )
    assert target.is_file() and b'<script' not in target.read_bytes() and not window.test_errors
    assert load_document(document)['revision'] == revision
    assert 'nothing was recorded' in window.test_reports[-1][1]


def test_pdf_and_word_copies_and_the_menu_dialogs(window, tmp_path, monkeypatch):
    opened(window, tmp_path)
    target = tmp_path / 'from-menu'
    monkeypatch.setattr(
        gui_beta_readers.SendToBetaReadersDialog,
        'values',
        lambda self: {'format': 'pdf', 'reviewer': 'Pat', 'companion': False, 'note': ''},
    )
    monkeypatch.setattr(gui_beta_readers.SendToBetaReadersDialog, 'exec', lambda self: QDialog.Accepted)
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args, **kwargs: (str(target), ''))
    menu_action(window, 'Send to beta readers…').trigger()
    copy = target.with_suffix('.pdf')
    assert copy.is_file() and copy.read_bytes().startswith(b'%PDF') and not window.test_errors
    assert {p['format'] for p in window.doc['metadata']['review_packages'].values()} == {'pdf'}
    monkeypatch.setattr(QFileDialog, 'getOpenFileNames', lambda *args, **kwargs: ([], ''))
    menu_action(window, 'Import reader comments…').trigger()
    assert not window.test_errors


def test_unsaved_documents_and_documents_without_a_copy_are_explained(window, tmp_path):
    assert window.send_to_beta_readers(target=tmp_path / 'x.html', values={}) is None
    assert 'Save the document as a ByAuthorX file first' in window.test_messages[-1][1]
    opened(window, tmp_path)
    assert window.import_reader_comments(sources=[tmp_path / 'x.json']) is None
    assert 'no reader copy' in window.test_messages[-1][1]


def test_a_refused_file_is_reported_as_an_error_not_a_crash(window, tmp_path):
    opened(window, tmp_path)
    target = tmp_path / 'readers.html'
    window.send_to_beta_readers(target=target, values={'format': 'html'})
    bad = tmp_path / 'bad.json'
    bad.write_text('{"format": "something else"}')
    assert window.import_reader_comments(sources=[bad]) is None
    assert window.test_errors and 'not a ByAuthorX comment file' in window.test_errors[-1]
    assert beta_readers.FORMATS == ('html', 'pdf', 'docx')
