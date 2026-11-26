import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from gitkeeper.host_mode.command_broker import CommandRequest
from gitkeeper.policy.models import ModelLadder
from gitkeeper.queue.errors import LocalLeaseConflict
from gitkeeper.queue.models import AttemptOutcome, Lease, QueueKey
from gitkeeper.worker.models import WorktreeIdentity
from gitkeeper.worker.process import (
    ProcessRegistry,
    ProcessStartError,
    WindowsProcessAdapter,
)

_AT = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
_SHA = "a" * 40
_REPOSITORY = "SumukhaK/Setu-mobile"
_LADDER = ModelLadder.from_ids(
    ("opencode/deepseek-v4.1-flash-free",),
    active="opencode/deepseek-v4.1-flash-free",
)
pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows process creation is Windows-only."
)


def _environment() -> dict[str, str]:
    source = os.environ
    return {
        "PATH": source.get("PATH", ""),
        "PATHEXT": source.get("PATHEXT", ""),
        "SystemRoot": source.get("SystemRoot", ""),
        "ComSpec": source.get("ComSpec", ""),
        "TEMP": source.get("TEMP", ""),
        "TMP": source.get("TMP", ""),
        "USERPROFILE": source.get("USERPROFILE", ""),
        "LOCALAPPDATA": source.get("LOCALAPPDATA", ""),
        "APPDATA": source.get("APPDATA", ""),
    }


def _request(tmp_path, *arguments: str) -> CommandRequest:
    return CommandRequest(
        executable=sys.executable,
        cwd=str(tmp_path),
        repository=_REPOSITORY,
        pr_number=42,
        starting_sha=_SHA,
        purpose="GitKeeper process test",
        argv=arguments,
    )


def _identity() -> WorktreeIdentity:
    return WorktreeIdentity(
        repository=_REPOSITORY,
        pr_number=42,
        head_sha=_SHA,
        cycle_id=str(uuid4()),
    )


def _lease(identity: WorktreeIdentity, lease_id: str, attempt_id: str) -> Lease:
    return Lease(
        lease_id=lease_id,
        generation=1,
        attempt_id=attempt_id,
        owner="gitkeeper-automatic",
        key=QueueKey(
            identity.repository, identity.pr_number, identity.head_sha
        ),
        cycle_id=identity.cycle_id,
        retry_authorization=None,
        claimed_at=_AT,
        expires_at=_AT + timedelta(minutes=2),
        cancelled_at=None,
    )


class FakeHandle:
    def __init__(self, terminated: bool = True) -> None:
        self.terminated = terminated
        self.calls = 0

    def wait(self, timeout_seconds: float = 1800.0):
        raise AssertionError("FakeHandle.wait must not be called")

    def terminate(self) -> bool:
        self.calls += 1
        return self.terminated


def test_process_completes_and_discards_output(tmp_path) -> None:
    adapter = WindowsProcessAdapter()
    handle = adapter.start(
        _request(tmp_path, "-c", "print('x' * 100000)"),
        _environment(),
        cancellation=threading.Event(),
        model_ladder=_LADDER,
    )

    completion = handle.wait(20.0)

    assert completion.outcome is AttemptOutcome.completed
    assert completion.exit_code == 0
    assert completion.error is None


def test_quota_output_is_model_unavailable(tmp_path) -> None:
    adapter = WindowsProcessAdapter()
    handle = adapter.start(
        _request(
            tmp_path,
            "-c",
            "import sys; print('quota exceeded'); sys.exit(1)",
        ),
        _environment(),
        cancellation=threading.Event(),
        model_ladder=_LADDER,
    )

    completion = handle.wait(20.0)

    assert completion.outcome is AttemptOutcome.model_unavailable
    assert completion.error is not None
    assert completion.error.category.value == "model_unavailable"


