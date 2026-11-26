import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from gitkeeper.host_mode.command_broker import ApprovalOutcome, ExecutionResult
from gitkeeper.worker.broker_bridge import BrokerBridge
from gitkeeper.worker.models import WorktreeIdentity, WorktreeRecord

_AT = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
_SHA = "a" * 40
_REPOSITORY = "SumukhaK/Setu-mobile"
_PLUGIN = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "gitkeeper"
    / "host_mode"
    / "broker_plugin"
    / "gitkeeper-command-broker.mjs"
)
pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or shutil.which("node") is None,
    reason="The bridge integration needs Windows and Node.",
)


class FakeBroker:
    def __init__(self, outcome: ApprovalOutcome = ApprovalOutcome.APPROVED) -> None:
        self.outcome = outcome
        self.requests = []
        self.executed = []

    def request_approval(self, request):
        self.requests.append(request)
        return SimpleNamespace(outcome=self.outcome)

    def execute_approved(self, request, decision):
        self.executed.append(request)
        return ExecutionResult(0, "approved")


def _worktree(tmp_path) -> tuple[WorktreeRecord, Path]:
    path = tmp_path / "worktree"
    path.mkdir()
    return (
        WorktreeRecord(
            worktree_id=str(uuid4()),
            identity=WorktreeIdentity(_REPOSITORY, 42, _SHA, str(uuid4())),
            path=path,
            branch=None,
            created_at=_AT,
            preserved_reason=None,
        ),
        path,
    )


def _environment(endpoint, worktree: Path) -> dict[str, str]:
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
        "GITKEEPER_BROKER_URL": endpoint.url,
        "GITKEEPER_BROKER_TOKEN_FILE": str(endpoint.token_file),
        "GITKEEPER_BROKER_TIMEOUT_MS": "5000",
        "TEST_WORKTREE": str(worktree),
    }


def _run_plugin(tmp_path, endpoint, worktree: Path) -> subprocess.CompletedProcess[str]:
    runner = tmp_path / "runner.mjs"
    runner.write_text(
        "\n".join(
            (
                f'import {{ createBrokerHooks }} from {json.dumps(_PLUGIN.as_uri())};',
                "const hooks = createBrokerHooks({",
                "  directory: process.env.TEST_WORKTREE,",
                "  env: process.env,",
                "});",
                "try {",
                '  await hooks["tool.execute.before"](',
                '    { tool: "bash", sessionID: "session-1", callID: "call-1" },',
                '    { args: { command: "echo hello" } },',
                "  );",
                "  process.exit(0);",
                "} catch (error) {",
                "  process.stderr.write(String(error));",
                "  process.exit(2);",
                "}",
            )
        ),
        encoding="utf-8",
    )
    return subprocess.run(
        ["node", str(runner)],
        env=_environment(endpoint, worktree),
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_real_node_plugin_is_approved_by_the_bridge(tmp_path) -> None:
    record, worktree = _worktree(tmp_path)
    broker = FakeBroker()
    bridge = BrokerBridge(
        worktree=record,
        broker=broker,
        token_directory=tmp_path / "token",
        environment_names=("PATH", "SystemRoot"),
    )
    endpoint = bridge.start()
    try:
        result = _run_plugin(tmp_path, endpoint, worktree)
    finally:
        bridge.stop()

    assert result.returncode == 0, result.stderr
    assert len(broker.requests) == 1
    assert broker.requests[0].script == "echo hello"
    assert broker.executed == broker.requests


def test_real_node_plugin_is_blocked_by_a_denial(tmp_path) -> None:
    record, worktree = _worktree(tmp_path)
    broker = FakeBroker(outcome=ApprovalOutcome.DENIED)
    bridge = BrokerBridge(
        worktree=record,
        broker=broker,
        token_directory=tmp_path / "token",
        environment_names=("PATH", "SystemRoot"),
    )
    endpoint = bridge.start()
    try:
        result = _run_plugin(tmp_path, endpoint, worktree)
    finally:
        bridge.stop()

    assert result.returncode == 2
    assert "blocked" in result.stderr.casefold()
    assert broker.executed == []
