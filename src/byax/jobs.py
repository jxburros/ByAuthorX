"""Durable bounded background work, without automatic external resubmission."""

from __future__ import annotations

import copy
import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .errors import ByaxError
from .providers import _atomic_json

_TERMINAL = {'completed', 'failed', 'cancelled', 'uncertain'}
# Allowed durable transitions. Uncertain work may only be resolved by a recorded
# remote lookup; other terminal states are final.
TRANSITIONS = {
    'queued': {'running', 'cancelled', 'uncertain', 'failed'},
    'running': {'completed', 'failed', 'cancelled', 'uncertain'},
    'uncertain': {'completed', 'failed', 'cancelled'},
    'completed': set(),
    'failed': set(),
    'cancelled': set(),
}


def _transition(job, state, reason):
    if state == job['state']:
        return
    if state not in TRANSITIONS.get(job['state'], set()):
        raise ByaxError('conflict', f'Job cannot move from {job["state"]} to {state}', target=job['id'])
    job.setdefault('transitions', []).append(
        {'from': job['state'], 'to': state, 'at': time.time(), 'reason': reason}
    )
    job['state'] = state


_SECRET_FIELDS = {
    'api_key',
    'secret',
    'password',
    'access_token',
    'authorization',
    'credential',
    'credentials',
}


def _validate_payload(value):
    if isinstance(value, dict):
        if any(str(key).lower() in _SECRET_FIELDS for key in value):
            raise ByaxError('invalid_input', 'Jobs must contain credential references, never secret values')
        for child in value.values():
            _validate_payload(child)
    elif isinstance(value, list):
        for child in value:
            _validate_payload(child)


def _alive(pid):
    if type(pid) is not int or pid <= 0:
        return False
    if os.name == 'nt':
        # os.kill(pid, 0) is not a portable Windows liveness probe: Windows
        # implements non-console signals using TerminateProcess.
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # Access denied does not prove process is gone.
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except (OSError, TypeError):
        return False


class _PersistentCancelEvent(threading.Event):
    def __init__(self, check):
        super().__init__()
        self._check = check

    def is_set(self):
        return super().is_set() or self._check()


