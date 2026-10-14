import os
import threading
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from byax import engine, model, storage
from byax.gui import MainWindow
from byax.model import canonical_json


def _edit(doc, op, **envelope):
    return engine.apply(doc, {'operations': [op], **envelope})


def _history_growth(blocks, edits=30):
    doc = model.new_document(
        text='\n'.join(f'Paragraph {i} with some ordinary words.' for i in range(blocks))
    )
    target = doc['blocks'][0]['id']
    doc, _ = _edit(doc, {'type': 'set_text', 'target': target, 'text': 'start'})
    base = len(canonical_json(doc['history']))
    for i in range(edits):
        doc, _ = _edit(doc, {'type': 'insert_text', 'target': target, 'offset': 0, 'text': str(i)})
    return doc, (len(canonical_json(doc['history'])) - base) / edits


def test_history_bytes_per_edit_do_not_scale_with_block_count():
    _, small = _history_growth(10)
    large_doc, large = _history_growth(1000)
    assert large < small * 1.25 + 64
    assert sum('snapshot' in r for r in large_doc['history']['revisions']) == 1
    for record in large_doc['history']['revisions'][1:]:
        assert 'block_order' not in record['delta']
        assert 'block_order_diff' not in record['delta']
        assert record['bytes'] == len(canonical_json(record['delta']))


def test_structural_edit_records_order_as_a_splice():
    doc = model.new_document(text='\n'.join(f'Paragraph {i}' for i in range(1000)))
    doc, _ = _edit(doc, {'type': 'set_text', 'target': doc['blocks'][0]['id'], 'text': 'start'})
    after = doc['blocks'][500]['id']
    doc, _ = _edit(doc, {'type': 'insert_paragraph', 'after': after, 'text': 'New'})
    moved = doc['blocks'][10]['id']
    doc, _ = _edit(doc, {'type': 'move_block', 'target': moved, 'after': after})
    doc, _ = _edit(doc, {'type': 'set_text', 'target': doc['blocks'][0]['id'], 'text': 'end'})
    for record in doc['history']['revisions'][-2:]:
        assert record['bytes'] < 1500
        assert 'block_order_diff' in record['delta']
    states = [engine._revision(doc, r['revision']) for r in doc['history']['revisions']]
    assert len(states[-1]['blocks']) == 1001
    assert [b['id'] for b in states[-1]['blocks'][499:501]] == [after, moved]
    engine._STATES.clear()
    assert [engine._revision(doc, r['revision']) for r in doc['history']['revisions']] == states


def test_order_diff_round_trips_arbitrary_reorders():
    import random

    rng = random.Random(4)
    for _ in range(50):
        old = [f'b{i}' for i in range(rng.randint(0, 200))]
        new = [x for x in old if rng.random() > 0.1] + [f'n{i}' for i in range(rng.randint(0, 5))]
        rng.shuffle(new) if rng.random() < 0.3 else None
        order = list(old)
        for start, end, inserted in reversed(engine._order_diff(old, new) or []):
            order[start:end] = inserted
        assert order == new


def test_snapshots_follow_accumulated_delta_size():
    doc = model.new_document(text='Short')
    target = doc['blocks'][0]['id']
    versions = []
    for i in range(120):
        doc, _ = _edit(doc, {'type': 'set_text', 'target': target, 'text': f'{i} ' + 'w' * 400})
        versions.append((doc['revision'], f'{i} ' + 'w' * 400))
    records = doc['history']['revisions']
    snapshots = [i for i, r in enumerate(records) if 'snapshot' in r]
    assert 1 < len(snapshots) < 20
    accumulated = 0
    for record in records[snapshots[1] - 1 : snapshots[0] : -1]:
        accumulated += record['bytes']
    assert accumulated >= engine._SNAPSHOT_MIN_BYTES
    engine._STATES.clear()
    for revision, text in versions[::17]:
        restored, _ = engine.restore(doc, revision)
        assert restored['blocks'][0]['text'] == text


