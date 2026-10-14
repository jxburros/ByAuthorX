"""Task lifetime, reduced motion, and presentation must preserve document behavior."""

import copy
import json
import threading
from types import SimpleNamespace

import pytest
from PIL import Image
from PySide6.QtCore import QAbstractAnimation, QSettings

from byax import model, storage
from byax.brand import BRAND_ROOT
from byax.gui import MainWindow


def make_window(qtbot, monkeypatch, tmp_path):
    settings = QSettings(str(tmp_path / 'appearance.ini'), QSettings.IniFormat)
    monkeypatch.setattr('byax.gui.QSettings', lambda *_: settings)
    monkeypatch.delenv('BYAX_REDUCED_MOTION', raising=False)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    return window


def test_empty_review_switches_to_real_items_and_back(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    original = copy.deepcopy(window.doc)
    assert window.review_stack.currentIndex() == 1
    assert all(not button.isVisible() for button in window.review_buttons)
    assert window.doc == original
    window.doc['review']['comments'].append(
        {'id': 'comment-one', 'text': 'Keep this thought.', 'status': 'open'}
    )
    window.update_sidebar()
    assert window.review_stack.currentIndex() == 0
    window.review_list.setCurrentRow(0)
    assert window.review_buttons[1].isEnabled()
    assert not window.review_buttons[3].isEnabled()
    window.doc['review']['comments'][0]['status'] = 'resolved'
    window.update_sidebar()
    assert window.review_stack.currentIndex() == 1


@pytest.mark.parametrize('state', ['completed', 'failed', 'cancelled', 'uncertain'])
def test_job_terminal_states_clear_activity(qtbot, monkeypatch, tmp_path, state):
    window = make_window(qtbot, monkeypatch, tmp_path)
    errors, results = [], []
    monkeypatch.setattr(window, 'error', errors.append)
    window.job_manager = SimpleNamespace(
        submit=lambda *_: {'id': 'test-job'},
        status=lambda *_, **__: {'state': state, 'error': {'message': 'Expected job failure'}},
        result=lambda *_, **__: {'result': 'finished'},
    )
    window.start_job('transcription', {}, lambda *_: None, results.append)
    qtbot.waitUntil(lambda: not window._active_tasks)
    assert window.activity_graphic.isHidden()
    assert window.activity_graphic.animation.state() == QAbstractAnimation.Stopped
    assert results == (['finished'] if state == 'completed' else [])
    assert errors == (['Expected job failure'] if state == 'failed' else [])


def test_current_style_uses_readable_label_and_canonical_key(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    index = window.style_box.findText('Heading 1')
    window.style_box.setCurrentIndex(index)
    window.style_box.activated.emit(index)
    assert window.doc['blocks'][0]['style'] == 'heading1'
    assert window.style_box.currentText() == 'Heading 1'
    window.dirty = False


def test_stored_check_findings_appear_once_in_review(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    finding = {'id': 'finding-one', 'message': 'Add an image description.', 'status': 'open'}
    window.doc['review']['findings'] = [finding]
    window._last_report = {'findings': [copy.deepcopy(finding)]}
    window.update_sidebar()
    assert window.review_list.count() == 1
    assert window.review_stack.currentIndex() == 0


def test_overlapping_tasks_keep_busy_until_last_result_and_failure(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    errors, results = [], []
    monkeypatch.setattr(window, 'error', errors.append)
    first, second = threading.Event(), threading.Event()
    original = copy.deepcopy(window.doc)

    def work(event, fail=False):
        assert event.wait(3)
        if fail:
            raise RuntimeError('Expected task failure')
        return 'done'

    try:
        window.background('First task…', lambda: work(first), results.append)
        window.background('Second task…', lambda: work(second, True), results.append)
        qtbot.waitUntil(lambda: window.activity_graphic.animation.state() == QAbstractAnimation.Running)
        height = window.statusBar().height()
        first.set()
        qtbot.waitUntil(lambda: results == ['done'])
        assert len(window._active_tasks) == 1
        assert window.activity_graphic.mode == 'busy'
        assert window.status_label.text() == 'Second task…'
        second.set()
        qtbot.waitUntil(lambda: bool(errors))
        assert errors == ['Expected task failure']
        assert not window._active_tasks
        assert window.activity_graphic.isHidden()
        assert window.activity_graphic.animation.state() == QAbstractAnimation.Stopped
        assert window.statusBar().height() == height
        assert window.doc == original
    finally:
        first.set()
        second.set()


def test_busy_pauses_for_reduced_motion_hide_and_minimize(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    window._begin_activity('test-task', 'Checking document…')
    qtbot.waitUntil(lambda: window.activity_graphic.animation.state() == QAbstractAnimation.Running)
    window.motion.set_reduced(True)
    assert window.activity_graphic.animation.state() == QAbstractAnimation.Stopped
    assert window.activity_graphic.isVisible()
    window.motion.set_reduced(False)
    assert window.activity_graphic.animation.state() == QAbstractAnimation.Running
    window.showMinimized()
    qtbot.waitUntil(lambda: window.activity_graphic.animation.state() == QAbstractAnimation.Stopped)
    window.showNormal()
    qtbot.waitUntil(lambda: window.activity_graphic.animation.state() == QAbstractAnimation.Running)
    window.hide()
    assert window.activity_graphic.animation.state() == QAbstractAnimation.Stopped
    window.show()
    qtbot.waitUntil(lambda: window.activity_graphic.animation.state() == QAbstractAnimation.Running)
    window._end_activity('test-task')
    assert window.activity_graphic.isHidden()


def test_explicit_save_has_finite_feedback_but_autosave_is_quiet(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    path = tmp_path / 'document.byax'
    storage.save_document(window.doc, path)
    assert window.open_path(path)
    window.editor.insertPlainText('The first line.')
    assert window.save()
    assert window.activity_graphic.mode == 'saved'
    qtbot.waitUntil(lambda: window.activity_graphic.animation.state() == QAbstractAnimation.Stopped)
    assert window.document_state.text() == 'Saved locally'
    qtbot.waitUntil(window.activity_graphic.isHidden, timeout=1800)
    window.editor.insertPlainText(' Another thought.')
    window.autosave()
    assert window.activity_graphic.isHidden()
    assert window.document_state.text() == 'Unsaved changes'
    assert storage.load_document(path)['blocks'][0]['text'] == 'The first line.'
    window.dirty = False


def test_baked_resources_have_exact_endpoints_and_no_document_art(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    window.doc = model.new_document(text='Authored words, without interface illustrations.')
    window.refresh()
    original = copy.deepcopy(window.doc)
    window._begin_activity('busy', 'Working…')
    window.motion.set_reduced(True)
    window.toggle_pages()
    window._end_activity('busy')
    assert window.doc == original
    assert window.doc['assets'] == original['assets']
    for name in ('busy-write', 'save-check', 'about-write'):
        manifest = json.loads((BRAND_ROOT / 'ui' / f'{name}.json').read_text())
        assert manifest['checks']['passed']
        assert manifest['frameTimesMs'][0] == 0
        assert manifest['frameTimesMs'][-1] == manifest['durationMs']
        with (
            Image.open(BRAND_ROOT / 'ui' / f'{name}-sprite.png') as sprite,
            Image.open(BRAND_ROOT / 'ui' / f'{name}-poster.png') as poster,
        ):
            width, height = poster.size
            assert sprite.size == (width * len(manifest['frameTimesMs']), height)
            if not manifest['loop']:
                assert (
                    sprite.crop((sprite.width - width, 0, sprite.width, height)).tobytes() == poster.tobytes()
                )
