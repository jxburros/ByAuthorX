"""Recoverable job transitions, explicit cancellation outcomes and bounded batch results."""

import json
import os
import threading

import pytest

from byax.cli import execute, main
from byax.errors import ByaxError
from byax.jobs import TRANSITIONS, JobManager, run_batch
from byax.model import new_document
from byax.storage import save_document


def _states(job):
    return [(item['from'], item['to']) for item in job['transitions']]


def test_every_state_transition_is_recorded_and_terminal_states_are_final(tmp_path):
    manager = JobManager(tmp_path, max_workers=1)
    completed = manager.wait(manager.submit('local', {'remote': False}, lambda p, c: {'ok': True})['id'], 3)
    assert completed['state'] == 'completed' and _states(completed) == [
        ('queued', 'running'),
        ('running', 'completed'),
    ]

    def boom(payload, cancel):
        raise RuntimeError('secret provider text sk-live-123')

    failed = manager.wait(manager.submit('local', {'remote': False}, boom)['id'], 3)
    assert failed['state'] == 'failed' and 'sk-live' not in json.dumps(failed)
    assert manager.cancel(completed['id'])['state'] == 'completed'
    assert manager.progress(failed['id'], 0.5)['state'] == 'failed'
    gate, started = threading.Event(), threading.Event()

    def slow(payload, cancel):
        started.set()
        gate.wait(3)
        return {'late': True}

    running = manager.submit('local', {'remote': False}, slow)
    queued = manager.submit('review', {'remote': True}, slow)
    assert started.wait(3)
    cancelled_queued = manager.cancel(queued['id'])
    assert (
        cancelled_queued['state'] == 'cancelled' and cancelled_queued['cancellation']['remote'] == 'not_sent'
    )
    local = manager.cancel(running['id'])
    assert local['state'] == 'cancelled' and local['cancellation'] == {
        'local': 'acknowledged',
        'acknowledged_at': local['cancellation']['acknowledged_at'],
        'remote': 'not_applicable',
    }
    gate.set()
    late = manager.wait(running['id'], 3)
    assert late['state'] == 'cancelled' and late['late_result'] and late['stale_reasons'] == ['late_result']
    assert manager.wait(queued['id'], 3)['state'] == 'cancelled'
    assert set(TRANSITIONS['completed']) == set() and TRANSITIONS['uncertain'] == {
        'completed',
        'failed',
        'cancelled',
    }
    manager.close()


def test_remote_cancellation_acknowledges_locally_and_recovers_by_lookup_only(tmp_path):
    lookups = []

    def lookup(remote_id):
        lookups.append(remote_id)
        return {'state': 'completed', 'result': {'text': 'remote finished'}}

    manager = JobManager(tmp_path, remote_lookups={'review': lookup})
    started, gate, calls = threading.Event(), threading.Event(), []

    def worker(payload, cancel):
        calls.append(1)
        started.set()
        gate.wait(3)
        return {'request_id': 'req_42'}

    job = manager.submit('review', {'revision': 'rev1', 'remote': True}, worker)
    assert started.wait(3)
    cancelled = manager.cancel(job['id'])
    assert cancelled['state'] == 'uncertain'
    assert (
        cancelled['cancellation']['local'] == 'acknowledged'
        and cancelled['cancellation']['remote'] == 'unknown'
    )
    gate.set()
    manager.wait(job['id'], 3)
    with pytest.raises(ByaxError) as error:
        manager.retry(job['id'], worker)
    assert error.value.code == 'review_required' and error.value.details['remote_id'] == 'req_42'
    recovered = manager.recover(job['id'])
    assert recovered['state'] == 'completed' and recovered['result'] == {'text': 'remote finished'}
    assert recovered['cancellation']['remote'] == 'continued' and lookups == ['req_42'] and calls == [1]
    assert ('uncertain', 'completed') in _states(recovered)
    manager.close()


def test_frozen_versions_mark_results_stale(tmp_path):
    manager = JobManager(tmp_path)
    job = manager.submit(
        'check',
        {'revision': 'rev1', 'profile_version': 'p1', 'source_versions': {'s': 'v1'}, 'remote': False},
        lambda p, c: {'ok': True},
    )
    manager.wait(job['id'], 3)
    assert not manager.status(
        job['id'], current_revision='rev1', current_profile_version='p1', current_sources={'s': 'v1'}
    )['stale']
    stale = manager.result(
        job['id'], current_revision='rev2', current_profile_version='p2', current_sources={'s': 'v2'}
    )
    assert stale['stale'] and stale['stale_reasons'] == ['revision', 'profile', 'source:s']
    manager.close()


def test_interrupted_owner_becomes_uncertain_without_resubmission(tmp_path):
    manager = JobManager(tmp_path)
    job_id = 'job_' + '0' * 32
    record = {
        'id': job_id,
        'kind': 'transcription',
        'state': 'running',
        'progress': 0.3,
        'created_at': 0,
        'owner_pid': 2**22 + 12345,
        'payload': {},
        'input_revision': 'rev',
        'profile_version': None,
        'source_versions': {},
        'remote': True,
        'remote_id': None,
        'result': None,
        'error': None,
        'cancel_requested': False,
        'transitions': [],
    }
    (tmp_path / (job_id + '.json')).write_text(json.dumps(record))
    status = manager.status(job_id)
    assert status['state'] == 'uncertain' and _states(status) == [('running', 'uncertain')]
    recovered = manager.recover(job_id)
    assert 'No request resubmitted' in recovered['recovery']
    manager.close()