def test_nonzero_exit_is_a_worker_failure(tmp_path) -> None:
    adapter = WindowsProcessAdapter()
    handle = adapter.start(
        _request(tmp_path, "-c", "import sys; print('boom'); sys.exit(3)"),
        _environment(),
        cancellation=threading.Event(),
        model_ladder=_LADDER,
    )

    completion = handle.wait(20.0)

    assert completion.outcome is AttemptOutcome.failed
    assert completion.exit_code == 3
    assert completion.error.category.value == "worker_failed"


def test_cancellation_terminates_the_job(tmp_path) -> None:
    adapter = WindowsProcessAdapter()
    cancellation = threading.Event()
    handle = adapter.start(
        _request(tmp_path, "-c", "import time; time.sleep(10)"),
        _environment(),
        cancellation=cancellation,
        model_ladder=_LADDER,
    )
    cancellation.set()
    started = time.monotonic()

    completion = handle.wait(20.0)

    assert completion.outcome is AttemptOutcome.cancelled
    assert time.monotonic() - started < 5.0


def test_timeout_terminates_the_job(tmp_path) -> None:
    adapter = WindowsProcessAdapter()
    handle = adapter.start(
        _request(tmp_path, "-c", "import time; time.sleep(10)"),
        _environment(),
        cancellation=threading.Event(),
        model_ladder=_LADDER,
    )

    completion = handle.wait(0.2)

    assert completion.outcome is AttemptOutcome.failed
    assert completion.error.category.value == "worker_failed"


def test_registry_cancels_a_pending_entry_then_terminates_on_register() -> None:
    registry = ProcessRegistry()
    identity = _identity()
    lease_id = str(uuid4())
    attempt_id = str(uuid4())
    cancellation = threading.Event()
    handle = FakeHandle()

    registry.begin(identity, lease_id, attempt_id, cancellation)
    assert registry.cancel(_lease(identity, lease_id, attempt_id)) is False
    assert cancellation.is_set()

    registry.register(identity, lease_id, attempt_id, handle, cancellation)
    assert handle.calls == 1

    registry.unregister(identity, lease_id, attempt_id)
    assert registry.cancel(_lease(identity, lease_id, attempt_id)) is True


def test_registry_cancels_an_active_exact_handle() -> None:
    registry = ProcessRegistry()
    identity = _identity()
    lease_id = str(uuid4())
    attempt_id = str(uuid4())
    cancellation = threading.Event()
    handle = FakeHandle(terminated=True)
    registry.begin(identity, lease_id, attempt_id, cancellation)
    registry.register(identity, lease_id, attempt_id, handle, cancellation)

    assert registry.cancel(_lease(identity, lease_id, attempt_id)) is True
    assert handle.calls == 1


def test_registry_key_mismatch_does_not_touch_another_entry() -> None:
    registry = ProcessRegistry()
    identity = _identity()
    lease_id = str(uuid4())
    attempt_id = str(uuid4())
    cancellation = threading.Event()
    registry.begin(identity, lease_id, attempt_id, cancellation)

    assert registry.cancel(_lease(identity, lease_id, str(uuid4()))) is True
    assert not cancellation.is_set()


def test_registry_rejects_a_duplicate_pending_key() -> None:
    registry = ProcessRegistry()
    identity = _identity()
    lease_id = str(uuid4())
    attempt_id = str(uuid4())
    registry.begin(identity, lease_id, attempt_id, threading.Event())

    with pytest.raises(LocalLeaseConflict):
        registry.begin(identity, lease_id, attempt_id, threading.Event())


def test_process_adapter_rejects_script_requests(tmp_path) -> None:
    request = CommandRequest(
        executable=sys.executable,
        cwd=str(tmp_path),
        repository=_REPOSITORY,
        pr_number=42,
        starting_sha=_SHA,
        purpose="GitKeeper process test",
        script="print('not launched')",
    )

    with pytest.raises(ProcessStartError):
        WindowsProcessAdapter().start(
            request,
            _environment(),
            cancellation=threading.Event(),
            model_ladder=_LADDER,
        )
