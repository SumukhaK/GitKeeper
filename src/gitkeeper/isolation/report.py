from dataclasses import dataclass
from pathlib import Path
from typing import Literal


REQUIRED_HOST_PROBES = frozenset(
    {
        "host_file_access",
        "process_metadata",
        "environment_access",
        "outside_worktree_write",
        "network_egress",
    }
)


@dataclass(frozen=True)
class ProbeResult:
    name: str
    passed: bool
    failure_category: str | None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("probe name must not be empty")
        if type(self.passed) is not bool:
            raise TypeError("probe passed must be a bool")
        if self.passed and self.failure_category is not None:
            raise ValueError("passing probe cannot have a failure category")
        if not self.passed and not self.failure_category:
            raise ValueError("failed probe requires a failure category")


@dataclass(frozen=True)
class IsolationReport:
    prototype_id: str
    host_probe_results: list[ProbeResult]
    credential_probe_result: ProbeResult
    opencode_model_configured: bool
    opencode_call_succeeded: bool
    evidence_paths: list[Path]

    def __post_init__(self) -> None:
        if not self.prototype_id.strip():
            raise ValueError("prototype_id must not be empty")
        if type(self.opencode_model_configured) is not bool:
            raise TypeError("opencode_model_configured must be a bool")
        if type(self.opencode_call_succeeded) is not bool:
            raise TypeError("opencode_call_succeeded must be a bool")

    @property
    def verdict(self) -> Literal["GO", "NO_GO"]:
        return "GO" if isolation_passes(self) else "NO_GO"


def isolation_passes(report: IsolationReport) -> bool:
    probe_names = [probe.name for probe in report.host_probe_results]
    if len(probe_names) != len(set(probe_names)):
        return False
    if not REQUIRED_HOST_PROBES.issubset(probe_names):
        return False
    if not all(probe.passed for probe in report.host_probe_results):
        return False
    if not report.credential_probe_result.passed:
        return False
    if not report.opencode_model_configured or not report.opencode_call_succeeded:
        return False
    if not report.evidence_paths:
        return False
    return True
