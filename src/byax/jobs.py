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

_TERMINAL = {"completed", "failed", "cancelled", "uncertain"}
_SECRET_FIELDS = {"api_key", "secret", "password", "access_token", "authorization", "credential", "credentials"}


def _validate_payload(value):
    if isinstance(value, dict):
        if any(str(key).lower() in _SECRET_FIELDS for key in value):
            raise ByaxError("invalid_input", "Jobs must contain credential references, never secret values")
        for child in value.values():
            _validate_payload(child)
    elif isinstance(value, list):
        for child in value:
            _validate_payload(child)


def _alive(pid):
    if type(pid) is not int or pid <= 0:
        return False
    if os.name == "nt":
        # os.kill(pid, 0) is not a portable Windows liveness probe: Windows
        # implements non-console signals using TerminateProcess.
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
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
    def __init__(self, root=None, *, max_workers=2):
        if not 1 <= max_workers <= 16:
            raise ByaxError("invalid_input", "Worker count must be between 1 and 16")
        if root is None:
            from platformdirs import user_state_path
            root = user_state_path("ByAuthorX", appauthor=False) / "jobs"
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="byax-job")
        self._lock = threading.RLock()
        self._events = {}
        self._futures = {}

    def _path(self, job_id):
        if not isinstance(job_id, str) or not job_id.startswith("job_") or len(job_id) != 36:
            raise ByaxError("invalid_input", "Invalid job identifier")
        try:
            int(job_id[4:], 16)
        except ValueError as exc:
            raise ByaxError("invalid_input", "Invalid job identifier") from exc
        return self.root / (job_id + ".json")

    def _read(self, job_id):
        try:
            value = json.loads(self._path(job_id).read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ByaxError("invalid_input", "Job not found", target=job_id) from exc
        except (OSError, ValueError) as exc:
            raise ByaxError("invalid_input", "Job state is unreadable", target=job_id) from exc
        return value

    def _write(self, job):
        job["updated_at"] = time.time()
        _atomic_json(self._path(job["id"]), job)

    def submit(self, kind, payload, worker):
        if not isinstance(payload, dict) or not callable(worker):
            raise ByaxError("invalid_input", "A JSON payload and worker are required")
        _validate_payload(payload)
        try:
            frozen = json.loads(json.dumps(payload, allow_nan=False))
        except (ValueError, TypeError) as exc:
            raise ByaxError("invalid_input", "Job payload must be JSON serializable") from exc
        if len(json.dumps(frozen)) > 16_000_000:
            raise ByaxError("limit_exceeded", "Job payload exceeds 16 MB")
        job_id = "job_" + uuid.uuid4().hex
        job = {"id": job_id, "kind": kind, "state": "queued", "progress": 0,
               "created_at": time.time(), "owner_pid": os.getpid(), "payload": frozen,
               "input_revision": frozen.get("revision", frozen.get("expected_revision")),
               "profile_version": frozen.get("profile_version"),
               "source_versions": frozen.get("source_versions", {}),
               "remote": bool(frozen.get("remote", kind in {"review", "transcription", "provider"})),
               "remote_id": None, "result": None, "error": None, "cancel_requested": False}
        with self._lock:
            self._write(job)
            event = _PersistentCancelEvent(lambda: bool(self._read(job_id).get("cancel_requested")))
            self._events[job_id] = event
            self._futures[job_id] = self._executor.submit(self._run, job_id, frozen, worker, event)
        return copy.deepcopy(job)

    def _run(self, job_id, payload, worker, event):
        with self._lock:
            job = self._read(job_id)
            if event.is_set():
                job["state"] = "cancelled"
                self._write(job)
                return
            job["state"] = "running"
            self._write(job)
        try:
            result = worker(copy.deepcopy(payload), event)
            _validate_payload(result)
            # Freeze worker return before publishing durable completion.
            result = json.loads(json.dumps(result, allow_nan=False))
            with self._lock:
                job = self._read(job_id)
                job["result"] = result
                if isinstance(result, dict):
                    job["remote_id"] = result.get("request_id", result.get("remote_id"))
                job["state"] = ("uncertain" if job["remote"] else "cancelled") if event.is_set() else "completed"
                job["progress"] = 1 if job["state"] == "completed" else job["progress"]
                self._write(job)
        except Exception as exc:  # noqa: BLE001 - persist every worker failure without leaking its message
            with self._lock:
                job = self._read(job_id)
                code = getattr(exc, "code", "worker_failure")
                job["state"] = "uncertain" if code == "uncertain_external_outcome" or (event.is_set() and job["remote"]) else (
                    "cancelled" if event.is_set() or code == "cancelled" else "failed")
                # Never persist exception text from an HTTP client or external process.
                job["error"] = {"code": code, "message": "Job ended with an error; inspect configuration or recorded remote status before retrying"}
                self._write(job)
        finally:
            with self._lock:
                self._events.pop(job_id, None)

    def status(self, job_id, *, current_revision=None):
        with self._lock:
            job = self._read(job_id)
            if job["state"] in {"running", "queued"} and not _alive(job.get("owner_pid")):
                job["state"] = "uncertain"
                job["error"] = {"code": "uncertain_external_outcome", "message": "Worker interrupted; no request has been resubmitted"}
                self._write(job)
            result = {key: copy.deepcopy(value) for key, value in job.items() if key not in {"payload", "result"}}
            result["stale"] = bool(current_revision is not None and job["input_revision"] is not None and current_revision != job["input_revision"])
            return result

    def result(self, job_id, *, current_revision=None):
        status = self.status(job_id, current_revision=current_revision)
        job = self._read(job_id)
        return {**status, "result": job["result"]}

    def list(self):
        return [self.status(path.stem) for path in sorted(self.root.glob("job_*.json"))]

    def cancel(self, job_id):
        with self._lock:
            job = self._read(job_id)
            if job["state"] in _TERMINAL:
                return self.status(job_id)
            job["cancel_requested"] = True
            event = self._events.get(job_id)
            if event:
                event.set()
            # Queued work is known not to have sent anything. Running remote work
            # remains uncertain even when its local worker cooperates with cancel.
            job["state"] = "cancelled" if job["state"] == "queued" or not job["remote"] else "uncertain"
            self._write(job)
            return self.status(job_id)

    def progress(self, job_id, fraction, *, remote_id=None):
        if not 0 <= fraction <= 1:
            raise ByaxError("invalid_input", "Progress must be between zero and one")
        with self._lock:
            job = self._read(job_id)
            if job["state"] not in _TERMINAL:
                job["progress"] = fraction
                if remote_id:
                    job["remote_id"] = str(remote_id)
                self._write(job)
        return self.status(job_id)

    def recover(self, job_id, *, query_remote=None):
        """Query a recorded request if the caller supplies a supported lookup.

        Never invokes the original worker. Our synchronous provider adapters do
        not promise a remote lookup endpoint and therefore leave uncertainty explicit.
        """
        with self._lock:
            job = self._read(job_id)
            if job["state"] in {"running", "queued"} and job_id in self._events:
                return self.result(job_id)
            if job["state"] in {"running", "queued"}:
                if _alive(job.get("owner_pid")):
                    return self.result(job_id)
                job["state"] = "uncertain"
                self._write(job)
            if job["state"] != "uncertain" or not job.get("remote_id") or query_remote is None:
                return {**self.result(job_id), "recovery": "No request resubmitted; remote lookup unavailable or unnecessary"}
        recovered = query_remote(job["remote_id"])
        if not isinstance(recovered, dict) or recovered.get("state") not in _TERMINAL:
            raise ByaxError("invalid_input", "Remote recovery must return a terminal state and result")
        _validate_payload(recovered)
        with self._lock:
            job = self._read(job_id)
            job["state"] = recovered["state"]
            job["result"] = recovered.get("result")
            job["error"] = recovered.get("error")
            self._write(job)
        return self.result(job_id)

    def wait(self, job_id, timeout=None):
        future = self._futures.get(job_id)
        if future is not None:
            future.result(timeout=timeout)
        return self.result(job_id)

    def close(self, *, wait=True):
        self._executor.shutdown(wait=wait)
