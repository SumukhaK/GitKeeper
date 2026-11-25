"""Whether the installed OpenCode CLI can actually mediate every shell request.

Task 3 of the host-mode readiness plan asks whether OpenCode `1.19.6` can be
made to route every shell request through the companion approval broker. The
answer must be machine-checkable, never a documentation claim, so this module
inspects facts instead of trusting intent:

1. the exact CLI version on the host;
2. the *resolved* CLI configuration shown by `opencode debug config` in a
   controlled directory: `permission.bash` must be `ask`, the broker plugin must
   be the only plugin list entry, and no MCP server may be configured, because an
   MCP server command would spawn processes outside the broker;
3. a durable record that a live probe already proved the hook pauses a request,
   invalidates an approval when the request changes, and prevents execution on
   denial, without finding a direct shell bypass.

Anything missing is a named gap, and any gap means the readiness gate keeps
refusing to launch a worker. There is no fallback and no partial credit. In
particular a prompt instruction is not mediation, and a plugin that is absent
from the resolved configuration proves nothing about the run that matters.

`OPENCODE_DISABLE_PROJECT_CONFIG=1` is what makes this enforceable: with it set,
OpenCode ignores the project `opencode.json` inside the untrusted PR worktree,
so a pull request cannot inject a plugin or widen its own shell permission.
`OPENCODE_CONFIG` then points at the launcher-owned controlled configuration.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from gitkeeper.host_mode.readiness import (
    EXPECTED_CLI_VERSION,
    CliCapability,
    HostModePolicyError,
)

#: The plugin spec the launcher writes into the controlled configuration and the
#: exact value the resolved configuration must contain.
BROKER_PLUGIN_SPEC = "gitkeeper-command-broker"

#: The only shell permission that makes the companion the decider.
SHELL_PERMISSION = "ask"

#: The OpenCode plugin hook that runs before every tool call, including `bash`.
SUPPORTED_HOOK = "plugin.tool.execute.before"

#: Flags that would move the decision out of the companion's hands.
FORBIDDEN_LAUNCH_FLAGS = ("--auto", "--pure")

#: Environment overrides that could re-enable a bypass, so the launcher clears them.
FORBIDDEN_LAUNCH_VARIABLES = ("OPENCODE_PURE", "OPENCODE_PERMISSION")

CONFIG_DIRECTORY_NAME = "GitKeeper"
WORKTREE_DIRECTORY_NAME = "worktrees"

#: Directories the CLI scans for plugins outside the project-config guard.
PROJECT_PLUGIN_DIRECTORY_NAMES = (".opencode",)

_PROBE_FIELDS = (
    "cli_version",
    "hook",
    "approval_paused_the_command",
    "approval_change_invalidated_the_request",
    "denial_prevented_execution",
    "direct_shell_bypass_found",
    "probe_directory",
)


class ProbeRecordError(HostModePolicyError):
    """Capability evidence is malformed or self-contradictory."""


class ControlledConfigError(HostModePolicyError):
    """A launch configuration or argument would weaken command mediation."""


class CapabilityGap(str, Enum):
    """Named reasons command mediation is not proven on this host."""

    CLI_MISSING = "cli-missing"
    CLI_VERSION_MISMATCH = "cli-version-mismatch"
    SHELL_PERMISSION_NOT_ASK = "shell-permission-not-ask"
    BROKER_PLUGIN_MISSING = "broker-plugin-missing"
    MCP_SERVERS_CONFIGURED = "mcp-servers-configured"
    UNEXPECTED_PLUGINS = "unexpected-plugins"
    LIVE_PROBE_MISSING = "live-probe-missing"
    LIVE_PROBE_VERSION_MISMATCH = "live-probe-version-mismatch"
    LIVE_PROBE_HOOK_UNSUPPORTED = "live-probe-hook-unsupported"
    LIVE_PROBE_INCOMPLETE = "live-probe-incomplete"
    DIRECT_SHELL_BYPASS_FOUND = "direct-shell-bypass-found"


@dataclass(frozen=True)
class ResolvedCliConfig:
    """The `opencode debug config` fields command mediation depends on.

    Nothing else from the resolved configuration is retained, so provider keys
    and credentials can never end up in a telemetry record or a log line.
    """

    bash_permission: str | None
    plugins: tuple[str, ...] = ()
    mcp_servers: tuple[str, ...] = ()
    model: str | None = None

    def __post_init__(self) -> None:
        if self.bash_permission is not None and not isinstance(
            self.bash_permission, str
        ):
            raise ProbeRecordError("bash_permission must be a string or None.")
        _require_str_tuple(self.plugins, "plugins")
        _require_str_tuple(self.mcp_servers, "mcp_servers")
        if self.model is not None and not isinstance(self.model, str):
            raise ProbeRecordError("model must be a string or None.")


def _require_str_tuple(values: object, field_name: str) -> tuple[str, ...]:
    if isinstance(values, str) or not isinstance(values, Iterable):
        raise ProbeRecordError(f"{field_name} must be a collection of strings.")
    items = tuple(values)
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise ProbeRecordError(f"{field_name} must contain non-empty strings.")
    return items


@dataclass(frozen=True)
class LiveProbeRecord:
    """Evidence that a live probe exercised the hook on this host.

    `verified` is the probe harness's own claim. The gate never trusts it: it
    re-derives the verdict from the individual proofs, so a record cannot be
    upgraded by setting a flag.
    """

    cli_version: str
    hook: str
    approval_paused_the_command: bool
    approval_change_invalidated_the_request: bool
    denial_prevented_execution: bool
    direct_shell_bypass_found: bool
    probe_directory: str
    verified: bool = False

    def __post_init__(self) -> None:
        for name in (
            "approval_paused_the_command",
            "approval_change_invalidated_the_request",
            "denial_prevented_execution",
            "direct_shell_bypass_found",
            "verified",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ProbeRecordError(f"{name} must be a boolean.")
        if not isinstance(self.cli_version, str) or not self.cli_version.strip():
            raise ProbeRecordError("cli_version must be a version string.")
        if not isinstance(self.hook, str) or not self.hook.strip():
            raise ProbeRecordError("hook must name the hook that was probed.")
        if not isinstance(self.probe_directory, str) or not self.probe_directory.strip():
            raise ProbeRecordError("probe_directory must be a directory path.")
        if self.verified and not self.proofs_hold():
            raise ProbeRecordError(
                "A probe record cannot claim verification while a proof is "
                "missing or a bypass was found."
            )

    def proofs_hold(self) -> bool:
        return (
            self.approval_paused_the_command
            and self.approval_change_invalidated_the_request
            and self.denial_prevented_execution
            and not self.direct_shell_bypass_found
        )

    def as_mapping(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in _PROBE_FIELDS + ("verified",)}


@dataclass(frozen=True)
class OpenCodeCapability:
    """The full verdict plus every reason it is not verified."""

    cli_version: str | None
    resolved: ResolvedCliConfig | None
    probe: LiveProbeRecord | None
    gaps: tuple[CapabilityGap, ...] = field(default=())

    @property
    def is_verified(self) -> bool:
        return not self.gaps

    def to_cli_capability(self) -> CliCapability:
        """Convert to the readiness gate's capability type."""

        if self.is_verified:
            return CliCapability(cli_version=self.cli_version, broker_verified=True)
        return CliCapability(
            cli_version=self.cli_version,
            broker_verified=False,
            unsupported_reason="OpenCode command mediation is not proven: "
            + ", ".join(gap.value for gap in self.gaps),
        )