def test_configurable_job_limits_bound_queue_and_payload(tmp_path):
    with pytest.raises(ByaxError):
        JobManager(tmp_path, max_pending=0)
    manager = JobManager(tmp_path, max_workers=1, max_pending=1, max_payload_bytes=100)
    with pytest.raises(ByaxError) as error:
        manager.submit('local', {'remote': False, 'text': 'x' * 200}, lambda p, c: {})
    assert error.value.code == 'limit_exceeded'
    gate = threading.Event()
    first = manager.submit('local', {'remote': False}, lambda p, c: gate.wait(3) and {})
    with pytest.raises(ByaxError) as error:
        manager.submit('local', {'remote': False}, lambda p, c: {})
    assert error.value.code == 'limit_exceeded'
    gate.set()
    manager.wait(first['id'], 3)
    manager.close()


def _sources(tmp_path, count=3):
    paths = []
    for index in range(count):
        path = tmp_path / f'in{index}.byax'
        save_document(new_document(f'Doc {index}', f'Body {index} 文'), path)
        paths.append(path)
    return paths


def test_partial_batch_reports_every_outcome_and_staged_all_publishes_nothing(tmp_path):
    good = _sources(tmp_path, 2)
    items = [
        {'path': str(good[0]), 'out': str(tmp_path / 'out' / 'a.docx')},
        {'path': str(tmp_path / 'missing.byax'), 'out': str(tmp_path / 'out' / 'b.docx')},
        {'path': str(good[1]), 'out': str(tmp_path / 'out' / 'c.odt')},
    ]
    result = execute('batch', {'input': {'items': items}, 'staging': 'all', 'workers': 2})
    assert result['execution'] == 'failed' and result['outcome'] == 'none'
    states = [entry['state'] for entry in result['items']]
    assert states == ['not_published', 'failed', 'not_published']
    assert result['items'][1]['error']['code'] in ('not_found', 'invalid_input')
    assert not (tmp_path / 'out' / 'a.docx').exists() and not (tmp_path / 'out' / 'c.odt').exists()
    assert not [p for p in (tmp_path / 'out').iterdir()] if (tmp_path / 'out').exists() else True
    each = execute('batch', {'input': {'items': items}, 'staging': 'each'})
    assert [entry['state'] for entry in each['items']] == ['completed', 'failed', 'completed']
    assert each['items'][0]['output'] and each['items'][0]['losses'] is not None
    assert each['outcome'] == 'partial' and each['counts'] == {
        'completed': 2,
        'failed': 1,
        'cancelled': 0,
        'skipped': 0,
        'not_published': 0,
    }
    clean = execute('batch', {'input': {'items': [items[0], items[2]]}, 'staging': 'all', 'overwrite': True})
    assert clean['execution'] == 'completed' and clean['outcome'] == 'complete'
    assert (tmp_path / 'out' / 'a.docx').exists() and not [
        p for p in (tmp_path / 'out').iterdir() if p.name.startswith('.byax')
    ]


def test_batch_overwrite_memory_and_cancellation_policies(tmp_path):
    paths = _sources(tmp_path, 2)
    out = tmp_path / 'existing.txt'
    out.write_text('keep')
    refused = execute('batch', {'input': {'items': [{'path': str(paths[0]), 'out': str(out)}]}})
    assert refused['items'][0]['error']['code'] == 'conflict' and out.read_text() == 'keep'
    staged_refusal = execute(
        'batch', {'input': {'items': [{'path': str(paths[0]), 'out': str(out)}]}, 'staging': 'all'}
    )
    assert staged_refusal['items'][0]['error']['code'] == 'conflict' and out.read_text() == 'keep'
    bounded = execute(
        'batch',
        {'input': {'items': [{'path': str(paths[0]), 'out': str(tmp_path / 'm.txt')}]}, 'max_memory_mb': 1},
    )
    assert bounded['items'][0]['state'] in ('completed', 'skipped')
    big = tmp_path / 'big.txt'
    big.write_bytes(b'x' * (200 * 1024))
    skipped = execute(
        'batch',
        {'input': {'items': [{'path': str(big), 'out': str(tmp_path / 'big.md')}]}, 'max_memory_mb': 1},
    )
    assert (
        skipped['items'][0]['state'] == 'skipped' and skipped['items'][0]['error']['code'] == 'limit_exceeded'
    )
    assert skipped['policy']['max_memory_mb'] == 1
    cancel = threading.Event()
    cancel.set()
    cancelled = run_batch(
        [{'path': str(paths[0]), 'out': str(tmp_path / 'z.txt')}], lambda item: {}, cancel=cancel
    )
    assert cancelled['execution'] == 'cancelled' and cancelled['items'][0]['exit_code'] == 6
    with pytest.raises(ByaxError):
        run_batch([], lambda item: {}, staging='sometimes')


def test_cli_batch_exit_code_reflects_partial_failure(tmp_path, capsys):
    paths = _sources(tmp_path, 1)
    spec = tmp_path / 'batch.json'
    spec.write_text(
        json.dumps(
            {
                'items': [
                    {'path': str(paths[0]), 'out': str(tmp_path / 'ok.md')},
                    {'path': str(tmp_path / 'nope.byax'), 'out': str(tmp_path / 'no.md')},
                ]
            }
        )
    )
    code = main(['batch', '--input', str(spec), '--staging', 'all'])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1 and payload['outcome'] == 'none' and not os.path.exists(tmp_path / 'ok.md')
