"""Bounded GUI soak: open, many mixed edits, periodic saves, then a long undo/redo walk."""

import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import random
import time

import pytest
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication

from byax import model, storage
from byax.gui import MainWindow

STEPS = 300
BUDGET_SECONDS = 45


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


def _text(doc):
    return '\n'.join(block['text'] for block in doc['blocks'])


def _edit(rng, editor):
    cursor = editor.textCursor()
    length = len(editor.toPlainText())
    action = rng.random()
    if action < 0.15:
        cursor.setPosition(rng.randint(0, length))
        editor.setTextCursor(cursor)
    if action < 0.6:
        editor.insertPlainText(rng.choice(['word ', 'é', 'ab', 'x', '文字 ']))
    elif action < 0.7:
        editor.insertPlainText('\n')
    elif action < 0.9:
        if cursor.position():
            cursor.deletePreviousChar()
            editor.setTextCursor(cursor)
    else:
        cursor.setPosition(rng.randint(0, length))
        cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, 3)
        cursor.removeSelectedText()
        editor.setTextCursor(cursor)


def test_gui_soak_open_edit_save_undo(window, tmp_path):
    path = tmp_path / 'soak.byax'
    storage.save_document(model.new_document('Soak', 'Opening line\nSecond line'), path)
    assert window.open_path(path)
    rng = random.Random(4747)
    started = time.monotonic()
    for step in range(STEPS):
        _edit(rng, window.editor)
        assert window.flush_edits(), step
        if step % 60 == 59:
            assert window.save()
            saved = storage.load_document(path)
            model.validate_document(saved)
            assert _text(saved) == _text(window.doc)
        assert time.monotonic() - started < BUDGET_SECONDS, f'soak exceeded budget at step {step}'
    assert not window.test_errors
    assert window.editor.toPlainText() == _text(window.doc)
    final = _text(window.doc)
    for _ in range(40):
        window.undo()
    model.validate_document(window.doc)
    assert _text(window.doc) != final
    assert window.editor.toPlainText() == _text(window.doc)
    for _ in range(40):
        window.redo()
    assert _text(window.doc) == final
    assert window.save()
    assert _text(storage.load_document(path)) == final
    assert not window.test_errors
