"""Bounded Windows process creation and fenced process cancellation.

The Windows adapter creates the child suspended, assigns it to a kill-on-close
Job Object, resumes only the main thread, drains a bounded amount of output, and
terminates the exact job on timeout or cancellation. It never uses a shell.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from ctypes import wintypes
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from gitkeeper.host_mode.command_broker import CommandRequest
from gitkeeper.policy.models import ModelLadder, classify_model_failure
from gitkeeper.queue.errors import (
    ErrorCategory,
    LocalLeaseConflict,
    ModelUnavailable,
    WorkerFailed,
)
from gitkeeper.queue.models import AttemptOutcome, Lease, OperationalError
from gitkeeper.worker.models import ProcessCompletion, WorktreeIdentity

_MAX_OUTPUT_BYTES = 65_536
_CREATE_SUSPENDED = 0x00000004
_CREATE_NO_WINDOW = 0x08000000
_TH32CS_SNAPTHREAD = 0x00000004
_THREAD_SUSPEND_RESUME = 0x0002
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001
_PROCESS_QUERY_INFORMATION = 0x0400
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_THREAD_SUSPEND_FAILED = 0xFFFFFFFF


class ProcessStartError(RuntimeError):
    """The trusted process could not be created or fenced."""


@runtime_checkable
class ProcessHandle(Protocol):
    def wait(self, timeout_seconds: float = 1800.0) -> ProcessCompletion: ...

    def terminate(self) -> bool: ...


@runtime_checkable
class ProcessPort(Protocol):
    def start(
        self,
        request: CommandRequest,
        environment: Mapping[str, str],
        *,
        cancellation: threading.Event,
        model_ladder: ModelLadder,
    ) -> ProcessHandle: ...


if sys.platform == "win32":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
else:  # pragma: no cover - the companion is Windows-only.
    _kernel32 = None


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JobBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _JobExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobBasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _ThreadEntry32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ThreadID", wintypes.DWORD),
        ("th32OwnerProcessID", wintypes.DWORD),
        ("tpBasePri", wintypes.LONG),
        ("tpDeltaPri", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
    ]


def _bind_kernel32() -> None:
    if _kernel32 is None:
        raise ProcessStartError("Windows process creation is unavailable.")
    _kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenThread.restype = wintypes.HANDLE
    _kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    _kernel32.ResumeThread.restype = wintypes.DWORD
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel32.Thread32First.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ThreadEntry32),
    ]
    _kernel32.Thread32First.restype = wintypes.BOOL
    _kernel32.Thread32Next.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ThreadEntry32),
    ]
    _kernel32.Thread32Next.restype = wintypes.BOOL


def _create_job() -> wintypes.HANDLE:
    _bind_kernel32()
    job = _kernel32.CreateJobObjectW(None, None)
    if not job:
        raise ProcessStartError("The kill-on-close Job Object could not be created.")
    information = _JobExtendedLimitInformation()
    information.BasicLimitInformation.LimitFlags = (
        _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    )
    if not _kernel32.SetInformationJobObject(
        job,
        _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(information),
        ctypes.sizeof(information),
    ):
        _kernel32.CloseHandle(job)
        raise ProcessStartError("The Job Object could not be configured.")
    return job


def _main_thread_id(pid: int) -> int:
    _bind_kernel32()
    snapshot = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
    if not snapshot:
        raise ProcessStartError("The process thread list is unavailable.")
    entry = _ThreadEntry32()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        present = _kernel32.Thread32First(snapshot, ctypes.byref(entry))
        while present:
            if entry.th32OwnerProcessID == pid:
                return int(entry.th32ThreadID)
            present = _kernel32.Thread32Next(snapshot, ctypes.byref(entry))
    finally:
        _kernel32.CloseHandle(snapshot)
    raise ProcessStartError("The suspended process main thread was not found.")


class _WindowsProcessHandle:
    def __init__(
        self,
        process: subprocess.Popen[bytes],
        job: wintypes.HANDLE,
        *,
        cancellation: threading.Event,
        model_ladder: ModelLadder,
        clock,
        max_output_bytes: int,
    ) -> None:
        self._process = process
        self._job = job
        self._cancellation = cancellation
        self._model_ladder = model_ladder
        self._clock = clock
        self._max_output_bytes = max_output_bytes
        self._output = bytearray()
        self._lock = threading.RLock()
        self._drain_thread: threading.Thread | None = None
        self._closed = False

    def _drain(self) -> None:
        stream = self._process.stdout
        if stream is None:
            return
        while True:
            chunk = stream.read(4096)
            if not chunk:
                return
            with self._lock:
                room = self._max_output_bytes - len(self._output)
                if room > 0:
                    self._output.extend(chunk[:room])

    def _start_drain(self) -> None:
        with self._lock:
            if self._drain_thread is not None:
                return
            self._drain_thread = threading.Thread(
                target=self._drain,
                name="gitkeeper-process-output",
                daemon=True,
            )
            self._drain_thread.start()

    def _join_drain(self) -> None:
        with self._lock:
            thread = self._drain_thread
        if thread is not None:
            thread.join(timeout=5.0)

    def _error(self, category: ErrorCategory) -> OperationalError:
        message = (
            str(ModelUnavailable())
            if category is ErrorCategory.model_unavailable
            else str(WorkerFailed())
        )
        return OperationalError(category, message, self._clock())

    def _close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            job = self._job
            self._job = None
        if job:
            _kernel32.CloseHandle(job)
        if self._process.stdout is not None:
            try:
                self._process.stdout.close()
            except OSError:
                pass

    def terminate(self) -> bool:
        with self._lock:
            job = self._job
        if job:
            _kernel32.TerminateJobObject(job, 1)
        else:
            self._process.kill()
        try:
            self._process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            return False
        return self._process.poll() is not None

    def wait(self, timeout_seconds: float = 1800.0) -> ProcessCompletion:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")
        self._start_drain()
        deadline = time.monotonic() + timeout_seconds
        timed_out = False
        while True:
            if self._cancellation.is_set():
                self.terminate()
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                self.terminate()
                break
            try:
                self._process.wait(timeout=min(0.1, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
        self._join_drain()
        returncode = self._process.poll()
        text = bytes(self._output).decode("utf-8", "replace")
        if self._cancellation.is_set() and not timed_out:
            outcome = AttemptOutcome.cancelled
            error = None
        elif timed_out:
            outcome = AttemptOutcome.failed
            error = self._error(ErrorCategory.worker_failed)
        elif returncode == 0:
            outcome = AttemptOutcome.completed
            error = None
        else:
            failure = classify_model_failure(
                ladder=self._model_ladder,
                model_id=self._model_ladder.active,
                stderr=text,
                exit_code=returncode,
            )
            if failure.is_model_unavailable:
                outcome = AttemptOutcome.model_unavailable
                error = self._error(ErrorCategory.model_unavailable)
            else:
                outcome = AttemptOutcome.failed
                error = self._error(ErrorCategory.worker_failed)
        self._close()
        return ProcessCompletion(
            outcome=outcome,
            exit_code=returncode,
            error=error,
        )


class WindowsProcessAdapter:
    """The fail-closed process adapter for the accepted host mode."""

    def __init__(
        self,
        clock=None,
        *,
        max_output_bytes: int = _MAX_OUTPUT_BYTES,
    ) -> None:
        if clock is None:
            clock = lambda: datetime.now(timezone.utc)
        if not callable(clock):
            raise TypeError("clock must be callable.")
        if (
            isinstance(max_output_bytes, bool)
            or not isinstance(max_output_bytes, int)
            or max_output_bytes <= 0
        ):
            raise ValueError("max_output_bytes must be a positive integer.")
        self._clock = clock
        self._max_output_bytes = max_output_bytes

    def start(
        self,
        request: CommandRequest,
        environment: Mapping[str, str],
        *,
        cancellation: threading.Event,
        model_ladder: ModelLadder,
    ) -> ProcessHandle:
        if sys.platform != "win32":
            raise ProcessStartError("Windows process creation is unavailable.")
        if not isinstance(request, CommandRequest):
            raise TypeError("request must be a CommandRequest.")
        if request.script is not None:
            raise ProcessStartError("The process adapter accepts argv requests only.")
        if not isinstance(environment, Mapping):
            raise TypeError("environment must be a mapping.")
        if not isinstance(cancellation, threading.Event):
            raise TypeError("cancellation must be a threading.Event.")
        if not isinstance(model_ladder, ModelLadder):
            raise TypeError("model_ladder must be a ModelLadder.")
        job = _create_job()
        process = None
        try:
            process = subprocess.Popen(
                [request.executable, *request.argv],
                cwd=request.cwd,
                env=dict(environment),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=_CREATE_SUSPENDED | _CREATE_NO_WINDOW,
                close_fds=True,
            )
            process_handle = _kernel32.OpenProcess(
                _PROCESS_SET_QUOTA | _PROCESS_TERMINATE | _PROCESS_QUERY_INFORMATION,
                False,
                process.pid,
            )
            if not process_handle:
                raise ProcessStartError("The suspended process handle is unavailable.")
            try:
                if not _kernel32.AssignProcessToJobObject(job, process_handle):
                    raise ProcessStartError(
                        "The process could not be assigned to the Job Object."
                    )
            finally:
                _kernel32.CloseHandle(process_handle)
            thread_id = _main_thread_id(process.pid)
            thread = _kernel32.OpenThread(
                _THREAD_SUSPEND_RESUME, False, thread_id
            )
            if not thread:
                raise ProcessStartError("The suspended process thread is unavailable.")
            try:
                if _kernel32.ResumeThread(thread) == _THREAD_SUSPEND_FAILED:
                    raise ProcessStartError("The suspended process could not resume.")
            finally:
                _kernel32.CloseHandle(thread)
        except BaseException:
            if process is not None:
                try:
                    process.kill()
                    process.wait(timeout=5.0)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            _kernel32.TerminateJobObject(job, 1)
            _kernel32.CloseHandle(job)
            raise
        return _WindowsProcessHandle(
            process,
            job,
            cancellation=cancellation,
            model_ladder=model_ladder,
            clock=self._clock,
            max_output_bytes=self._max_output_bytes,
        )


@dataclass
class _RegistryEntry:
    cancellation: threading.Event
    handle: ProcessHandle | None = None


class ProcessRegistry:
    """Fenced process lookup used by the queue cancellation port."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: dict[tuple[object, str, str], _RegistryEntry] = {}

    @staticmethod
    def _key(
        identity: WorktreeIdentity, lease_id: str, attempt_id: str
    ) -> tuple[object, str, str]:
        if not isinstance(identity, WorktreeIdentity):
            raise TypeError("identity must be a WorktreeIdentity.")
        return (identity, lease_id, attempt_id)

    def begin(
        self,
        identity: WorktreeIdentity,
        lease_id: str,
        attempt_id: str,
        cancellation: threading.Event,
    ) -> None:
        if not isinstance(cancellation, threading.Event):
            raise TypeError("cancellation must be a threading.Event.")
        key = self._key(identity, lease_id, attempt_id)
        with self._lock:
            if key in self._entries:
                raise LocalLeaseConflict()
            self._entries[key] = _RegistryEntry(cancellation=cancellation)

    def register(
        self,
        identity: WorktreeIdentity,
        lease_id: str,
        attempt_id: str,
        handle: ProcessHandle,
        cancellation: threading.Event,
    ) -> None:
        key = self._key(identity, lease_id, attempt_id)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or entry.cancellation is not cancellation:
                handle.terminate()
                return
            entry.handle = handle
            cancelled = entry.cancellation.is_set()
        if cancelled:
            handle.terminate()

    def unregister(
        self, identity: WorktreeIdentity, lease_id: str, attempt_id: str
    ) -> None:
        key = self._key(identity, lease_id, attempt_id)
        with self._lock:
            self._entries.pop(key, None)

    def cancel(self, lease: Lease) -> bool:
        if not isinstance(lease, Lease):
            raise TypeError("lease must be a Lease.")
        identity = WorktreeIdentity(
            repository=lease.key.repository,
            pr_number=lease.key.pr_number,
            head_sha=lease.key.head_sha,
            cycle_id=lease.cycle_id,
        )
        key = self._key(identity, lease.lease_id, lease.attempt_id)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return True
            entry.cancellation.set()
            handle = entry.handle
        if handle is None:
            return False
        return handle.terminate()
