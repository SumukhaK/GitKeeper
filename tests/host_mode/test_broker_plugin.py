"""Behavioural tests for the OpenCode command-broker plugin.

The plugin is the only thing standing between a model and a shell on the host, so
it is tested against a real HTTP server and real `fetch` rather than mocks of the
plugin's own internals. Node runs the plugin; pytest owns the broker and asserts
what the plugin did.

Every failure shape must block. Only an explicit approval, for the request the
plugin itself issued, lets a command run.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = (
    REPO_ROOT
    / "src"
    / "gitkeeper"
    / "host_mode"
    / "broker_plugin"
    / "gitkeeper-command-broker.mjs"
)
TOKEN = "probe-token-3d9f1c"

NODE = shutil.which("node")
if NODE is None:  # pragma: no cover - environment guard
    raise RuntimeError(
        "the broker plugin is JavaScript; install Node.js to run its tests"
    )


RUNNER_SOURCE = """
import { pathToFileURL } from "node:url";

const [spec, rawConfig] = process.argv.slice(2);
const config = JSON.parse(rawConfig);

const module_ = await import(pathToFileURL(spec).href);
const hooks = await module_.default({
  directory: config.directory ?? "C:/gitkeeper/worktrees/pr-1",
});

let resolved = false;
let error = null;
try {
  await hooks["tool.execute.before"](
    { tool: config.tool, sessionID: "ses_probe", callID: "call_probe" },
    { args: config.args },
  );
  resolved = true;
} catch (caught) {
  error = { name: caught.name, message: String(caught.message) };
}

process.stdout.write(JSON.stringify({ resolved, error }));
"""


@pytest.fixture(scope="module")
def runner(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("broker-runner") / "runner.mjs"
    path.write_text(RUNNER_SOURCE, encoding="utf-8")
    return path


class _Recorder:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.verdict = "approve"
        self.status = 200
        self.delay = 0.0
        self.mangle: str | None = None


class _StandInBroker:
    """A minimal stand-in for the companion's approval endpoint."""

    def __init__(self) -> None:
        self.state = _Recorder()
        state = self.state
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler(state))
        self.port = self._server.server_address[1]
        self._thread = None

    @staticmethod
    def _handler(state: _Recorder):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def do_POST(self):
                length = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(length).decode("utf-8")
                if self.headers.get("authorization") != f"Bearer {TOKEN}":
                    self.send_response(401)
                    self.end_headers()
                    return
                if self.path != "/command-request":
                    self.send_response(404)
                    self.end_headers()
                    return
                body = json.loads(raw)
                state.requests.append(
                    {"path": self.path, "authorization": self.headers["authorization"], **body}
                )
                if state.delay:
                    time.sleep(state.delay)
                if state.status != 200:
                    self.send_response(state.status)
                    self.send_header("content-length", "0")
                    self.end_headers()
                    return
                if state.mangle == "bad-json":
                    payload = "<<<not json>>>".encode("utf-8")
                else:
                    request_id = body["request_id"]
                    if state.mangle == "mismatch":
                        request_id = f"{request_id}-someone-else"
                    payload = json.dumps(
                        {"request_id": request_id, "decision": state.verdict}
                    ).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        return Handler

    def start(self) -> "_StandInBroker":
        import threading

        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


@pytest.fixture
def broker():
    server = _StandInBroker().start()
    try:
        yield server
    finally:
        server.stop()