class JobManager:
    def __init__(
        self, root=None, *, max_workers=2, max_pending=64, max_payload_bytes=16_000_000, remote_lookups=None
    ):
        if not 1 <= max_workers <= 16:
            raise ByaxError('invalid_input', 'Worker count must be between 1 and 16')
        if not 1 <= max_pending <= 10_000 or not 1 <= max_payload_bytes <= 64_000_000:
            raise ByaxError(
                'invalid_input', 'Pending-job limit must be 1-10000 and payload limit 1 byte to 64 MB'
            )
        self.limits = {
            'max_workers': max_workers,
            'max_pending': max_pending,
            'max_payload_bytes': max_payload_bytes,
        }
        self._lookups = dict(remote_lookups or {})
        if root is None:
            from platformdirs import user_state_path

            root = user_state_path('ByAuthorX', appauthor=False) / 'jobs'
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='byax-job')
        self._lock = threading.RLock()
        self._events = {}
        self._futures = {}

    def _path(self, job_id):
        if not isinstance(job_id, str) or not job_id.startswith('job_') or len(job_id) != 36:
            raise ByaxError('invalid_input', 'Invalid job identifier')
        try:
            int(job_id[4:], 16)
        except ValueError as exc:
            raise ByaxError('invalid_input', 'Invalid job identifier') from exc
        return self.root / (job_id + '.json')

    def _read(self, job_id):
        try:
            value = json.loads(self._path(job_id).read_text(encoding='utf-8'))
        except FileNotFoundError as exc:
            raise ByaxError('invalid_input', 'Job not found', target=job_id) from exc
        except (OSError, ValueError) as exc:
            raise ByaxError('invalid_input', 'Job state is unreadable', target=job_id) from exc
        return value

    def _write(self, job):
        job['updated_at'] = time.time()
        _atomic_json(self._path(job['id']), job)

    def submit(self, kind, payload, worker):
        if not isinstance(payload, dict) or not callable(worker):
            raise ByaxError('invalid_input', 'A JSON payload and worker are required')
        _validate_payload(payload)
        try:
            frozen = json.loads(json.dumps(payload, allow_nan=False))
        except (ValueError, TypeError) as exc:
            raise ByaxError('invalid_input', 'Job payload must be JSON serializable') from exc
        if len(json.dumps(frozen)) > self.limits['max_payload_bytes']:
            raise ByaxError(
                'limit_exceeded',
                'Job payload exceeds the configured size limit',
                limit=self.limits['max_payload_bytes'],
            )
        with self._lock:
            pending = sum(1 for future in self._futures.values() if not future.done())
        if pending >= self.limits['max_pending']:
            raise ByaxError(
                'limit_exceeded',
                'Too many unfinished jobs; wait or cancel before submitting more',
                limit=self.limits['max_pending'],
            )
        job_id = 'job_' + uuid.uuid4().hex
        job = {
            'id': job_id,
            'kind': kind,
            'state': 'queued',
            'progress': 0,
            'created_at': time.time(),
            'owner_pid': os.getpid(),
            'payload': frozen,
            'input_revision': frozen.get('revision', frozen.get('expected_revision')),
            'profile_version': frozen.get('profile_version'),
            'source_versions': frozen.get('source_versions', {}),
            'remote': bool(frozen.get('remote', kind in {'review', 'transcription', 'provider'})),
            'remote_id': None,
            'result': None,
            'error': None,
            'cancel_requested': False,
            'cancellation': None,
            'late_result': False,
            'transitions': [],
        }
        with self._lock:
            self._write(job)
            event = _PersistentCancelEvent(lambda: bool(self._read(job_id).get('cancel_requested')))
            self._events[job_id] = event
            self._futures[job_id] = self._executor.submit(self._run, job_id, frozen, worker, event)
        return copy.deepcopy(job)

    def _run(self, job_id, payload, worker, event):
        with self._lock:
            job = self._read(job_id)
            if event.is_set() or job['state'] != 'queued':
                if job['state'] == 'queued':
                    _transition(job, 'cancelled', 'cancelled before start')
                self._write(job)
                return
            _transition(job, 'running', 'worker started')
            self._write(job)
        try:
            result = worker(copy.deepcopy(payload), event)
            _validate_payload(result)
            # Freeze worker return before publishing durable completion.
            result = json.loads(json.dumps(result, allow_nan=False))
            with self._lock:
                job = self._read(job_id)
                job['result'] = result
                if isinstance(result, dict):
                    job['remote_id'] = result.get('request_id', result.get('remote_id')) or job.get(
                        'remote_id'
                    )
                if event.is_set() or job['state'] != 'running':
                    # A result that arrives after cancellation is retained but never treated as current.
                    job['late_result'] = True
                    if job['state'] == 'running':
                        _transition(
                            job, 'uncertain' if job['remote'] else 'cancelled', 'result after cancellation'
                        )
                else:
                    _transition(job, 'completed', 'worker returned')
                    job['progress'] = 1
                self._write(job)
        except Exception as exc:  # noqa: BLE001 - persist every worker failure without leaking its message
            with self._lock:
                job = self._read(job_id)
                code = getattr(exc, 'code', 'worker_failure')
                if job['state'] == 'running':
                    _transition(
                        job,
                        'uncertain'
                        if code == 'uncertain_external_outcome' or (event.is_set() and job['remote'])
                        else ('cancelled' if event.is_set() or code == 'cancelled' else 'failed'),
                        'worker raised ' + str(code),
                    )
                # Never persist exception text from an HTTP client or external process.
                job['error'] = {
                    'code': code,
                    'message': 'Job ended with an error; inspect configuration or recorded remote status before retrying',
                }
                self._write(job)
        finally:
            with self._lock:
                self._events.pop(job_id, None)

    def status(self, job_id, *, current_revision=None, current_profile_version=None, current_sources=None):
        with self._lock:
            job = self._read(job_id)
            if job['state'] in {'running', 'queued'} and not _alive(job.get('owner_pid')):
                if job['state'] == 'queued' and not job['remote']:
                    _transition(job, 'cancelled', 'owner process ended before start')
                else:
                    _transition(job, 'uncertain', 'owner process ended')
                job['error'] = {
                    'code': 'uncertain_external_outcome',
                    'message': 'Worker interrupted; no request has been resubmitted',
                }
                self._write(job)
            result = {
                key: copy.deepcopy(value) for key, value in job.items() if key not in {'payload', 'result'}
            }
            reasons = []
            if (
                current_revision is not None
                and job['input_revision'] is not None
                and current_revision != job['input_revision']
            ):
                reasons.append('revision')
            if current_profile_version is not None and current_profile_version != job.get('profile_version'):
                reasons.append('profile')
            if current_sources is not None:
                recorded = job.get('source_versions') or {}
                reasons.extend(
                    'source:' + key
                    for key in sorted(set(recorded) | set(current_sources))
                    if recorded.get(key) != current_sources.get(key)
                )
            if job.get('late_result'):
                reasons.append('late_result')
            result['stale'] = bool(reasons)
            result['stale_reasons'] = reasons
            return result

    def result(self, job_id, *, current_revision=None, **current):
        status = self.status(job_id, current_revision=current_revision, **current)
        job = self._read(job_id)
        return {**status, 'result': job['result']}

    def list(self):
        return [self.status(path.stem) for path in sorted(self.root.glob('job_*.json'))]

    def cancel(self, job_id):
        with self._lock:
            job = self._read(job_id)
            if job['state'] in _TERMINAL:
                return self.status(job_id)
            job['cancel_requested'] = True
            event = self._events.get(job_id)
            if event:
                event.set()
            # Queued work is known not to have sent anything. Running remote work
            # remains uncertain even when its local worker cooperates with cancel.
            remote = (
                'not_applicable'
                if not job['remote']
                else 'not_sent'
                if job['state'] == 'queued'
                else 'unknown'
            )
            job['cancellation'] = {'local': 'acknowledged', 'acknowledged_at': time.time(), 'remote': remote}
            _transition(
                job,
                'cancelled' if job['state'] == 'queued' or not job['remote'] else 'uncertain',
                'cancel requested',
            )
            self._write(job)
            return self.status(job_id)

    def progress(self, job_id, fraction, *, remote_id=None):
        if not 0 <= fraction <= 1:
            raise ByaxError('invalid_input', 'Progress must be between zero and one')
        with self._lock:
            job = self._read(job_id)
            if job['state'] not in _TERMINAL:
                job['progress'] = fraction
                if remote_id:
                    job['remote_id'] = str(remote_id)
                self._write(job)
        return self.status(job_id)

    def recover(self, job_id, *, query_remote=None):
        """Query a recorded request if the caller supplies a supported lookup.

        Never invokes the original worker. Our synchronous provider adapters do
        not promise a remote lookup endpoint and therefore leave uncertainty explicit.
        """
        query_remote = query_remote or self._lookups.get(None) or None
        with self._lock:
            job = self._read(job_id)
            query_remote = query_remote or self._lookups.get(job['kind'])
            if job['state'] in {'running', 'queued'} and job_id in self._events:
                return self.result(job_id)
            if job['state'] in {'running', 'queued'}:
                if _alive(job.get('owner_pid')):
                    return self.result(job_id)
                _transition(job, 'uncertain', 'recovery found no live owner')
                self._write(job)
            if job['state'] != 'uncertain' or not job.get('remote_id') or query_remote is None:
                return {
                    **self.result(job_id),
                    'recovery': 'No request resubmitted; remote lookup unavailable or unnecessary',
                }
        recovered = query_remote(job['remote_id'])
        if not isinstance(recovered, dict) or recovered.get('state') not in _TERMINAL:
            raise ByaxError('invalid_input', 'Remote recovery must return a terminal state and result')
        _validate_payload(recovered)
        with self._lock:
            job = self._read(job_id)
            if job['state'] == 'uncertain':
                _transition(job, recovered['state'], 'remote lookup')
                job['result'] = recovered.get('result')
                job['error'] = recovered.get('error')
                if job.get('cancellation'):
                    job['cancellation']['remote'] = (
                        'stopped' if recovered['state'] == 'cancelled' else 'continued'
                    )
                self._write(job)
        return {**self.result(job_id), 'recovery': 'Recorded remote request queried; nothing was resubmitted'}

    def retry(self, job_id, worker, *, confirm_duplicate_risk=False):
        """Submit a new job from a finished job's frozen payload under an explicit policy.

        Uncertain remote work may already have run (and been billed); it is never
        resubmitted unless the caller explicitly accepts the duplicate risk.
        """
        job = self._read(job_id)
        if job['state'] in ('queued', 'running'):
            raise ByaxError('conflict', 'Job is still active', target=job_id)
        if job['state'] == 'completed':
            raise ByaxError(
                'conflict', 'Completed jobs are not retried; reuse the recorded result', target=job_id
            )
        if job['state'] == 'uncertain' and job['remote'] and not confirm_duplicate_risk:
            raise ByaxError(
                'review_required',
                'The remote request may still have run; query it with recover or explicitly accept duplicate risk',
                target=job_id,
                remote_id=job.get('remote_id'),
            )
        payload = dict(job['payload'], retry_of=job_id)
        return self.submit(job['kind'], payload, worker)

    def wait(self, job_id, timeout=None):
        future = self._futures.get(job_id)
        if future is not None:
            future.result(timeout=timeout)
        return self.result(job_id)

    def close(self, *, wait=True):
        self._executor.shutdown(wait=wait)


