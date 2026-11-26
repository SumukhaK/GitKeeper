import http.client
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

from gitkeeper.host_mode.command_broker import (
    ApprovalOutcome,
    ExecutionResult,
)
from gitkeeper.worker.broker_bridge import BrokerBridge, BrokerBridgeError
from gitkeeper.worker.models import WorktreeIdentity, WorktreeRecord

_AT = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
_SHA = "a" * 40
_REPOSITORY = "SumukhaK/Setu-mobile"
pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="The broker token uses a Windows DACL."
)


class FakeBroker:
    def __init__(
        self,
        outcome: ApprovalOutcome = ApprovalOutcome.APPROVED,
        execute_error: Exception | None = None,
    ) -> None:
        self.outcome = outcome
        self.execute_error = execute_error
        self.requests = []
        self.executed = []

    def request_approval(self, request):
        self.requests.append(request)
        return SimpleNamespace(outcome=self.outcome)

    def execute_approved(self, request, decision):
        self.executed.append(request)
        if self.execute_error is not None:
            raise self.execute_error
        return ExecutionResult(0, "approved")


def _worktree(tmp_path) -> tuple[WorktreeRecord, Path]:
    path = tmp_path / "worktree"
    path.mkdir()
    record = WorktreeRecord(
        worktree_id=str(uuid4()),
        identity=WorktreeIdentity(
            repository=_REPOSITORY,
            pr_number=42,
            head_sha=_SHA,
            cycle_id=str(uuid4()),
        ),
        path=path,
        branch=None,
        created_at=_AT,
        preserved_reason=None,
    )
    return record, path


def _bridge(tmp_path, broker=None, names=("PATH", "SystemRoot")):
    record, path = _worktree(tmp_path)
    bridge = BrokerBridge(
        worktree=record,
        broker=broker or FakeBroker(),
        token_directory=tmp_path / "token",
        environment_names=names,
    )
    endpoint = bridge.start()
    return bridge, endpoint, path


def _payload(path: Path, **overrides):
    values = {
        "request_id": str(uuid4()),
        "tool": "bash",
        "session_id": "session-1",
        "call_id": "call-1",
        "cwd": str(path),
        "command": "echo hello",
    }
    values.update(overrides)
    return values


def _connection(endpoint):
    parts = urlsplit(endpoint.url)
    return http.client.HTTPConnection(parts.hostname, parts.port, timeout=5)


def _post(
    endpoint,
    payload=None,
    *,
    token: str | None = None,
    path: str = "/command-request",
    method: str = "POST",
    content_type: str = "application/json",
    raw_body: bytes | None = None,
) -> tuple[int, bytes]:
    if token is None:
        token = endpoint.token_file.read_text(encoding="utf-8").strip()
    body = raw_body if raw_body is not None else json.dumps(payload).encode("utf-8")
    connection = _connection(endpoint)
    connection.request(
        method,
        path,
        body=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
        },
    )
    response = connection.getresponse()
    data = response.read()
    status = response.status
    connection.close()
    return status, data


def test_accepts_the_exact_plugin_request(tmp_path) -> None:
    broker = FakeBroker()
    bridge, endpoint, path = _bridge(tmp_path, broker)
    payload = _payload(path, command="python -m pytest -q")
    try:
        status, body = _post(endpoint, payload)
    finally:
        bridge.stop()

    assert status == 200
    response = json.loads(body)
    assert response == {
        "decision": "approve",
        "request_id": payload["request_id"],
    }
    assert len(broker.requests) == 1
    request = broker.requests[0]
    assert request.executable == "opencode-shell"
    assert request.script == "python -m pytest -q"
    assert request.cwd == str(path)
    assert request.repository == _REPOSITORY
    assert request.pr_number == 42
    assert request.starting_sha == _SHA
    assert request.environment_names == ("PATH", "SystemRoot")
    assert broker.executed == [request]