def inspect_resolved_config(runner: Callable[[], str]) -> ResolvedCliConfig:
    """Parse `opencode debug config` output into the fields the gate checks.

    The runner is injected so the gate can be tested without a CLI, and so a
    caller can decide where the CLI is allowed to run.
    """

    raw = runner()
    if not isinstance(raw, str) or not raw.strip():
        raise ProbeRecordError("The CLI did not return resolved configuration.")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ProbeRecordError(f"Resolved configuration is not JSON: {error}") from error
    if not isinstance(payload, Mapping):
        raise ProbeRecordError("Resolved configuration must be a JSON object.")

    permission = payload.get("permission")
    if isinstance(permission, Mapping):
        bash_permission = permission.get("bash")
    elif isinstance(permission, str):
        bash_permission = permission
    elif permission is None:
        bash_permission = None
    else:
        raise ProbeRecordError("permission must be an object, string, or absent.")

    plugins = payload.get("plugin")
    if plugins is None:
        plugins_tuple: tuple[str, ...] = ()
    elif isinstance(plugins, str):
        plugins_tuple = (plugins,)
    elif isinstance(plugins, Sequence):
        plugins_tuple = tuple(plugins)
    else:
        raise ProbeRecordError("plugin must be a string, list, or absent.")

    mcp = payload.get("mcp")
    if mcp is None:
        mcp_servers: tuple[str, ...] = ()
    elif isinstance(mcp, Mapping):
        mcp_servers = tuple(sorted(mcp))
    else:
        raise ProbeRecordError("mcp must be an object or absent.")

    model = payload.get("model")
    return ResolvedCliConfig(
        bash_permission=bash_permission,
        plugins=plugins_tuple,
        mcp_servers=mcp_servers,
        model=model if isinstance(model, str) else None,
    )