BATCH_LIMITS = {'max_items': 1000, 'max_workers': 16, 'max_memory_mb': 4096, 'memory_factor': 16}


def run_batch(
    items,
    convert,
    *,
    workers=1,
    overwrite=False,
    max_memory_mb=1024,
    staging='each',
    cancel=None,
    describe_error=None,
):
    """Run independent conversions with explicit worker, memory, overwrite and staging policies.

    ``convert(item)`` performs one conversion and returns a result envelope. Every input
    receives an outcome (completed, failed, cancelled, skipped or not_published).
    ``staging='all'`` writes every output to a private staging area and publishes only
    when all inputs succeeded, so a partial batch never leaves completed-looking files.
    """
    import shutil
    from concurrent.futures import ThreadPoolExecutor

    if not isinstance(items, list):
        raise ByaxError('invalid_input', 'Batch input must contain an items array')
    if len(items) > BATCH_LIMITS['max_items'] or not 1 <= workers <= BATCH_LIMITS['max_workers']:
        raise ByaxError('limit_exceeded', 'Batch limit is 1000 inputs and 1-16 workers', limits=BATCH_LIMITS)
    if not 1 <= max_memory_mb <= BATCH_LIMITS['max_memory_mb']:
        raise ByaxError('invalid_input', 'max_memory_mb must be between 1 and 4096', field='max_memory_mb')
    if staging not in ('each', 'all'):
        raise ByaxError('invalid_input', 'Staging must be each or all', field='staging')
    destinations = [
        str(Path(item['out']).resolve()) for item in items if isinstance(item, dict) and 'out' in item
    ]
    if len(set(destinations)) != len(destinations):
        raise ByaxError('invalid_input', 'Batch outputs must be distinct')
    budget = max_memory_mb * 1024 * 1024
    estimates = []
    for item in items:
        try:
            size = Path(item['path']).stat().st_size if isinstance(item, dict) and item.get('path') else 0
        except OSError:
            size = 0
        estimates.append(size * BATCH_LIMITS['memory_factor'])
    largest = max(estimates, default=0)
    # Bound concurrency so the estimated working sets of simultaneous items fit the budget.
    effective = max(1, min(workers, budget // largest if largest else workers))
    staging_root = ('.byax-batch-' + uuid.uuid4().hex) if staging == 'all' else None
    created = set()

    def stage_path(index, out):
        # Stage beside the destination so publication is an atomic same-filesystem rename.
        directory = Path(out).resolve().parent / staging_root
        return directory / f'{index:04d}-{Path(out).name}'

    lock = threading.Lock()

    def outcome(index, item, state, *, result=None, error=None, code=0, output=None):
        return {
            'index': index,
            'input': item.get('path') if isinstance(item, dict) else None,
            'output': output,
            'state': state,
            'exit_code': code,
            'losses': (result or {}).get('losses', []),
            'findings': (result or {}).get('findings', []),
            'validation': (result or {}).get('validation'),
            'result': result,
            'error': error,
        }

    def run(index):
        item = items[index]
        if cancel is not None and cancel.is_set():
            return outcome(
                index, item, 'cancelled', code=6, error={'code': 'cancelled', 'message': 'Batch cancelled'}
            )
        if not isinstance(item, dict):
            return outcome(
                index,
                item,
                'failed',
                code=2,
                error={'code': 'invalid_input', 'message': 'Every batch item must be an object'},
            )
        if estimates[index] > budget:
            return outcome(
                index,
                item,
                'skipped',
                code=1,
                error={
                    'code': 'limit_exceeded',
                    'message': 'Estimated memory for this input exceeds the batch budget',
                    'limit_mb': max_memory_mb,
                    'estimated_mb': round(estimates[index] / 1048576, 1),
                },
            )
        arguments = dict(item, overwrite=item.get('overwrite', overwrite))
        if staging_root is not None and item.get('out'):
            if Path(item['out']).exists() and not arguments['overwrite']:
                return outcome(
                    index,
                    item,
                    'failed',
                    code=3,
                    error={
                        'code': 'conflict',
                        'message': 'Output already exists; explicitly select overwrite',
                    },
                )
            stage = stage_path(index, item['out'])
            with lock:
                stage.parent.mkdir(parents=True, exist_ok=True)
                created.add(stage.parent)
            arguments.update(out=str(stage), overwrite=True)
        try:
            result = convert(arguments)
        except Exception as exc:  # noqa: BLE001 - every input keeps a structured outcome
            data, code = (
                describe_error(exc)
                if describe_error
                else ({'code': getattr(exc, 'code', 'execution_failure'), 'message': str(exc)}, 1)
            )
            return outcome(
                index,
                item,
                'cancelled' if code == 6 else 'failed',
                code=code,
                error=data.get('error', data),
                result=None,
            )
        failed = result.get('execution') != 'completed' or result.get('validation') == 'failed'
        return outcome(
            index,
            item,
            'failed' if failed else 'completed',
            result=result,
            code=4 if failed else 0,
            output=item.get('out') if staging_root is None else None,
        )

    try:
        with ThreadPoolExecutor(max_workers=effective) as executor:
            results = list(executor.map(run, range(len(items))))
        if staging_root is not None:
            complete = all(entry['state'] == 'completed' for entry in results)
            from .exchange import publish_file

            for entry in results:
                item = items[entry['index']]
                if entry['state'] != 'completed':
                    continue
                if not complete:
                    entry.update(
                        state='not_published',
                        exit_code=1,
                        error={
                            'code': 'failed_requirement',
                            'message': 'Staged output withheld because another batch input failed',
                        },
                    )
                    continue
                stage = stage_path(entry['index'], item['out'])
                try:
                    Path(item['out']).parent.mkdir(parents=True, exist_ok=True)
                    publish_file(stage, item['out'], overwrite=item.get('overwrite', overwrite))
                    entry['output'] = item['out']
                    if isinstance(entry['result'], dict):
                        entry['result']['outputs'] = [str(Path(item['out']).resolve())]
                except ByaxError as exc:
                    entry.update(state='failed', exit_code=exc.exit_code, error=exc.to_dict())
    finally:
        for directory in created:
            shutil.rmtree(directory, ignore_errors=True)
    counts = {
        state: sum(1 for entry in results if entry['state'] == state)
        for state in ('completed', 'failed', 'cancelled', 'skipped', 'not_published')
    }
    unfinished = len(items) - counts['completed']
    return {
        'items': results,
        'total': len(items),
        'failed': unfinished,
        'counts': counts,
        'outcome': 'complete' if not unfinished else 'partial' if counts['completed'] else 'none',
        'execution': 'completed'
        if not unfinished
        else 'cancelled'
        if counts['cancelled'] == unfinished
        else 'failed',
        'policy': {
            'workers': workers,
            'effective_workers': effective,
            'max_memory_mb': max_memory_mb,
            'overwrite': overwrite,
            'staging': staging,
        },
    }