def run_plugin(
    runner: Path,
    tmp_path: Path,
    *,
    tool: str = "bash",
    args=None,
    broker_url: str | None = None,
    token: str | None = TOKEN,
    directory: str = "C:/gitkeeper/worktrees/pr-1",
    timeout_ms: int = 5000,
) -> dict:
    token_file = tmp_path / "token.txt"
    token_file.write_text(token or "", encoding="utf-8")
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GITKEEPER_BROKER_")
    }
    env["GITKEEPER_BROKER_TIMEOUT_MS"] = str(timeout_ms)
    if broker_url is not None:
        env["GITKEEPER_BROKER_URL"] = broker_url
    if token is not None:
        env["GITKEEPER_BROKER_TOKEN_FILE"] = str(token_file)
    if token == "missing-file":
        env["GITKEEPER_BROKER_TOKEN_FILE"] = str(tmp_path / "absent-token.txt")
    elif token == "empty-file":
        token_file.write_text("   \n", encoding="utf-8")
    completed = subprocess.run(
        [
            NODE,
            str(runner),
            str(PLUGIN_PATH),
            json.dumps(
                {
                    "tool": tool,
                    "args": args if args is not None else {"command": "gradlew test"},
                    "directory": directory,
                }
            ),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def assert_blocked(result: dict, fragment: str) -> None:
    assert result["resolved"] is False, "the plugin let the call through"
    assert result["error"] is not None
    assert fragment in result["error"]["message"]
    assert "GitKeeper blocked this tool call" in result["error"]["message"]


def test_an_approved_request_lets_the_command_run(runner: Path, tmp_path: Path, broker):
    result = run_plugin(runner, tmp_path, broker_url=broker.url)

    assert result["resolved"] is True
    assert result["error"] is None
    assert len(broker.state.requests) == 1


def test_the_request_carries_the_command_and_nothing_secret(
    runner: Path, tmp_path: Path, broker
):
    run_plugin(
        runner,
        tmp_path,
        broker_url=broker.url,
        args={"command": "./gradlew testDebugUnitTest"},
    )

    sent = broker.state.requests[0]
    assert sent["tool"] == "bash"
    assert sent["command"] == "./gradlew testDebugUnitTest", (
        "the owner must see the command that will run; an empty or different "
        "command would let a model get approval for something else"
    )
    assert sent["cwd"] == "C:/gitkeeper/worktrees/pr-1"
    assert sent["session_id"] == "ses_probe"
    assert sent["call_id"] == "call_probe"
    assert sent["request_id"]
    assert TOKEN not in json.dumps({k: v for k, v in sent.items() if k != "authorization"})


def test_a_denied_request_stops_the_command(runner: Path, tmp_path: Path, broker):
    broker.state.verdict = "deny"

    result = run_plugin(runner, tmp_path, broker_url=broker.url)

    assert_blocked(result, "deny")
    assert len(broker.state.requests) == 1


def test_an_approval_for_another_request_does_not_count(
    runner: Path, tmp_path: Path, broker
):
    broker.state.mangle = "mismatch"

    result = run_plugin(runner, tmp_path, broker_url=broker.url)

    assert_blocked(result, "different request")


def test_an_unreadable_reply_blocks(runner: Path, tmp_path: Path, broker):
    broker.state.mangle = "bad-json"

    result = run_plugin(runner, tmp_path, broker_url=broker.url)

    assert_blocked(result, "not readable")


def test_an_error_status_blocks(runner: Path, tmp_path: Path, broker):
    broker.state.status = 503

    result = run_plugin(runner, tmp_path, broker_url=broker.url)

    assert_blocked(result, "rejected the request")


def test_a_wrong_token_is_refused_by_the_companion(runner: Path, tmp_path: Path, broker):
    result = run_plugin(runner, tmp_path, broker_url=broker.url, token="not-the-token")

    assert_blocked(result, "rejected the request")
    assert broker.state.requests == []


def test_an_unreachable_companion_blocks(runner: Path, tmp_path: Path):
    result = run_plugin(runner, tmp_path, broker_url="http://127.0.0.1:9")

    assert_blocked(result, "did not answer")


def test_a_timed_out_companion_blocks(runner: Path, tmp_path: Path, broker):
    broker.state.delay = 2.0
    started = time.monotonic()

    result = run_plugin(runner, tmp_path, broker_url=broker.url, timeout_ms=300)
    elapsed = time.monotonic() - started

    assert_blocked(result, "did not answer")
    assert elapsed < 2.0, "the plugin waited for the full broker reply instead of timing out"
    assert len(broker.state.requests) == 1


def test_an_unconfigured_broker_blocks_without_calling_out(
    runner: Path, tmp_path: Path
):
    result = run_plugin(runner, tmp_path, broker_url=None)

    assert_blocked(result, "not configured")


def test_a_missing_token_file_blocks(runner: Path, tmp_path: Path, broker):
    result = run_plugin(runner, tmp_path, broker_url=broker.url, token="missing-file")

    assert_blocked(result, "unreadable")


def test_an_empty_token_file_blocks(runner: Path, tmp_path: Path, broker):
    result = run_plugin(runner, tmp_path, broker_url=broker.url, token="empty-file")

    assert_blocked(result, "empty")


def test_an_unconfigured_token_file_blocks(runner: Path, tmp_path: Path, broker):
    result = run_plugin(runner, tmp_path, broker_url=broker.url, token=None)

    assert_blocked(result, "not configured")


def test_a_non_shell_tool_is_not_mediated(runner: Path, tmp_path: Path, broker):
    result = run_plugin(
        runner,
        tmp_path,
        broker_url=broker.url,
        tool="read",
        args={"filePath": "README.md"},
    )

    assert result["resolved"] is True
    assert broker.state.requests == []


def test_an_unknown_tool_carrying_a_command_is_mediated(
    runner: Path, tmp_path: Path, broker
):
    broker.state.verdict = "deny"

    result = run_plugin(
        runner,
        tmp_path,
        broker_url=broker.url,
        tool="sh2",
        args={"command": "rm -rf build"},
    )

    assert_blocked(result, "deny")
    assert broker.state.requests[0]["command"] == "rm -rf build"


def test_a_shell_tool_named_differently_is_still_mediated(
    runner: Path, tmp_path: Path, broker
):
    broker.state.verdict = "deny"

    result = run_plugin(
        runner,
        tmp_path,
        broker_url=broker.url,
        tool="powershell.exec",
        args={"script": "Get-ChildItem"},
    )

    assert_blocked(result, "deny")


def test_a_shell_call_with_no_command_text_blocks_without_asking(
    runner: Path, tmp_path: Path, broker
):
    result = run_plugin(
        runner, tmp_path, broker_url=broker.url, args={"description": "list files"}
    )

    assert result["resolved"] is False, (
        "an approval with no command text would authorise an unseen command"
    )
    assert broker.state.requests == []


def test_the_plugin_declares_the_hook_opencode_awaits(runner: Path, tmp_path: Path):
    result = run_plugin(runner, tmp_path, broker_url="http://127.0.0.1:9")

    assert "tool.execute.before" in PLUGIN_PATH.read_text(encoding="utf-8")
    assert result["resolved"] is False