def test_request_records_are_capped_and_gui_commits_record_none():
    doc = model.new_document(text='x')
    target = doc['blocks'][0]['id']
    doc, _ = _edit(doc, {'type': 'set_text', 'target': target, 'text': 'g'}, request_id='gui-1', origin='gui')
    assert doc['requests'] == {}
    for i in range(engine.REQUEST_LIMIT + 20):
        doc, _ = _edit(doc, {'type': 'set_text', 'target': target, 'text': str(i)}, request_id=f'r{i}')
    assert len(doc['requests']) == engine.REQUEST_LIMIT
    assert 'r0' not in doc['requests'] and f'r{engine.REQUEST_LIMIT + 19}' in doc['requests']
    _, result = _edit(doc, {'type': 'set_text', 'target': target, 'text': '519'}, request_id='r519')
    assert result['execution'] == 'already_committed'


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
    w.wait_for_save()
    w.dirty = False
    w.editor.blockSignals(True)
    w.close()


@pytest.fixture
def gated_save(monkeypatch):
    real = storage.save_document
    gate = threading.Event()
    calls = []

    def slow(doc, path, **kwargs):
        calls.append({'path': path, 'thread': threading.get_ident(), 'revision': doc['revision']})
        assert gate.wait(10)
        real(doc, path, **kwargs)
        calls[-1]['done'] = True

    monkeypatch.setattr(storage, 'save_document', slow)
    return gate, calls


def test_save_returns_to_event_loop_before_serialization(window, tmp_path, gated_save):
    gate, calls = gated_save
    path = tmp_path / 'doc.byax'
    storage.DocumentLock(path, owner=window.owner)
    window.path = path
    window._disk_stamp = None
    window.editor.insertPlainText('Saved text')
    started = time.perf_counter()
    assert window.save()
    assert time.perf_counter() - started < 0.5
    assert window._save_task is not None and not path.exists()
    assert window.document_state.text() == 'Saving…'
    window.check_external_change()
    assert 'outside' not in window.status_label.text()
    deadline = time.perf_counter() + 5
    while not calls and time.perf_counter() < deadline:
        QApplication.processEvents()
    assert calls[0]['thread'] != threading.get_ident()
    window.editor.insertPlainText('More ')
    assert window.flush_edits()
    gate.set()
    assert window.wait_for_save()
    assert storage.load_document(path)['blocks'][0]['text'] == 'Saved text'
    assert window.dirty, 'edits made during the write must stay unsaved'
    assert window._disk_stamp == window.file_stamp(path)
    assert window.save(wait=True)
    assert not window.dirty
    assert storage.load_document(path)['blocks'][0]['text'] == 'Saved textMore '


def test_second_save_and_autosave_queue_behind_running_write(window, tmp_path, gated_save):
    gate, calls = gated_save
    path = tmp_path / 'doc.byax'
    window.path = path
    window.editor.insertPlainText('one')
    assert window.save()
    window.editor.insertPlainText('two ')
    assert window.flush_edits()
    assert window.save()
    window.autosave()
    assert window._save_again
    gate.set()
    assert window.wait_for_save()
    assert [c['path'] for c in calls] == [path, path]
    assert all(c.get('done') for c in calls)
    assert storage.load_document(path)['blocks'][0]['text'] == 'onetwo '
    assert not window.dirty


def test_autosave_runs_on_worker(window, gated_save):
    gate, calls = gated_save
    window.editor.insertPlainText('Recovered')
    window.autosave()
    assert window._save_task is not None
    gate.set()
    window.wait_for_save()
    assert calls[0]['thread'] != threading.get_ident()
    assert storage.load_document(window.recovery_path())['blocks'][0]['text'] == 'Recovered'
    assert window.dirty


def test_close_waits_for_in_flight_save(window, tmp_path, gated_save):
    gate, calls = gated_save
    path = tmp_path / 'doc.byax'
    window.path = path
    window.lock = storage.DocumentLock(path, owner=window.owner).acquire()
    window.editor.insertPlainText('Closing')
    assert window.save()
    QTimer.singleShot(150, gate.set)
    window.close()
    assert calls and calls[0].get('done')
    assert window.lock is None
    assert storage.load_document(path)['blocks'][0]['text'] == 'Closing'
    other = storage.DocumentLock(path, owner='someone else').acquire()
    other.release()
