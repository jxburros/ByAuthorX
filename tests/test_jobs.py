import json
import threading

import pytest

from byax.errors import ByaxError
from byax.jobs import JobManager


def test_job_freezes_input_and_persists_result(tmp_path):
    manager = JobManager(tmp_path)
    payload = {'revision': 'rev1', 'text': 'original', 'remote': False}
    gate = threading.Event()

    def worker(frozen, cancel):
        gate.wait(2)
        return {'text': frozen['text']}

    job = manager.submit('local', payload, worker)
    payload['text'] = 'changed'
    gate.set()
    result = manager.wait(job['id'], 3)
    assert result['state'] == 'completed'
    assert result['result']['text'] == 'original'
    assert manager.status(job['id'], current_revision='rev2')['stale']
    manager.close()
    fresh = JobManager(tmp_path)
    assert fresh.result(job['id'])['result'] == {'text': 'original'}
    fresh.close()


def test_running_remote_cancellation_is_uncertain(tmp_path):
    manager = JobManager(tmp_path)
    started = threading.Event()
    gate = threading.Event()

    def worker(payload, cancel):
        started.set()
        gate.wait(2)
        return {'request_id': 'remote_123', 'text': 'may have billed'}

    job = manager.submit('review', {'revision': 'rev1', 'remote': True}, worker)
    assert started.wait(2)
    assert manager.cancel(job['id'])['state'] == 'uncertain'
    gate.set()
    assert manager.wait(job['id'], 3)['state'] == 'uncertain'
    recovered = manager.recover(job['id'])
    assert 'No request resubmitted' in recovered['recovery']
    assert recovered['remote_id'] == 'remote_123'
    queried = []

    def lookup(remote_id):
        queried.append(remote_id)
        return {'state': 'completed', 'result': {'text': 'recorded result'}}

    assert manager.recover(job['id'], query_remote=lookup)['state'] == 'completed'
    assert queried == ['remote_123']
    manager.close()


def test_process_interruption_marks_uncertain_without_reexecution(tmp_path):
    manager = JobManager(tmp_path)
    job = manager.submit('local', {'remote': False}, lambda p, c: {})
    manager.wait(job['id'])
    path = tmp_path / (job['id'] + '.json')
    raw = json.loads(path.read_text())
    raw.update(state='running', owner_pid=99999999)
    path.write_text(json.dumps(raw))
    assert manager.status(job['id'])['state'] == 'uncertain'
    assert manager.recover(job['id'])['state'] == 'uncertain'
    manager.close()


def test_worker_exception_does_not_persist_secret(tmp_path):
    manager = JobManager(tmp_path)

    def worker(p, c):
        raise RuntimeError('request key=secret-value')

    job = manager.submit('local', {}, worker)
    assert manager.wait(job['id'])['state'] == 'failed'
    assert 'secret-value' not in (tmp_path / (job['id'] + '.json')).read_text()
    with pytest.raises(ByaxError, match='secret'):
        manager.submit('review', {'api_key': 'secret'}, worker)
    manager.close()