def test_rejects_a_missing_or_wrong_bearer(tmp_path) -> None:
    broker = FakeBroker()
    bridge, endpoint, path = _bridge(tmp_path, broker)
    try:
        status, _ = _post(endpoint, _payload(path), token="wrong")
    finally:
        bridge.stop()

    assert status == 401
    assert broker.requests == []


def test_rejects_wrong_path_and_method(tmp_path) -> None:
    bridge, endpoint, path = _bridge(tmp_path)
    try:
        wrong_path, _ = _post(endpoint, _payload(path), path="/other")
        wrong_method, _ = _post(endpoint, _payload(path), method="GET")
    finally:
        bridge.stop()

    assert wrong_path == 404
    assert wrong_method == 405


def test_rejects_unknown_duplicate_and_invalid_json(tmp_path) -> None:
    bridge, endpoint, path = _bridge(tmp_path)
    try:
        unknown, _ = _post(endpoint, {**_payload(path), "extra": "value"})
        duplicate, _ = _post(
            endpoint,
            raw_body=b'{"request_id":"a","request_id":"b"}',
        )
        invalid, _ = _post(endpoint, raw_body=b"{not-json")
    finally:
        bridge.stop()

    assert unknown == 400
    assert duplicate == 400
    assert invalid == 400


def test_rejects_wrong_cwd(tmp_path) -> None:
    bridge, endpoint, path = _bridge(tmp_path)
    try:
        status, _ = _post(endpoint, _payload(tmp_path / "other"))
    finally:
        bridge.stop()

    assert status == 403


def test_rejects_oversize_and_chunked_bodies(tmp_path) -> None:
    bridge, endpoint, path = _bridge(tmp_path)
    token = endpoint.token_file.read_text(encoding="utf-8").strip()
    try:
        connection = _connection(endpoint)
        connection.putrequest("POST", "/command-request")
        connection.putheader("Authorization", f"Bearer {token}")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", "65537")
        connection.endheaders()
        oversize = connection.getresponse()
        oversize.read()
        connection.close()

        connection = _connection(endpoint)
        connection.putrequest("POST", "/command-request")
        connection.putheader("Authorization", f"Bearer {token}")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Transfer-Encoding", "chunked")
        connection.endheaders()
        chunked = connection.getresponse()
        chunked.read()
        connection.close()
    finally:
        bridge.stop()

    assert oversize.status == 413
    assert chunked.status == 411


def test_denial_does_not_execute(tmp_path) -> None:
    broker = FakeBroker(outcome=ApprovalOutcome.DENIED)
    bridge, endpoint, path = _bridge(tmp_path, broker)
    try:
        status, _ = _post(endpoint, _payload(path))
    finally:
        bridge.stop()

    assert status == 403
    assert broker.executed == []


def test_execution_error_blocks_without_raw_text(tmp_path) -> None:
    broker = FakeBroker(execute_error=RuntimeError("secret command text"))
    bridge, endpoint, path = _bridge(tmp_path, broker)
    try:
        status, body = _post(endpoint, _payload(path))
    finally:
        bridge.stop()

    assert status == 403
    assert b"secret" not in body


def test_stop_removes_the_token_file(tmp_path) -> None:
    bridge, endpoint, _ = _bridge(tmp_path)
    assert endpoint.token_file.exists()

    bridge.stop()

    assert not endpoint.token_file.exists()


def test_endpoint_is_loopback_with_an_ephemeral_port(tmp_path) -> None:
    bridge, endpoint, _ = _bridge(tmp_path)
    try:
        parts = urlsplit(endpoint.url)
        assert parts.hostname == "127.0.0.1"
        assert parts.port and parts.port > 0
        with pytest.raises(BrokerBridgeError):
            bridge.start()
    finally:
        bridge.stop()


def test_token_directory_inside_the_worktree_is_rejected(tmp_path) -> None:
    record, path = _worktree(tmp_path)

    with pytest.raises(BrokerBridgeError):
        BrokerBridge(
            worktree=record,
            broker=FakeBroker(),
            token_directory=path / "token",
            environment_names=("PATH",),
        )
