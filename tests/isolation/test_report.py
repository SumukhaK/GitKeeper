from pathlib import Path

import pytest


HOST_PROBE_NAMES = (
    "host_file_access",
    "process_metadata",
    "environment_access",
    "outside_worktree_write",
    "network_egress",
)


def build_report(
    *,
    failed_probe: str | None = None,
    probe_names: tuple[str, ...] = HOST_PROBE_NAMES,
    model_configured: bool = True,
    opencode_succeeded: bool = True,
    evidence_paths: list[Path] | None = None,
):
    from gitkeeper.isolation.report import IsolationReport, ProbeResult

    host_probe_results = [
        ProbeResult(
            name=name,
            passed=name != failed_probe,
            failure_category=None if name != failed_probe else "boundary_violation",
        )
        for name in probe_names
    ]
    return IsolationReport(
        prototype_id="windows-prototype-v1",
        host_probe_results=host_probe_results,
        credential_probe_result=ProbeResult(
            name="credential_exposure",
            passed=failed_probe != "credential_exposure",
            failure_category=(
                None if failed_probe != "credential_exposure" else "credential_visible"
            ),
        ),
        opencode_model_configured=model_configured,
        opencode_call_succeeded=opencode_succeeded,
        evidence_paths=evidence_paths or [Path("evidence/summary.json")],
    )


def test_complete_report_returns_go(tmp_path):
    from gitkeeper.isolation.report import isolation_passes

    evidence = tmp_path / "summary.json"
    evidence.write_text("verified probe evidence", encoding="utf-8")
    report = build_report(evidence_paths=[evidence])

    assert isolation_passes(report) is True
    assert report.verdict == "GO"


@pytest.mark.parametrize(
    "failed_probe",
    [
        "host_file_access",
        "process_metadata",
        "environment_access",
        "outside_worktree_write",
        "network_egress",
        "credential_exposure",
    ],
)
def test_failed_boundary_probe_returns_no_go(failed_probe, tmp_path):
    from gitkeeper.isolation.report import isolation_passes

    evidence = tmp_path / "summary.json"
    evidence.write_text("verified probe evidence", encoding="utf-8")
    report = build_report(
        failed_probe=failed_probe,
        evidence_paths=[evidence],
    )

    assert isolation_passes(report) is False
    assert report.verdict == "NO_GO"


def test_missing_host_probe_returns_no_go(tmp_path):
    from gitkeeper.isolation.report import isolation_passes

    evidence = tmp_path / "summary.json"
    evidence.write_text("verified probe evidence", encoding="utf-8")
    report = build_report(
        probe_names=HOST_PROBE_NAMES[:-1],
        evidence_paths=[evidence],
    )

    assert isolation_passes(report) is False
    assert report.verdict == "NO_GO"


def test_unconfigured_model_cannot_return_go(tmp_path):
    from gitkeeper.isolation.report import isolation_passes

    evidence = tmp_path / "summary.json"
    evidence.write_text("verified probe evidence", encoding="utf-8")
    report = build_report(model_configured=False, evidence_paths=[evidence])

    assert isolation_passes(report) is False
    assert report.verdict == "NO_GO"


def test_failed_opencode_call_cannot_return_go(tmp_path):
    from gitkeeper.isolation.report import isolation_passes

    evidence = tmp_path / "summary.json"
    evidence.write_text("verified probe evidence", encoding="utf-8")
    report = build_report(opencode_succeeded=False, evidence_paths=[evidence])

    assert isolation_passes(report) is False
    assert report.verdict == "NO_GO"