def is_broker_plugin(spec: str) -> bool:
    """Whether a resolved plugin entry is the GitKeeper broker plugin.

    `opencode debug config` reports an absolute plugin as a `file://` URL ending
    in the module name, while a bare package spec is reported as typed, so both
    have to be recognised or the gate would reject a correct host.
    """

    if not isinstance(spec, str):
        return False
    entry = spec.strip()
    if entry.startswith("file://"):
        entry = entry[len("file://") :]
    entry = entry.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    name = entry.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    for suffix in (".mjs", ".cjs", ".js", ".ts"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name == BROKER_PLUGIN_SPEC


def evaluate_capability(
    cli_version: str | None,
    resolved: ResolvedCliConfig | None,
    probe: LiveProbeRecord | None,
) -> OpenCodeCapability:
    """Return the capability verdict and gaps in a stable, documented order."""

    gaps: list[CapabilityGap] = []
    if cli_version is None:
        gaps.append(CapabilityGap.CLI_MISSING)
    elif cli_version != EXPECTED_CLI_VERSION:
        gaps.append(CapabilityGap.CLI_VERSION_MISMATCH)

    if resolved is None:
        gaps.extend(
            (
                CapabilityGap.SHELL_PERMISSION_NOT_ASK,
                CapabilityGap.BROKER_PLUGIN_MISSING,
            )
        )
    else:
        if resolved.bash_permission != SHELL_PERMISSION:
            gaps.append(CapabilityGap.SHELL_PERMISSION_NOT_ASK)
        if not any(is_broker_plugin(spec) for spec in resolved.plugins):
            gaps.append(CapabilityGap.BROKER_PLUGIN_MISSING)
        elif not all(is_broker_plugin(spec) for spec in resolved.plugins):
            gaps.append(CapabilityGap.UNEXPECTED_PLUGINS)
        if resolved.mcp_servers:
            gaps.append(CapabilityGap.MCP_SERVERS_CONFIGURED)

    if probe is None:
        gaps.append(CapabilityGap.LIVE_PROBE_MISSING)
    else:
        if probe.cli_version != cli_version:
            gaps.append(CapabilityGap.LIVE_PROBE_VERSION_MISMATCH)
        if probe.hook != SUPPORTED_HOOK:
            gaps.append(CapabilityGap.LIVE_PROBE_HOOK_UNSUPPORTED)
        if not probe.proofs_hold():
            if probe.direct_shell_bypass_found:
                gaps.append(CapabilityGap.DIRECT_SHELL_BYPASS_FOUND)
            else:
                gaps.append(CapabilityGap.LIVE_PROBE_INCOMPLETE)

    return OpenCodeCapability(
        cli_version=cli_version, resolved=resolved, probe=probe, gaps=tuple(gaps)
    )


def _require_controlled_config(controlled_config: object) -> str:
    if not isinstance(controlled_config, str) or not controlled_config.strip():
        raise ControlledConfigError("A controlled configuration path is required.")
    if not Path(controlled_config).is_absolute():
        raise ControlledConfigError(
            "The controlled configuration must be an absolute path."
        )
    path = os.path.abspath(controlled_config)
    parts = Path(path).parts
    if CONFIG_DIRECTORY_NAME not in parts:
        raise ControlledConfigError(
            "The controlled configuration must live in the GitKeeper local state "
            "directory, not next to a repository or worktree."
        )
    if WORKTREE_DIRECTORY_NAME in parts:
        raise ControlledConfigError(
            "The controlled configuration must never live inside a worktree."
        )
    return path


def build_launch_environment(
    controlled_config: str, *, base: Mapping[str, str] | None = None
) -> dict[str, str]:
    """The environment a mediated launch must use, and nothing weaker."""

    path = _require_controlled_config(controlled_config)
    environment = dict(os.environ if base is None else base)
    for name in FORBIDDEN_LAUNCH_VARIABLES:
        environment.pop(name, None)
    environment["OPENCODE_CONFIG"] = path
    environment["OPENCODE_DISABLE_PROJECT_CONFIG"] = "1"
    return environment


def validate_launch_arguments(arguments: Sequence[str]) -> tuple[str, ...]:
    """Refuse a launch that would auto-approve or skip plugins."""

    if isinstance(arguments, str) or not isinstance(arguments, Iterable):
        raise ControlledConfigError("Launch arguments must be a sequence.")
    found = tuple(arguments)
    for argument in found:
        if not isinstance(argument, str) or not argument:
            raise ControlledConfigError("Launch arguments must be non-empty strings.")
        for flag in FORBIDDEN_LAUNCH_FLAGS:
            if argument == flag or argument.startswith(f"{flag}="):
                raise ControlledConfigError(
                    f"{flag} moves the decision away from the companion broker."
                )
    return found


def require_inert_worktree(worktree: str | Path) -> Path:
    """Refuse a worktree whose contents could be loaded as CLI plugins.

    `OPENCODE_DISABLE_PROJECT_CONFIG=1` stops the project `opencode.json`, but the
    CLI scans a `.opencode` directory and auto-loads `.opencode/plugin/*.{ts,js}`
    outside that guard. A pull request is untrusted, so a worktree that ships one
    would add code to the mediating process itself; the launcher removes it or
    refuses to start instead of trusting the resolved configuration to save us.
    """

    candidate = Path(worktree)
    if not candidate.is_dir():
        raise ControlledConfigError(
            f"The worktree to launch in does not exist: {candidate}"
        )
    for name in PROJECT_PLUGIN_DIRECTORY_NAMES:
        if (candidate / name).exists():
            raise ControlledConfigError(
                f"The worktree contains {name}, which the CLI loads outside the "
                "project-config guard; remove it before launching a worker."
            )
    return candidate.resolve()


def save_probe_record(path: str | Path, record: LiveProbeRecord) -> None:
    """Persist probe evidence outside both repositories."""

    if not isinstance(record, LiveProbeRecord):
        raise ProbeRecordError("Only a LiveProbeRecord can be saved.")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(record.as_mapping(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_probe_record(path: str | Path) -> LiveProbeRecord | None:
    """Load evidence, treating absence or damage as "not proven"."""

    target = Path(path)
    if not target.is_file():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, Mapping):
        return None
    if any(name not in payload for name in _PROBE_FIELDS):
        return None
    try:
        return LiveProbeRecord(
            cli_version=payload["cli_version"],
            hook=payload["hook"],
            approval_paused_the_command=payload["approval_paused_the_command"],
            approval_change_invalidated_the_request=payload[
                "approval_change_invalidated_the_request"
            ],
            denial_prevented_execution=payload["denial_prevented_execution"],
            direct_shell_bypass_found=payload["direct_shell_bypass_found"],
            probe_directory=payload["probe_directory"],
            verified=payload.get("verified", False),
        )
    except ProbeRecordError:
        return None
