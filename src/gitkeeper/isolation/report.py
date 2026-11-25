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
WINDOWS_ISOLATION_PROTOTYPE_ID = "windows-prototype-v1"


@dataclass(frozen=True)
class ProbeResult:
    name: str
    passed: bool
    failure_category: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("probe name must not be empty")
        if type(self.passed) is not bool:
            raise TypeError("probe passed must be a bool")
        if self.failure_category is not None and (
            not isinstance(self.failure_category, str)
            or not self.failure_category.strip()
        ):
            raise ValueError("probe failure category must be non-empty text")
        if self.passed and self.failure_category is not None:
            raise ValueError("passing probe cannot have a failure category")
        if not self.passed and self.failure_category is None:
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
        if not isinstance(self.prototype_id, str) or not self.prototype_id.strip():
            raise ValueError("prototype_id must not be empty")
        if not isinstance(self.host_probe_results, list) or not all(
            isinstance(probe, ProbeResult) for probe in self.host_probe_results
        ):
            raise TypeError("host_probe_results must contain ProbeResult values")
        if not isinstance(self.credential_probe_result, ProbeResult):
            raise TypeError("credential_probe_result must be a ProbeResult")
        if not isinstance(self.evidence_paths, list):
            raise TypeError("evidence_paths must be a list of paths")
        if type(self.opencode_model_configured) is not bool:
            raise TypeError("opencode_model_configured must be a bool")
        if type(self.opencode_call_succeeded) is not bool:
            raise TypeError("opencode_call_succeeded must be a bool")

    @property
    def verdict(self) -> Literal["GO", "NO_GO"]:
        return "GO" if isolation_passes(self) else "NO_GO"


def isolation_passes(report: IsolationReport) -> bool:
    if not isinstance(report, IsolationReport):
        return False
    if not isinstance(report.host_probe_results, list) or not all(
        isinstance(probe, ProbeResult) for probe in report.host_probe_results
    ):
        return False
    if not isinstance(report.credential_probe_result, ProbeResult):
        return False
    if not isinstance(report.evidence_paths, list):
        return False
    probe_names = [probe.name for probe in report.host_probe_results]
    if len(probe_names) != len(set(probe_names)):
        return False
    if report.credential_probe_result.name != "credential_exposure":
        return False
    if not REQUIRED_HOST_PROBES.issubset(probe_names):
        return False
    if not all(probe.passed for probe in report.host_probe_results):
        return False
    if not report.credential_probe_result.passed:
        return False
    if not report.opencode_model_configured or not report.opencode_call_succeeded:
        return False
    if not report.evidence_paths or not all(
        _is_evidence_file(path) for path in report.evidence_paths
    ):
        return False
    return True


def _is_evidence_file(path: Path) -> bool:
    if not isinstance(path, Path):
        return False
    try:
        return not path.is_symlink() and path.is_file()
    except OSError:
        return False


class IsolationGateError(RuntimeError):
    pass


def require_verified(
    report: IsolationReport, expected_prototype_id: str
) -> None:
    if (
        not isinstance(expected_prototype_id, str)
        or not expected_prototype_id.strip()
    ):
        raise IsolationGateError("an expected isolation prototype ID is required")
    if not isinstance(report, IsolationReport):
        raise IsolationGateError("a typed isolation report is required")
    if (
        not isinstance(report.prototype_id, str)
        or report.prototype_id != expected_prototype_id
    ):
        raise IsolationGateError("isolation report prototype ID does not match")
    if not isolation_passes(report):
        raise IsolationGateError("isolation report is incomplete or failed")
