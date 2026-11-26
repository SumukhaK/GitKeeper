"""Per-job loopback HTTP bridge for the real OpenCode command plugin.

The bridge accepts only the plugin's exact POST shape on 127.0.0.1, requires a
fresh bearer token stored in an owner-only file, routes the command through the
existing one-shot ApprovalBroker, and returns success only after the approved
handoff completes.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import stat
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from gitkeeper.host_mode.command_broker import (
    ApprovalBroker,
    ApprovalOutcome,
    CommandRequest,
)
from gitkeeper.worker.models import WorktreeRecord
from gitkeeper.worker.win_acl import (
    create_owner_only_directory,
    create_owner_only_file,
    verify_owner_only_file,
)

_REQUEST_PATH = "/command-request"
_MAX_BODY_BYTES = 65_536
_MAX_HANDLERS = 4
_FIELDS = {"request_id", "tool", "session_id", "call_id", "cwd", "command"}
_LIMITS = {
    "tool": 128,
    "session_id": 256,
    "call_id": 256,
    "cwd": 32_767,
    "command": 32_768,
}


class BrokerBridgeError(RuntimeError):
    """The bridge cannot start or secure its token."""


@dataclass(frozen=True)
class BrokerEndpoint:
    url: str
    token_file: Path


def _invalid(field: str) -> ValueError:
    return ValueError(f"Invalid {field}.")


def _uuid4(value: object) -> str:
    if not isinstance(value, str) or value != value.lower():
        raise _invalid("request_id")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        raise _invalid("request_id") from None
    if parsed.version != 4 or str(parsed) != value:
        raise _invalid("request_id")
    return value


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = path.lstat().st_file_attributes
    except (OSError, AttributeError):
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


class _BridgeHandler(BaseHTTPRequestHandler):
    server: "_BridgeServer"
    timeout = 10

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def _send(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(
            payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass

    def _reject(self, status: int, reason: str) -> None:
        self._send(status, {"error": reason})

    def do_GET(self) -> None:  # noqa: N802
        self._reject(HTTPStatus.METHOD_NOT_ALLOWED, "method")

    def do_PUT(self) -> None:  # noqa: N802
        self._reject(HTTPStatus.METHOD_NOT_ALLOWED, "method")

    def do_DELETE(self) -> None:  # noqa: N802
        self._reject(HTTPStatus.METHOD_NOT_ALLOWED, "method")

    def do_PATCH(self) -> None:  # noqa: N802
        self._reject(HTTPStatus.METHOD_NOT_ALLOWED, "method")

    def do_HEAD(self) -> None:  # noqa: N802
        self._reject(HTTPStatus.METHOD_NOT_ALLOWED, "method")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._reject(HTTPStatus.METHOD_NOT_ALLOWED, "method")

    def do_TRACE(self) -> None:  # noqa: N802
        self._reject(HTTPStatus.METHOD_NOT_ALLOWED, "method")

    def do_CONNECT(self) -> None:  # noqa: N802
        self._reject(HTTPStatus.METHOD_NOT_ALLOWED, "method")

    def do_POST(self) -> None:  # noqa: N802
        bridge = self.server.bridge
        if bridge.stopped:
            self._reject(HTTPStatus.SERVICE_UNAVAILABLE, "stopped")
            return
        try:
            target = urlsplit(self.path)
        except ValueError:
            self._reject(HTTPStatus.BAD_REQUEST, "path")
            return
        if target.path != _REQUEST_PATH or target.query:
            self._reject(HTTPStatus.NOT_FOUND, "path")
            return
        content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0]
        if content_type.strip().casefold() != "application/json":
            self._reject(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "content-type")
            return
        if self.headers.get("Transfer-Encoding"):
            self._reject(HTTPStatus.LENGTH_REQUIRED, "chunked")
            return
        length_headers = self.headers.get_all("Content-Length") or []
        if len(length_headers) != 1:
            self._reject(HTTPStatus.BAD_REQUEST, "length")
            return
        try:
            length = int(length_headers[0])
        except (TypeError, ValueError):
            self._reject(HTTPStatus.BAD_REQUEST, "length")
            return
        if length <= 0 or length > _MAX_BODY_BYTES:
            self._reject(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "size")
            return
        expected = f"Bearer {bridge.token}".encode("ascii")
        provided = (self.headers.get("Authorization") or "").encode(
            "latin-1", "replace"
        )
        if not hmac.compare_digest(provided, expected):
            self._reject(HTTPStatus.UNAUTHORIZED, "authorization")
            return
        if not bridge.acquire_handler():
            try:
                self.rfile.read(length)
            except OSError:
                pass
            self._reject(HTTPStatus.SERVICE_UNAVAILABLE, "busy")
            return
        try:
            body = self.rfile.read(length)
            if len(body) != length:
                self._reject(HTTPStatus.BAD_REQUEST, "body")
                return
            try:
                data = json.loads(body, object_pairs_hook=_json_object)
            except (ValueError, UnicodeDecodeError):
                self._reject(HTTPStatus.BAD_REQUEST, "json")
                return
            if not isinstance(data, dict) or set(data) != _FIELDS:
                self._reject(HTTPStatus.BAD_REQUEST, "fields")
                return
            for name, limit in _LIMITS.items():
                value = data.get(name)
                if not isinstance(value, str) or len(value) > limit:
                    self._reject(HTTPStatus.BAD_REQUEST, name)
                    return
            request_id = _uuid4(data["request_id"])
            if not data["tool"] or not data["command"]:
                self._reject(HTTPStatus.BAD_REQUEST, "command")
                return
            if not bridge.matches_worktree(data["cwd"]):
                self._reject(HTTPStatus.FORBIDDEN, "cwd")
                return

            request = CommandRequest(
                executable="opencode-shell",
                cwd=data["cwd"],
                repository=bridge.worktree.identity.repository,
                pr_number=bridge.worktree.identity.pr_number,
                starting_sha=bridge.worktree.identity.head_sha,
                purpose=f"OpenCode shell call {request_id}",
                script=data["command"],
                environment_names=bridge.environment_names,
            )
            decision = bridge.broker.request_approval(request)
            if decision.outcome is not ApprovalOutcome.APPROVED:
                self._reject(HTTPStatus.FORBIDDEN, "denied")
                return
            if bridge.stopped:
                self._reject(HTTPStatus.SERVICE_UNAVAILABLE, "stopped")
                return
            bridge.broker.execute_approved(request, decision)
            if bridge.stopped:
                self._reject(HTTPStatus.SERVICE_UNAVAILABLE, "stopped")
                return
            self._send(
                HTTPStatus.OK,
                {"decision": "approve", "request_id": request_id},
            )
        except Exception:  # noqa: BLE001 - every failure must block
            self._reject(HTTPStatus.FORBIDDEN, "blocked")
        finally:
            bridge.release_handler()


class _BridgeServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, bridge: "BrokerBridge") -> None:
        self.bridge = bridge
        super().__init__(("127.0.0.1", 0), _BridgeHandler)


class BrokerBridge:
    """Own one loopback listener and its per-job bearer token."""

    def __init__(
        self,
        worktree: WorktreeRecord,
        broker: ApprovalBroker,
        token_directory: Path,
        environment_names: tuple[str, ...],
    ) -> None:
        if not isinstance(worktree, WorktreeRecord):
            raise TypeError("worktree must be a WorktreeRecord.")
        if not callable(getattr(broker, "request_approval", None)) or not callable(
            getattr(broker, "execute_approved", None)
        ):
            raise TypeError("broker must provide request_approval/execute_approved.")
        if not isinstance(token_directory, Path) or not token_directory.is_absolute():
            raise BrokerBridgeError("token_directory must be an absolute path.")
        if not isinstance(environment_names, tuple) or any(
            not isinstance(name, str) or not name for name in environment_names
        ):
            raise BrokerBridgeError("environment_names must be non-empty names.")
        if token_directory.exists():
            if not token_directory.is_dir():
                raise BrokerBridgeError("token_directory must be a directory.")
            if _is_reparse_point(token_directory):
                raise BrokerBridgeError(
                    "token_directory must not be a reparse point."
                )
        try:
            if token_directory.resolve(strict=False).is_relative_to(
                worktree.path.resolve(strict=False)
            ):
                raise BrokerBridgeError(
                    "token_directory must be outside the worker worktree."
                )
        except AttributeError:  # pragma: no cover - Python < 3.9
            pass
        self.worktree = worktree
        self.broker = broker
        self.environment_names = tuple(sorted(environment_names))
        self.token = secrets.token_urlsafe(32)
        self._token_directory = token_directory
        self._token_file = token_directory / "broker-token"
        self._stop = threading.Event()
        self._semaphore = threading.BoundedSemaphore(_MAX_HANDLERS)
        self._server: _BridgeServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def acquire_handler(self) -> bool:
        return self._semaphore.acquire(blocking=False)

    def release_handler(self) -> None:
        try:
            self._semaphore.release()
        except ValueError:
            pass

    def matches_worktree(self, cwd: str) -> bool:
        try:
            candidate = Path(cwd).resolve(strict=False)
            expected = self.worktree.path.resolve(strict=False)
        except (OSError, ValueError):
            return False
        return os.path.normcase(str(candidate)) == os.path.normcase(str(expected))

    def _secure_token(self) -> None:
        try:
            if _is_reparse_point(self._token_file):
                raise BrokerBridgeError(
                    "The broker token file must not be a reparse point."
                )
            create_owner_only_directory(self._token_directory)
            create_owner_only_file(self._token_file)
            self._token_file.write_text(self.token, encoding="utf-8")
            verify_owner_only_file(self._token_file)
        except Exception:  # noqa: BLE001 - never expose a partial secret
            self._token_file.unlink(missing_ok=True)
            raise BrokerBridgeError("The broker token file could not be secured.")

    def start(self) -> BrokerEndpoint:
        if self._server is not None:
            raise BrokerBridgeError("The bridge is already started.")
        if self._stop.is_set():
            raise BrokerBridgeError("The bridge was stopped.")
        self._secure_token()
        try:
            server = _BridgeServer(self)
        except OSError:
            self._token_file.unlink(missing_ok=True)
            raise BrokerBridgeError("The loopback listener could not start.") from None
        self._server = server
        self._thread = threading.Thread(
            target=server.serve_forever,
            name="gitkeeper-broker-bridge",
            daemon=True,
        )
        self._thread.start()
        port = server.server_address[1]
        return BrokerEndpoint(
            url=f"http://127.0.0.1:{port}",
            token_file=self._token_file,
        )

    def stop(self) -> None:
        self._stop.set()
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        try:
            if server is not None:
                server.shutdown()
                server.server_close()
            if thread is not None:
                thread.join(timeout=5.0)
        finally:
            self._token_file.unlink(missing_ok=True)
